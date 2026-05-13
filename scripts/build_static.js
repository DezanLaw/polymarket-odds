/**
 * build_static.js — Export Turso data → JSON → static HTML dashboard
 * Run: node scripts/build_static.js
 *
 * Reads all market/signal data from Turso Cloud,
 * writes dist/data/markets.json + dist/index.html
 */

import { createClient } from "@libsql/client";
import { writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DIST = join(__dirname, "..", "dist");

const db = createClient({
  url: process.env.TURSO_DATABASE_URL,
  authToken: process.env.TURSO_AUTH_TOKEN,
});

// ---- Export data from Turso ----

async function exportData() {
  const marketsRes = await db.execute(`
    SELECT id, event_slug, event_title, league, sport,
           home_team, away_team, game_date, status,
           polymarket_url, updated_at
    FROM markets ORDER BY updated_at DESC
  `);

  const markets = [];

  for (const row of marketsRes.rows) {
    const mid = row.id;
    const m = { ...row };

    // Latest snapshot
    const snap = await db.execute({
      sql: `SELECT home_price, home_odds, away_price, away_odds,
                   volume_24h, liquidity, snapshot_at
            FROM odds_snapshots WHERE market_id = ?
            ORDER BY snapshot_at DESC LIMIT 1`,
      args: [mid],
    });
    if (snap.rows.length) Object.assign(m, snap.rows[0]);

    // Opening odds (first snapshot)
    const first = await db.execute({
      sql: `SELECT home_odds, away_odds FROM odds_snapshots
            WHERE market_id = ? ORDER BY snapshot_at ASC LIMIT 1`,
      args: [mid],
    });
    if (first.rows.length) {
      m.open_home_odds = first.rows[0].home_odds;
      m.open_away_odds = first.rows[0].away_odds;
    }

    // Latest signal
    const sig = await db.execute({
      sql: `SELECT signal_score, confidence, suggested_side,
                   rule1_sustained_drift, rule1_detail,
                   rule2_line_rejection, rule2_detail,
                   rule3_opening_overreaction, rule3_detail,
                   rule4_counter_trend, rule4_detail
            FROM signals WHERE market_id = ?
            ORDER BY detected_at DESC LIMIT 1`,
      args: [mid],
    });
    if (sig.rows.length) Object.assign(m, sig.rows[0]);

    // Snapshot count
    const cnt = await db.execute({
      sql: "SELECT COUNT(*) as c FROM odds_snapshots WHERE market_id = ?",
      args: [mid],
    });
    m.snapshot_count = cnt.rows[0]?.c || 0;

    markets.push(m);
  }

  // Sort: signal score desc, then volume desc
  markets.sort(
    (a, b) =>
      (b.signal_score || 0) - (a.signal_score || 0) ||
      (b.volume_24h || 0) - (a.volume_24h || 0)
  );

  const totalSnaps = await db.execute("SELECT COUNT(*) as c FROM odds_snapshots");

  return {
    generated_at: new Date().toISOString(),
    stats: {
      total_markets: markets.length,
      strong_signals: markets.filter((m) => (m.signal_score || 0) >= 2).length,
      lean_signals: markets.filter((m) => (m.signal_score || 0) === 1).length,
      total_snapshots: totalSnaps.rows[0]?.c || 0,
    },
    markets,
  };
}

// ---- Build HTML ----

function buildHtml() {
  // Self-contained dashboard that loads data/markets.json
  return `<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket 賠率分析預測模型</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;700&family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0b0e;color:#e5e7eb;font-family:'DM Sans','Noto Sans TC',sans-serif;min-height:100vh}
.hd{padding:24px;border-bottom:1px solid rgba(255,255,255,.04);background:linear-gradient(180deg,rgba(16,185,129,.03),transparent)}
.tag{font-size:10px;font-weight:700;letter-spacing:2px;color:#10b981;text-transform:uppercase;margin-bottom:6px}
h1{font-size:24px;font-weight:800;letter-spacing:-.5px;color:#f9fafb}
.sub{font-size:12px;color:#6b7280;margin-top:4px}
.sts{display:flex;gap:12px;margin-top:16px}
.st{flex:1;padding:12px;border-radius:8px}
.st.g{background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.15)}
.st.a{background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.12)}
.st.w{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06)}
.sn{font-size:22px;font-weight:800;line-height:1}
.sn.green{color:#10b981}.sn.amber{color:#f59e0b}.sn.white{color:#e5e7eb}
.sl{font-size:10px;font-weight:600;color:#6b7280;margin-top:3px;letter-spacing:.5px}
.gd{padding:16px 24px;display:grid;gap:10px}
.cd{background:#111318;border-radius:10px;padding:16px;border:1px solid rgba(255,255,255,.06);cursor:pointer;position:relative;overflow:hidden}
.cd.strong{border-color:rgba(16,185,129,.35)}.cd.strong::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,#10b981,transparent)}
.cd.lean{border-color:rgba(245,158,11,.25)}
.cd-h{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px}
.lb{font-size:10px;font-weight:700;letter-spacing:1px;text-transform:uppercase;padding:2px 6px;border-radius:3px;display:inline-block}
.mt{font-size:15px;font-weight:700;color:#f3f4f6;margin-top:4px}
.sb{text-align:right}.sb .la{font-size:11px;font-weight:800;letter-spacing:1px;margin-bottom:2px}
.sb .sc{font-size:20px;font-weight:800;line-height:1}.sb .sc span{font-size:11px;color:#6b7280;font-weight:400}
.og{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.ob{background:rgba(255,255,255,.03);border-radius:6px;padding:10px;border:1px solid rgba(255,255,255,.04)}
.ob.sg{border-color:rgba(16,185,129,.3)}
.tl{font-size:10px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.or{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.or label{font-size:11px;color:#9ca3af}
.ov{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;padding:1px 8px;border-radius:3px}
.ov.sw{background:rgba(16,185,129,.12);color:#059669;border:1px solid rgba(16,185,129,.25)}
.ov.nm{background:rgba(107,114,128,.08);color:#9ca3af;border:1px solid rgba(107,114,128,.15)}
.dr{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700}
.dr.p{color:#10b981}.dr.n{color:#ef4444}.dr.z{color:#6b7280}
.rl{margin-top:10px;display:none}.cd.exp .rl{display:block}
.ru{display:flex;align-items:flex-start;gap:6px;padding:6px 10px;border-radius:5px;margin-bottom:4px}
.ru.f{background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.15)}
.ru.o{opacity:.4}
.rk{flex-shrink:0;width:24px;height:18px;display:flex;align-items:center;justify-content:center;border-radius:3px;font-size:9px;font-weight:800}
.ru.f .rk{background:#10b981;color:#fff}.ru.o .rk{background:rgba(255,255,255,.06);color:#6b7280}
.rt{font-size:11px;color:#d1d5db;line-height:1.3}.rt .nm{font-weight:600;color:#e5e7eb}.rt .dt{color:#9ca3af}
.me{display:flex;gap:8px;font-size:11px;color:#6b7280;margin-top:8px}.me .v{color:#d1d5db;font-weight:600}
.ft{padding:20px 24px;border-top:1px solid rgba(255,255,255,.04);font-size:11px;color:#374151;text-align:center}
@media(max-width:600px){.hd,.gd{padding:12px}.sts{flex-direction:column;gap:6px}h1{font-size:20px}}
</style>
</head>
<body>
<div class="hd">
<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px">
<div><div class="tag">POLYMARKET SPORTS</div><h1>賠率分析預測模型</h1><div class="sub">規則式訊號引擎 · Turso + GitHub Pages · 每 5 分鐘自動更新</div></div>
<div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#6b7280;text-align:right"><div style="color:#374151">LAST UPDATE</div><div id="ts">—</div></div>
</div>
<div class="sts">
<div class="st g"><div class="sn green" id="s-str">0</div><div class="sl">強訊號</div></div>
<div class="st a"><div class="sn amber" id="s-lean">0</div><div class="sl">偏向</div></div>
<div class="st w"><div class="sn white" id="s-tot">0</div><div class="sl">總場次</div></div>
</div></div>
<div class="gd" id="grid"><div style="text-align:center;padding:40px;color:#374151">載入中...</div></div>
<div class="ft">研究用途 · Turso Cloud + GitHub Pages</div>
<script>
const RN={rule1_sustained_drift:"動量訊號",rule2_line_rejection:"價格反轉",rule3_opening_overreaction:"聰明錢",rule4_counter_trend:"逆向價值"};
const LC={NBA:"#c9082a",EPL:"#3d195b","La Liga":"#ee8707","Serie A":"#024494",Bundesliga:"#d20515","Ligue 1":"#091c3e",UCL:"#1a1a6b",MLS:"#808080","World Cup":"#004c99",NFL:"#013369",MLB:"#002d72",NHL:"#000",Tennis:"#2d6a4f",F1:"#e10600",Sports:"#6b7280",Awards:"#d4a017",NCAA:"#ff6600"};
const pct=(v)=>(Number(v||0)*100).toFixed(1)+"%";
function rc(m){
const s=m.signal_score||0,cn=m.confidence||"NO_BET",cc=s>=2?"#10b981":s===1?"#f59e0b":"#6b7280";
const cls=s>=2?"strong":s===1?"lean":"",lc=LC[m.league]||"#6b7280";
const yNow=Number(m.home_price||0),nNow=Number(m.away_price||0);
const yOpen=Number(m.open_home_odds?1/m.open_home_odds:m.home_price||0);
const chg=yNow-yOpen;
const rks=["rule1_sustained_drift","rule2_line_rejection","rule3_opening_overreaction","rule4_counter_trend"];
const rh=rks.map((k,i)=>{const f=m[k]===1||m[k]==="1";
const detailKey="rule"+(i+1)+"_detail";const detail=m[detailKey]||"";
return'<div class="ru '+(f?"f":"o")+'"><span class="rk">R'+(i+1)+'</span><div class="rt"><span class="nm">'+RN[k]+"</span>"+(f&&detail?"<br><span class=dt>"+detail+"</span>":f?" — 觸發":" — 未觸發")+"</div></div>"}).join("");
const title=m.event_title||((m.home_team||"")+(m.away_team?" — "+m.away_team:""));
const url=m.polymarket_url||"#";
return'<div class="cd '+cls+'" onclick="this.classList.toggle(\'exp\')"><div class="cd-h"><div><div style="display:flex;align-items:center;gap:6px;margin-bottom:4px"><span class="lb" style="color:'+lc+";background:"+lc+'18">'+(m.league||m.sport||"")+'</span>'+
(m.game_date?'<span style="font-size:11px;color:#6b7280">'+m.game_date.split("T")[0]+'</span>':"")+
'</div><div class="mt">'+title+'</div></div><div class="sb"><div class="la" style="color:'+cc+'">'+cn+'</div><div class="sc" style="color:'+cc+'">'+s+'<span>/4</span></div></div></div>'+
'<div class="og"><div class="ob'+(m.suggested_side==="yes"?" sg":"")+'"><div class="tl" style="color:#10b981">YES'+(m.suggested_side==="yes"?" ★":"")+'</div>'+
'<div class="or"><label>概率</label><span class="ov" style="background:rgba(16,185,129,.12);color:#059669;border:1px solid rgba(16,185,129,.25)">'+pct(yNow)+'</span></div>'+
'<div class="or"><label>賠率</label><span class="ov nm">'+Number(m.home_odds||0).toFixed(2)+'x</span></div>'+
'<div class="or"><label>變化</label><span class="dr '+(chg>0?"p":chg<0?"n":"z")+'">'+(chg>0?"▲":chg<0?"▼":"—")+" "+Math.abs(chg*100).toFixed(1)+'pp</span></div></div>'+
'<div class="ob'+(m.suggested_side==="no"?" sg":"")+'"><div class="tl" style="color:#ef4444">NO'+(m.suggested_side==="no"?" ★":"")+'</div>'+
'<div class="or"><label>概率</label><span class="ov" style="background:rgba(239,68,68,.1);color:#ef4444;border:1px solid rgba(239,68,68,.2)">'+pct(nNow)+'</span></div>'+
'<div class="or"><label>賠率</label><span class="ov nm">'+Number(m.away_odds||0).toFixed(2)+'x</span></div>'+
'<div class="or"><label>變化</label><span class="dr '+(chg<0?"p":chg>0?"n":"z")+'">'+(chg<0?"▲":chg>0?"▼":"—")+" "+Math.abs(chg*100).toFixed(1)+'pp</span></div></div></div>'+
'<div class="rl">'+rh+
'<div class="me"><span>24h Vol: <span class="v">'+(m.volume_24h?"$"+(Number(m.volume_24h)/1000).toFixed(0)+"K":"—")+'</span></span>'+
'<span style="color:#374151">|</span><span><a href="'+url+'" target="_blank" style="color:#6b7280;text-decoration:none">Polymarket ↗</a></span>'+
'<span style="color:#374151">|</span><span>'+Number(m.snapshot_count||0)+' snapshots</span></div></div></div>';}

async function load(){try{const r=await fetch("./data/markets.json");const d=await r.json();
document.getElementById("ts").textContent=new Date(d.generated_at).toLocaleString("en-HK");
const s=d.stats||{};document.getElementById("s-str").textContent=s.strong_signals||0;
document.getElementById("s-lean").textContent=s.lean_signals||0;document.getElementById("s-tot").textContent=s.total_markets||0;
const g=document.getElementById("grid");
g.innerHTML=d.markets&&d.markets.length?d.markets.map(rc).join(""):'<div style="text-align:center;padding:40px;color:#374151">暫無數據 — 等 GitHub Actions 跑完第一次</div>';
}catch(e){document.getElementById("grid").innerHTML='<div style="text-align:center;padding:40px;color:#ef4444">載入失敗: '+e.message+"</div>"}}
load();
</script>
</body>
</html>`;
}

// ---- Main ----

async function main() {
  mkdirSync(join(DIST, "data"), { recursive: true });

  console.log("Exporting data from Turso...");
  const data = await exportData();

  const jsonPath = join(DIST, "data", "markets.json");
  writeFileSync(jsonPath, JSON.stringify(data, null, 2));
  console.log(`  Exported ${data.markets.length} markets → ${jsonPath}`);

  const htmlPath = join(DIST, "index.html");
  writeFileSync(htmlPath, buildHtml());
  console.log(`  Built → ${htmlPath}`);

  console.log(`\nStats: ${data.stats.total_markets} markets, ${data.stats.strong_signals} strong, ${data.stats.total_snapshots} snapshots`);

  db.close();
}

main().catch((e) => {
  console.error("Build failed:", e);
  process.exit(1);
});
