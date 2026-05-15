═══════════════════════════════════════════════════════════════
IBKR OPTIONS MCP SERVER  —  v1.0
Built for: Unified Agentic Alpha Protocol V9.2
═══════════════════════════════════════════════════════════════

WHAT THIS IS
─────────────────────────────────────────────────────────────
A small Python server that connects Claude Desktop directly
to your live IBKR TWS session. Once running, Claude can fetch
real-time options chain data — the same prices, Greeks, and
bid/ask you see in TWS — without any manual copy-pasting.

WHY THIS IS BETTER THAN YAHOO FINANCE
─────────────────────────────────────────────────────────────
Yahoo Finance (the other MCP server):
  • ~15 minute delay on all prices
  • Delta sometimes missing
  • No Gamma, Theta, Vega
  • No IV Rank

This IBKR server:
  ✅ Real-time bid/ask (same as TWS)
  ✅ Real-time Delta, Gamma, Theta, Vega (live Greeks)
  ✅ IV Rank calculated from 52 weeks of your own data
  ✅ All data from your own IBKR data subscription
  ✅ 100% read-only — cannot place any orders

WHAT'S IN THIS FOLDER
─────────────────────────────────────────────────────────────
ibkr-options-mcp/
  server.py          ← The MCP server (open it, read every line)
  test.py            ← Self-test script (run this first)
  requirements.txt   ← One dependency: ib_async
  README.md          ← This file

═══════════════════════════════════════════════════════════════
STEP 1 — CHECK PYTHON IS INSTALLED
═══════════════════════════════════════════════════════════════

Open Terminal (Mac) or Command Prompt (Windows) and type:

  python3 --version

You should see Python 3.9 or higher.
If not: download from python.org (official site only).

═══════════════════════════════════════════════════════════════
STEP 2 — INSTALL THE ONE DEPENDENCY
═══════════════════════════════════════════════════════════════

In Terminal, navigate to this folder:

  Mac/Linux:
    cd /path/to/ibkr-options-mcp
    pip install -r requirements.txt

  Windows:
    cd C:\path\to\ibkr-options-mcp
    pip install -r requirements.txt

This installs only ib_async — a single, well-known open-source
Python library maintained on GitHub and published on PyPI.
You can verify it at: pypi.org/project/ib-async

If you prefer to install it directly (same result):
  pip install ib_async

═══════════════════════════════════════════════════════════════
STEP 3 — ENABLE API ACCESS IN TWS (one-time, 2 minutes)
═══════════════════════════════════════════════════════════════

Open TWS and log in. Then:

  TWS menu → Edit → Global Configuration
    → API
      → Settings

Enable these options:
  ✅ Enable ActiveX and Socket Clients
  Socket port:     7497  (for paper account)
                or 7496  (for live account)
  ✅ Allow connections from localhost only  ← security setting
  ✅ Read-Only API  ← TICK THIS. Prevents any accidental orders.

Click Apply → OK.

You only need to do this once. TWS remembers these settings.

NOTE ON PORTS:
  7497 = TWS Paper Trading (recommended for testing)
  7496 = TWS Live Trading
  The server defaults to 7497 (paper). Safe for data-only use.
  Change TWS_PORT in server.py line 47 to switch to 7496.

═══════════════════════════════════════════════════════════════
STEP 4 — RUN THE SELF-TEST
═══════════════════════════════════════════════════════════════

With TWS open and logged in:

  python3 test.py

You should see 8 lines, most showing ✅ PASS.

ABOUT THE WARNINGS YOU MIGHT SEE:

  ⚠️ "Greeks not populated yet"
     Normal outside market hours (9:30am–4pm ET).
     Greeks work fine during trading hours.

  ⚠️ "Only N bars of IV data"
     Your IBKR market data subscription may not include
     full historical IV for some tickers. The server
     handles this gracefully with a clear message.

  ❌ "Cannot connect to TWS"
     → Is TWS open and logged in?
     → Did you enable the API in Step 3?
     → Is the port correct? (7497 for paper, 7496 for live)

═══════════════════════════════════════════════════════════════
STEP 5 — ADD TO CLAUDE DESKTOP CONFIG
═══════════════════════════════════════════════════════════════

Find the Claude Desktop config file:

  Mac:     ~/Library/Application Support/Claude/claude_desktop_config.json
  Windows: %APPDATA%\Claude\claude_desktop_config.json

Open it in any text editor. Add the ibkr-options entry:

IF THE FILE IS EMPTY (first MCP server):
────────────────────────────────────────────────────────────
{
  "mcpServers": {
    "ibkr-options": {
      "command": "python3",
      "args": ["/FULL/PATH/TO/ibkr-options-mcp/server.py"]
    }
  }
}
────────────────────────────────────────────────────────────

IF YOU ALREADY HAVE THE YAHOO FINANCE MCP SERVER:
────────────────────────────────────────────────────────────
{
  "mcpServers": {
    "options-data": {
      "command": "node",
      "args": ["/path/to/tradingview-options-mcp/src/server.js"]
    },
    "ibkr-options": {
      "command": "python3",
      "args": ["/FULL/PATH/TO/ibkr-options-mcp/server.py"]
    }
  }
}
────────────────────────────────────────────────────────────

Both servers can run at the same time. Claude uses whichever
is appropriate:
  ibkr-options  → when TWS is open (real-time data)
  options-data  → when TWS is closed (Yahoo Finance fallback)

REPLACE /FULL/PATH/TO/ with your actual path.

Mac example:
  "/Users/ajay/Documents/ibkr-options-mcp/server.py"

