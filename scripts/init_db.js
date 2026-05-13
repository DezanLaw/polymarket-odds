/**
 * init_db.js — Create tables in Turso Cloud
 * Run ONCE: node scripts/init_db.js
 *
 * Env vars needed:
 *   TURSO_DATABASE_URL=libsql://your-db.turso.io
 *   TURSO_AUTH_TOKEN=your-jwt-token
 */

import { createClient } from "@libsql/client";

const db = createClient({
  url: process.env.TURSO_DATABASE_URL,
  authToken: process.env.TURSO_AUTH_TOKEN,
});

const statements = [
  `CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,
    event_slug TEXT NOT NULL,
    event_title TEXT NOT NULL,
    league TEXT NOT NULL,
    sport TEXT NOT NULL DEFAULT 'football',
    home_team TEXT,
    away_team TEXT,
    game_date TEXT,
    status TEXT DEFAULT 'upcoming',
    resolution TEXT,
    polymarket_url TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
  )`,

  `CREATE TABLE IF NOT EXISTS odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    snapshot_at TEXT NOT NULL DEFAULT (datetime('now')),
    home_price REAL, home_odds REAL, home_bid REAL, home_ask REAL,
    away_price REAL, away_odds REAL, away_bid REAL, away_ask REAL,
    volume_24h REAL DEFAULT 0,
    liquidity REAL DEFAULT 0,
    handicap_line REAL,
    raw_data TEXT
  )`,

  `CREATE INDEX IF NOT EXISTS idx_snap_market ON odds_snapshots(market_id, snapshot_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_snap_time ON odds_snapshots(snapshot_at DESC)`,

  `CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    rule1_sustained_drift INTEGER DEFAULT 0, rule1_detail TEXT,
    rule2_line_rejection INTEGER DEFAULT 0, rule2_detail TEXT,
    rule3_opening_overreaction INTEGER DEFAULT 0, rule3_detail TEXT,
    rule4_counter_trend INTEGER DEFAULT 0, rule4_detail TEXT,
    signal_score INTEGER DEFAULT 0,
    confidence TEXT,
    suggested_side TEXT,
    open_snapshot_id INTEGER,
    close_snapshot_id INTEGER
  )`,

  `CREATE INDEX IF NOT EXISTS idx_sig_market ON signals(market_id, detected_at DESC)`,
  `CREATE INDEX IF NOT EXISTS idx_sig_score ON signals(signal_score DESC)`,

  `CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL, signal_id INTEGER,
    placed_at TEXT DEFAULT (datetime('now')),
    side TEXT NOT NULL, stake REAL, odds_at_entry REAL, price_at_entry REAL,
    result TEXT, payout REAL, profit REAL, resolved_at TEXT, notes TEXT
  )`,
];

console.log(`Connecting to: ${process.env.TURSO_DATABASE_URL?.slice(0, 40)}...`);

for (const sql of statements) {
  const match = sql.match(/(?:TABLE|INDEX)\s+IF NOT EXISTS\s+(\S+)/i);
  const name = match ? match[1] : "statement";
  try {
    await db.execute(sql);
    console.log(`  ✓ ${name}`);
  } catch (e) {
    console.log(`  ✗ ${name}: ${e.message}`);
  }
}

const tables = await db.execute(
  "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
);
console.log(`\nTables: [${tables.rows.map((r) => r.name).join(", ")}]`);
console.log("Database init complete!");

db.close();
