"""
fetch_odds.py — Polymarket → Turso pipeline (v2)
==================================================
Adapted for Polymarket's ACTUAL market structure:
- Markets are "Will X happen?" with Yes/No outcomes
- NOT "Team A vs Team B" match betting
- Sports markets include: World Cup winners, tournament outcomes,
  player transfers, season bets, etc.

Signal rules adapted for prediction market structure:
- R1: Momentum — Yes price rising fast
- R2: Reversal — Price spiked then corrected
- R3: Smart Money — High volume + price movement
- R4: Contrarian — Very low Yes price drifting up
"""

import asyncio
import aiohttp
import json
import os
import sys
from datetime import datetime, timezone

import libsql_client

_raw_url = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_URL = _raw_url.replace("libsql://", "https://")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")
GAMMA_API = "https://gamma-api.polymarket.com"

SPORTS_KW = [
    "world cup", "fifa", "nba", "nfl", "mlb", "nhl", "wnba",
    "premier league", "epl", "champions league", "ucl",
    "la liga", "serie a", "bundesliga", "ligue 1",
    "super bowl", "stanley cup", "march madness",
    "olympics", "wimbledon", "us open", "roland garros",
    "formula 1", "f1", "mls cup",
    "mvp", "ballon d'or", "golden boot",
    "transfer", "sign with", "traded to",
    "lakers", "celtics", "warriors", "yankees", "dodgers",
    "chiefs", "eagles", "cowboys",
    "arsenal", "chelsea", "liverpool", "man city", "man united",
    "barcelona", "real madrid", "bayern", "juventus", "inter milan",
    "psg", "dortmund", "napoli", "tottenham", "atletico",
]

EXCLUDE = [
    "pandemic", "regime", "strike on", "assassination", "nuclear",
    "invasion", "sanctions", "election", "president", "congress",
    "impeach", "indictment", "verdict", "hantavirus", "bird flu",
    "tariff", "recession", "bankruptcy", "cease", "war ",
]

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")

def is_real_sports(m):
    text = " ".join([
        m.get("question", ""), m.get("description", ""),
        m.get("groupItemTitle", ""),
        " ".join(m.get("tags", [])) if isinstance(m.get("tags"), list) else "",
    ]).lower()
    if any(ex in text for ex in EXCLUDE):
        return False
    return any(kw in text for kw in SPORTS_KW)

def detect_sport_league(m):
    text = " ".join([m.get("question",""),m.get("description",""),m.get("groupItemTitle","")]).lower()
    for kw, sport, lg in [
        ("world cup","football","World Cup"),("fifa","football","World Cup"),
        ("premier league","football","EPL"),("epl","football","EPL"),
        ("champions league","football","UCL"),("ucl","football","UCL"),
        ("la liga","football","La Liga"),("serie a","football","Serie A"),
        ("bundesliga","football","Bundesliga"),("ligue 1","football","Ligue 1"),
        ("mls","football","MLS"),
        ("nba","basketball","NBA"),("wnba","basketball","WNBA"),
        ("march madness","basketball","NCAA"),
        ("nfl","american_football","NFL"),("super bowl","american_football","NFL"),
        ("mlb","baseball","MLB"),
        ("nhl","hockey","NHL"),("stanley cup","hockey","NHL"),
        ("formula 1","motorsport","F1"),("f1","motorsport","F1"),
        ("wimbledon","tennis","Tennis"),("us open","tennis","Tennis"),
        ("olympics","multi","Olympics"),
        ("ballon d'or","football","Awards"),("mvp","multi","Awards"),
    ]:
        if kw in text:
            return sport, lg
    return "sports", "Sports"

def parse_title(title):
    for sep in [" vs "," vs. "," v "]:
        if sep in title:
            p = title.split(sep, 1)
            return p[0].strip(), p[1].strip()
    clean = title.replace("?","").strip()
    if clean.lower().startswith("will "):
        clean = clean[5:]
    for verb in [" win "," make "," reach "," qualify "," advance "," sign with "]:
        if verb in clean.lower():
            i = clean.lower().index(verb)
            return clean[:i].strip(), clean[i+len(verb):].strip()
    return clean, ""