Windows example (forward slashes):
  "C:/Users/Ajay/Documents/ibkr-options-mcp/server.py"

═══════════════════════════════════════════════════════════════
STEP 6 — RESTART CLAUDE DESKTOP
═══════════════════════════════════════════════════════════════

Fully quit Claude Desktop:
  Mac:     Cmd+Q
  Windows: Right-click taskbar → Quit

Reopen Claude Desktop.
MCP servers only load at startup — restart is required.

═══════════════════════════════════════════════════════════════
STEP 7 — VERIFY IT CONNECTED
═══════════════════════════════════════════════════════════════

In any Claude Desktop chat, type:
  /mcp

You should see "ibkr-options" listed with a green dot.

Then test with:
  "Use ibkr-options to get a live quote for NVDA"

Claude calls the server, TWS returns the data, you get
a real-time price — no copy-pasting required.

═══════════════════════════════════════════════════════════════
HOW TO USE IN YOUR V9.2 TRADING PROJECT
═══════════════════════════════════════════════════════════════

Make sure TWS is running before starting your session.

── SESSION OPENER ──────────────────────────────────────────
"Scan the watchlist with live IBKR prices and rank
 by momentum. Show me top 2-3 candidates."

── IV RANK CHECK ────────────────────────────────────────────
"Get the IV Rank for NVDA and META from IBKR data"

── FULL OPTIONS CHAIN ───────────────────────────────────────
"Get the live NVDA options chain for June 20, 2026 —
 calls only, 5 strikes near ATM"

── COMPLETE V9.2 AUDIT ──────────────────────────────────────
"Run the full V9.2 spread analysis on NVDA:
 Bull Call Spread, June 20 expiry,
 long the $200 call, short the $202.50 call,
 2 contracts — use live IBKR data"

Claude fetches live prices, runs all 10 audit checks,
and delivers a verified execution ticket in seconds.

═══════════════════════════════════════════════════════════════
TOOLS AVAILABLE TO CLAUDE
═══════════════════════════════════════════════════════════════

  get_options_chain    — Live chain: bid/ask/all Greeks/OI
  get_spread_analysis  — Full V9.2 audit on real-time prices
  get_stock_quote      — Real-time price + expiry dates
  get_watchlist_scan   — Scan all 10 tickers simultaneously
  get_iv_rank          — IV Rank from 52-week IBKR history

═══════════════════════════════════════════════════════════════
SWITCHING BETWEEN PAPER AND LIVE
═══════════════════════════════════════════════════════════════

The server defaults to port 7497 (paper trading).
To switch to your live account:

  1. Open server.py in any text editor
  2. Find line 47:  TWS_PORT = 7497
  3. Change to:     TWS_PORT = 7496
  4. Save the file
  5. Restart Claude Desktop

RECOMMENDATION: Use paper port (7497) until you've run
at least 10 successful test analyses. The data is identical
between paper and live — only the account is different.

═══════════════════════════════════════════════════════════════
IMPORTANT LIMITATIONS
═══════════════════════════════════════════════════════════════

  • TWS must be running and logged in for this server to work.
    If TWS is closed, Claude falls back to the Yahoo Finance
    server (if you have it installed) automatically.

  • Greeks (Delta, Gamma, Theta) are most accurate during
    market hours (9:30am–4pm Eastern). Outside hours you
    may see NaN or stale values for some tickers. This is
    normal IBKR behavior, not a bug.

  • IBKR data throttling: requesting too many option contracts
    simultaneously can trigger rate limits. The server requests
    data efficiently, but avoid scanning 10 tickers with full
    chains all at once.

  • Market data subscriptions: you need an IBKR market data
    subscription that covers US equity options for the tickers
    you want. Most IBKR Pro accounts include this, but verify
    in Account Management → Market Data Subscriptions.

═══════════════════════════════════════════════════════════════
SECURITY
═══════════════════════════════════════════════════════════════

  • Read-Only API mode is enabled in the connection code.
    This server CANNOT place, modify, or cancel any orders.

  • All communication is localhost-only (127.0.0.1).
    Nothing goes to the internet.

  • No credentials are stored anywhere in this code.
    The server uses your already-logged-in TWS session.

  • The server stops running when Claude Desktop is closed.

═══════════════════════════════════════════════════════════════
TROUBLESHOOTING
═══════════════════════════════════════════════════════════════

"ibkr-options not showing in /mcp":
  → Fully quit and reopen Claude Desktop
  → Check the file path in claude_desktop_config.json
  → Run python3 server.py in Terminal — any errors shown?

"TWS Connection Error":
  → Is TWS open and logged in?
  → Did you enable API in TWS settings? (Step 3)
  → Correct port? (7497 paper, 7496 live)
  → Try: python3 test.py  for detailed diagnosis

"Greeks are NaN":
  → Normal outside 9:30am–4pm Eastern
  → Try again during market hours

"No option contracts found":
  → Check IBKR market data subscriptions
  → Account Management → Market Data → Manage Subscriptions
  → Need: US Equity Options (OPRA feed)

"ClientId conflict":
  → Another program is using clientId=15
  → Change TWS_CLIENT_ID = 15 to 16 in server.py line 48

═══════════════════════════════════════════════════════════════
VERSION HISTORY
═══════════════════════════════════════════════════════════════

v1.0  April 2026  Initial build for V9.2 protocol
                  5 tools: chain, spread, quote, scan, iv_rank
                  Pure Python, single dependency (ib_async)
                  Read-only, localhost-only, no credentials stored

═══════════════════════════════════════════════════════════════
