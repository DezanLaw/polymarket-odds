# Polymarket 賠率分析 — 部署教學（JS + Turso + GitHub）

Tech stack:
- **Node.js** — DB init (`@libsql/client`) + 靜態站 build
- **Python** — 只用喺 Polymarket API 抓數據（aiohttp 最穩定）
- **Turso** — hosted SQLite（免費 tier）
- **GitHub Actions** — 每 5 分鐘自動跑
- **GitHub Pages** — 免費 hosting

半年成本 = **US$0**

---

## 文件結構

```
polymarket-odds/
├── package.json              ← Node.js deps (@libsql/client)
├── requirements.txt          ← Python deps (aiohttp, libsql-client)
├── .gitignore
├── .github/
│   └── workflows/
│       └── fetch-and-deploy.yml  ← GitHub Actions (每 5 分鐘)
├── scripts/
│   ├── init_db.js            ← [JS] 建立 Turso 表（跑一次）
│   ├── build_static.js       ← [JS] 讀 Turso → JSON + HTML
│   └── fetch_odds.py         ← [Python] Polymarket API → Turso
└── dist/                     ← 自動生成，部署到 GitHub Pages
    ├── index.html
    └── data/markets.json
```

---

## 第 1 步：裝工具（5 分鐘）

打開 Terminal（Cmd+Space → "Terminal"）

```bash
# 檢查 Node.js（需要 v18+）
node --version
# 如果冇，裝 Node：
# brew install node

# 檢查 Python
python3 --version
# 如果冇，裝 Python：
# brew install python

# 檢查 Git
git --version
```

---

## 第 2 步：開 Turso 帳號 + 建 DB（5 分鐘）

```bash
# 裝 Turso CLI
brew install tursodatabase/tap/turso

# 登入（會開瀏覽器）
turso auth login

# 建 database
turso db create polymarket-odds

# 攞 URL（抄低！）
turso db show polymarket-odds --url
# 輸出：libsql://polymarket-odds-yourname.turso.io

# 建 token（抄低！）
turso db tokens create polymarket-odds
# 輸出：eyJhbG...（好長嘅一串）
```

---

## 第 3 步：下載 + 設定 Project（3 分鐘）

```bash
# 去 Downloads，解壓 project
cd ~/Downloads
tar xzf polymarket-turso-v2.tar.gz
cd polymarket-turso-v2

# 裝 JS dependencies
npm install

# 裝 Python dependencies
pip install -r requirements.txt

# 設定環境變數（換成你自己嘅值）
export TURSO_DATABASE_URL="libsql://polymarket-odds-yourname.turso.io"
export TURSO_AUTH_TOKEN="your-token-here"

# 初始化 DB 表
node scripts/init_db.js
```

你應該見到：
```
Connecting to: libsql://polymarket-odds-...
  ✓ markets
  ✓ odds_snapshots
  ✓ signals
  ✓ bets
Tables: [bets, markets, odds_snapshots, signals]
Database init complete!
```

---

## 第 4 步：本地測試（2 分鐘）

```bash
# 抓一次 Polymarket 數據
python3 scripts/fetch_odds.py

# Build 靜態站
node scripts/build_static.js

# 用瀏覽器開 dist/index.html 睇下
open dist/index.html
```

---

## 第 5 步：上傳到 GitHub（3 分鐘）

```bash
# 1. 去 https://github.com/new 開新 repo
#    Name: polymarket-odds
#    Visibility: Public
#    唔好 tick "Add a README"

# 2. Push code
git init
git add .
git commit -m "init: polymarket odds analyzer"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/polymarket-odds.git
git push -u origin main
```

---

## 第 6 步：加 GitHub Secrets（2 分鐘）

去你嘅 repo → **Settings** → **Secrets and variables** → **Actions**

撳 "New repository secret"，加兩個：

| Name | Value |
|------|-------|
| `TURSO_DATABASE_URL` | `libsql://polymarket-odds-yourname.turso.io` |
| `TURSO_AUTH_TOKEN` | `eyJhbG...`（你嘅 token） |

---

## 第 7 步：啟動！（2 分鐘）

1. 去 repo → **Actions** tab
2. 撳 "Fetch odds & deploy dashboard"
3. 撳 "Run workflow" → "Run workflow"
4. 等 1-2 分鐘（綠色 ✓ = 成功）
5. 去 **Settings** → **Pages** → Branch: `gh-pages` / `/ (root)` → Save
6. 等 1 分鐘，打開：

```
https://YOUR_USERNAME.github.io/polymarket-odds/
```

🎉 **搞掂！每 5 分鐘自動更新。**

---

## 常見問題

**Q: npm ci 失敗**
A: 確認有 `package-lock.json`。如果冇，先跑 `npm install` 再 commit。

**Q: Actions 跑失敗 "TURSO_DATABASE_URL not set"**
A: 返去 Step 6 檢查 Secrets 有冇打錯名。

**Q: GitHub Pages 404**
A: Settings → Pages 嘅 branch 要選 `gh-pages`。等 2 分鐘再 refresh。

**Q: "Gamma API returned 0 markets"**
A: Polymarket 暫時冇 sports/football markets。正常，下次會再試。

**Q: 超過 GitHub Actions 免費用量？**
A: 改 cron 做 `*/10 * * * *`（每 10 分鐘），用量減半。

**Q: 點樣手動睇 DB 內容？**
```bash
turso db shell polymarket-odds
> SELECT event_title, signal_score, confidence FROM markets m JOIN signals s ON m.id = s.market_id ORDER BY signal_score DESC;
```
