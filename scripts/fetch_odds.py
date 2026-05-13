"""
fetch_odds.py — Polymarket → Turso pipeline
=============================================
Fetches top 5 football markets from Polymarket Gamma API,
runs signal detection rules, writes results to Turso Cloud DB.

Designed to run inside GitHub Actions every 5 minutes.

Usage:
    export TURSO_DATABASE_URL="libsql://..."
    export TURSO_AUTH_TOKEN="..."
    python3 scripts/fetch_odds.py
"""

import asyncio
import aiohttp
import json
import os
import sys
from datetime import datetime, timezone

import libsql_client

# ---- Config ----
_raw_url = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_URL = _raw_url.replace("libsql://", "https://")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")
GAMMA_API = "https://gamma-api.polymarket.com"

FOOTBALL_KW = [
    "epl", "premier league", "la liga", "serie a", "bundesliga",
    "ligue 1", "champions league", "ucl", "mls", "world cup",
    "football", "soccer", "arsenal", "chelsea", "liverpool",
    "man city", "man united", "barcelona", "real madrid", "bayern",
    "juventus", "inter", "psg", "dortmund", "napoli", "tottenham",
    "atletico", "sevilla", "roma", "ac milan", "benfica", "porto",
]


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")


def is_football(m):
    text = " ".join([
        m.get("question", ""),
        m.get("description", ""),
        m.get("groupItemTitle", ""),
        " ".join(m.get("tags", [])) if isinstance(m.get("tags"), list) else "",
    ]).lower()
    return any(kw in text for kw in FOOTBALL_KW)


def detect_league(m):
    text = " ".join([
        m.get("question", ""),
        m.get("description", ""),
        m.get("groupItemTitle", ""),
    ]).lower()
    for kw, lg in [
        ("premier league", "EPL"), ("epl", "EPL"),
        ("la liga", "La Liga"), ("serie a", "Serie A"),
        ("bundesliga", "Bundesliga"), ("ligue 1", "Ligue 1"),
        ("champions league", "UCL"), ("ucl", "UCL"),
        ("europa league", "UEL"), ("mls", "MLS"),
        ("world cup", "World Cup"),
    ]:
        if kw in text:
            return lg
    return "Football"


def extract_teams(title):
    for sep in [" vs ", " vs. ", " v ", " - "]:
        if sep in title:
            parts = title.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return title, ""


def run_signal_rules(client, market_id):
    """Run 4 signal rules by comparing first and latest snapshots."""
    result = client.execute(
        "SELECT id, home_odds, away_odds, handicap_line "
        "FROM odds_snapshots WHERE market_id = ? ORDER BY snapshot_at ASC",
        [market_id]
    )
    rows = result.rows
    if len(rows) < 2:
        return

    first, last = rows[0], rows[-1]
    oh = float(first[1] or 0)
    ch = float(last[1] or 0)
    oa = float(first[2] or 0)
    ca = float(last[2] or 0)

    # R1: Sustained Drift
    r1, r1d = 0, ""
    hd = oh - ch
    ad = oa - ca
    if hd >= 0.15 and 1.80 <= ch <= 1.95:
        r1, r1d = 1, f"Home drift {hd:.2f} ({oh:.2f}→{ch:.2f}), close in sweet zone"
    elif ad >= 0.15 and 1.80 <= ca <= 1.95:
        r1, r1d = 1, f"Away drift {ad:.2f} ({oa:.2f}→{ca:.2f}), close in sweet zone"

    # R2: Line Rejection
    r2, r2d = 0, ""
    handicaps = [float(r[3]) for r in rows if r[3] is not None]
    if len(handicaps) >= 2 and handicaps[0] != handicaps[-1]:
        max_depth = max(handicaps, key=abs)
        if abs(handicaps[-1]) < abs(max_depth):
            r2, r2d = 1, f"Line rejected: deepened to {max_depth} then returned to {handicaps[-1]}"

    # R3: Opening Overreaction
    r3, r3d = 0, ""
    if oh >= 2.0 and ch <= 1.90:
        r3, r3d = 1, f"Home overreaction corrected: {oh:.2f}→{ch:.2f}"
    elif oa >= 2.0 and ca <= 1.90:
        r3, r3d = 1, f"Away overreaction corrected: {oa:.2f}→{ca:.2f}"

    # R4: Counter-trend
    r4, r4d = 0, ""
    if len(handicaps) >= 2 and handicaps[0] < 0:
        if abs(handicaps[-1]) < abs(handicaps[0]):
            r4, r4d = 1, f"Counter-trend: line weakened {handicaps[0]}→{handicaps[-1]}"

    score = r1 + r2 + r3 + r4
    conf = "STRONG" if score >= 2 else ("LEAN" if score == 1 else "NO_BET")
    side = "none"
    if r1 and "Home" in r1d:
        side = "home"
    elif r1 and "Away" in r1d:
        side = "away"
    elif r3 and "Home" in r3d:
        side = "home"
    elif r3 and "Away" in r3d:
        side = "away"
    elif r4:
        side = "away"

    # Delete old signals for this market, keep latest only
    client.execute("DELETE FROM signals WHERE market_id = ?", [market_id])
    client.execute(
        "INSERT INTO signals (market_id, rule1_sustained_drift, rule1_detail, "
        "rule2_line_rejection, rule2_detail, rule3_opening_overreaction, rule3_detail, "
        "rule4_counter_trend, rule4_detail, signal_score, confidence, suggested_side, "
        "open_snapshot_id, close_snapshot_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [market_id, r1, r1d, r2, r2d, r3, r3d, r4, r4d,
         score, conf, side, int(first[0]), int(last[0])]
    )

    if score > 0:
        log(f"  Signal! score={score} ({conf}) → {side}")