def run_signals(client, market_id):
    result = client.execute(
        "SELECT id, home_price, home_odds, away_price, away_odds, volume_24h "
        "FROM odds_snapshots WHERE market_id = ? ORDER BY snapshot_at ASC",
        [market_id]
    )
    rows = result.rows
    if len(rows) < 2:
        return
    first, last = rows[0], rows[-1]
    yes_open = float(first[1] or 0)
    yes_now = float(last[1] or 0)
    vol = float(last[5] or 0)
    change = yes_now - yes_open
    pct = (change / yes_open * 100) if yes_open > 0.001 else 0

    r1=r2=r3=r4=0; r1d=r2d=r3d=r4d=""

    # R1: Momentum — Yes price rising ≥5pp
    if change >= 0.05:
        r1, r1d = 1, f"Momentum: {yes_open:.0%} → {yes_now:.0%} (+{change:.0%})"

    # R2: Reversal — price ranged then mean-reverted
    if len(rows) >= 3:
        prices = [float(r[1] or 0) for r in rows]
        pk, tr = max(prices), min(prices)
        if pk - tr >= 0.05 and tr < yes_now < pk:
            r2, r2d = 1, f"Reversal: ranged {tr:.0%}–{pk:.0%}, now {yes_now:.0%}"

    # R3: Smart money — high vol + movement
    if vol > 200000 and abs(change) >= 0.03:
        d = "rising" if change > 0 else "falling"
        r3, r3d = 1, f"Smart money: ${vol/1000:.0f}K vol, {d} {abs(change):.0%}"

    # R4: Contrarian — cheap Yes (<10%) drifting up
    if yes_open < 0.10 and change > 0.02:
        r4, r4d = 1, f"Contrarian: {yes_open:.0%} → {yes_now:.0%}"

    score = r1+r2+r3+r4
    conf = "STRONG" if score>=2 else ("LEAN" if score==1 else "NO_BET")
    side = "yes" if change > 0 else ("no" if change < 0 else "none")

    client.execute("DELETE FROM signals WHERE market_id = ?", [market_id])
    client.execute(
        "INSERT INTO signals (market_id, rule1_sustained_drift, rule1_detail, "
        "rule2_line_rejection, rule2_detail, rule3_opening_overreaction, rule3_detail, "
        "rule4_counter_trend, rule4_detail, signal_score, confidence, suggested_side, "
        "open_snapshot_id, close_snapshot_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [market_id, r1,r1d, r2,r2d, r3,r3d, r4,r4d,
         score, conf, side, int(first[0]), int(last[0])]
    )
    if score > 0:
        log(f"  → Signal! score={score} ({conf}) → {side}")

async def fetch_markets():
    all_markets = []
    async with aiohttp.ClientSession() as session:
        params = {"active":"true","closed":"false","limit":"100",
                  "order":"volume24hr","ascending":"false","tag":"sports"}
        try:
            async with session.get(f"{GAMMA_API}/markets", params=params,
                                   timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict): data = data.get("data", [])
                    all_markets.extend(data)
                    log(f"Gamma API [sports]: {len(data)} markets")
        except Exception as e:
            log(f"Gamma API error: {e}")
    return all_markets

def store(client, market):
    mid = market.get("conditionId") or market.get("id","")
    title = market.get("question", market.get("groupItemTitle","?"))
    subject, event = parse_title(title)
    sport, league = detect_sport_league(market)
    slug = market.get("slug","")
    vol = float(market.get("volume24hr",0) or 0)
    liq = float(market.get("liquidityClob",0) or 0)

    client.execute(
        "INSERT INTO markets (id,event_slug,event_title,league,sport,"
        "home_team,away_team,game_date,status,polymarket_url,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,'upcoming',?,datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET updated_at=datetime('now'),league=?,sport=?",
        [mid,slug,title,league,sport,subject,event,
         market.get("endDate",""),
         f"https://polymarket.com/event/{slug}" if slug else "",
         league,sport]
    )

    yp, np_ = 0.0, 0.0
    raw = market.get("outcomePrices","")
    if isinstance(raw,str) and raw:
        try:
            pl = json.loads(raw)
            if len(pl)>=2: yp, np_ = float(pl[0]), float(pl[1])
        except: pass

    yo = round(1/yp,3) if yp > 0.001 else 0
    no = round(1/np_,3) if np_ > 0.001 else 0

    client.execute(
        "INSERT INTO odds_snapshots (market_id,home_price,home_odds,"
        "away_price,away_odds,volume_24h,liquidity) VALUES (?,?,?,?,?,?,?)",
        [mid, round(yp,4), yo, round(np_,4), no, vol, liq]
    )
    return mid

def main():
    if not TURSO_URL or not TURSO_TOKEN:
        print("ERROR: Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN"); sys.exit(1)
    log("Starting Polymarket → Turso fetch (v2)")

    all_mkts = asyncio.run(fetch_markets())
    sports = [m for m in all_mkts if is_real_sports(m)]
    log(f"Strict filter: {len(sports)} sports / {len(all_mkts)} total")

    for m in all_mkts[:15]:
        t = m.get("question","?")[:65]
        log(f"  {'✓' if is_real_sports(m) else '✗'} {t}")

    sports.sort(key=lambda m: float(m.get("volume24hr",0) or 0), reverse=True)
    top = sports[:10]

    if not top:
        log("No real sports markets found on Polymarket right now.")
        log("Polymarket is primarily a political prediction market.")
        log("Sports markets appear during major events (World Cup, Super Bowl, etc).")
        return

    log(f"Processing {len(top)} markets")

    with libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN) as client:
        for i, m in enumerate(top):
            title = m.get("question",m.get("groupItemTitle","?"))
            vol = float(m.get("volume24hr",0) or 0)
            log(f"[{i+1}/{len(top)}] {title} (${vol:,.0f})")
            mid = store(client, m)
            run_signals(client, mid)

        sc = client.execute("SELECT COUNT(*) FROM odds_snapshots").rows[0][0]
        sg = client.execute("SELECT COUNT(*) FROM signals WHERE signal_score>0").rows[0][0]
        log(f"DB: {sc} snapshots, {sg} signals")

    log("Done!")

if __name__ == "__main__":
    main()