async def fetch_markets():
    """Fetch top 5 football markets from Polymarket."""
    async with aiohttp.ClientSession() as session:
        params = {
            "active": "true",
            "closed": "false",
            "limit": "100",
            "order": "volume24hr",
            "ascending": "false",
            "tag": "sports",
        }
        try:
            async with session.get(
                f"{GAMMA_API}/markets",
                params=params,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    log(f"Gamma API returned {resp.status}")
                    return []
                data = await resp.json()
                if isinstance(data, dict):
                    data = data.get("data", [])
                log(f"Gamma API returned {len(data)} sports markets")
                return data
        except Exception as e:
            log(f"Gamma API error: {e}")
            return []


def store_market_and_snapshot(client, market):
    """Store a market and its current odds snapshot in Turso."""
    mid = market.get("conditionId") or market.get("id", "")
    title = market.get("question", market.get("groupItemTitle", "?"))
    home, away = extract_teams(title)
    league = detect_league(market)
    slug = market.get("slug", "")
    vol = float(market.get("volume24hr", 0) or 0)
    liq = float(market.get("liquidityClob", 0) or 0)

    # Upsert market
    client.execute(
        "INSERT INTO markets (id, event_slug, event_title, league, sport, "
        "home_team, away_team, game_date, status, polymarket_url, updated_at) "
        "VALUES (?, ?, ?, ?, 'football', ?, ?, ?, 'upcoming', ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET updated_at=datetime('now')",
        [mid, slug, title, league, home, away,
         market.get("endDate", ""),
         f"https://polymarket.com/event/{slug}" if slug else ""]
    )

    # Parse prices from outcomePrices
    hp, ap = 0.0, 0.0
    prices_raw = market.get("outcomePrices", "")
    if isinstance(prices_raw, str) and prices_raw:
        try:
            pl = json.loads(prices_raw)
            if len(pl) >= 2:
                hp, ap = float(pl[0]), float(pl[1])
        except (json.JSONDecodeError, ValueError):
            pass

    ho = round(1 / hp, 3) if hp > 0.001 else 0
    ao = round(1 / ap, 3) if ap > 0.001 else 0

    # Insert snapshot
    client.execute(
        "INSERT INTO odds_snapshots (market_id, home_price, home_odds, "
        "away_price, away_odds, volume_24h, liquidity) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [mid, round(hp, 4), ho, round(ap, 4), ao, vol, liq]
    )

    return mid


def main():
    if not TURSO_URL or not TURSO_TOKEN:
        print("ERROR: Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN env vars")
        sys.exit(1)

    log("Starting Polymarket → Turso fetch cycle")
    log(f"Turso DB: {TURSO_URL[:40]}...")

    # 1. Fetch markets from Polymarket
    all_markets = asyncio.run(fetch_markets())
    football = sorted(
        [m for m in all_markets if is_football(m)],
        key=lambda m: float(m.get("volume24hr", 0) or 0),
        reverse=True
    )[:5]

    if not football:
        log("No football markets found, using top 5 sports markets")
        football = all_markets[:5]

    if not football:
        log("No markets returned from API. Exiting.")
        return

    log(f"Processing {len(football)} markets")

    # 2. Write to Turso
    with libsql_client.create_client_sync(
        url=TURSO_URL,
        auth_token=TURSO_TOKEN
    ) as client:
        for i, market in enumerate(football):
            title = market.get("question", market.get("groupItemTitle", "?"))
            vol = float(market.get("volume24hr", 0) or 0)
            log(f"[{i+1}/{len(football)}] {title} (vol: ${vol:,.0f})")

            mid = store_market_and_snapshot(client, market)
            run_signal_rules(client, mid)

        # Summary
        snap_count = client.execute("SELECT COUNT(*) FROM odds_snapshots").rows[0][0]
        sig_count = client.execute(
            "SELECT COUNT(*) FROM signals WHERE signal_score > 0"
        ).rows[0][0]
        log(f"Total snapshots: {snap_count}")
        log(f"Active signals: {sig_count}")

    log("Fetch cycle complete!")


if __name__ == "__main__":
    main()
