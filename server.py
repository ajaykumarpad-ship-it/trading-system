#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
 IBKR OPTIONS + STOCK SWING MCP SERVER  —  v2.4
 Built for: Unified Agentic Alpha Protocol V9.9 (options)
            + Swing Alpha V1.1 (stock swing trading)
 Transport: stdio JSON-RPC 2.0
 Requires:  ib_async  (pip install ib_async)
            TWS running with API enabled

 v2.4 CHANGES from v2.3:
   - NEW: bear_watchlist.json support (bearish options universe)
   - NEW TOOL: get_bear_watchlist   (read bear watchlist)
   - NEW TOOL: update_bear_watchlist (add/remove, approval gate)
   - NEW TOOL: scan_combined_setups  (unified bull+bear daily scan)
     Scores all 20 options names (10 bull + 10 bear), runs both
     directional analyses, resolves same-ticker conflicts by taking
     higher score, returns top 3 ranked setups regardless of direction.

 ONE SERVER, TWO PROJECTS:
   This single server backs two separate Claude projects:
   1. Options Alpha V9.9  (uses watchlist.json + bear_watchlist.json)
   2. Swing Alpha V1.1    (uses swing_watchlist.json)

 TOOLS EXPOSED TO CLAUDE (21 total in v2.4):

   OPTIONS TOOLS (5)
     1.  get_options_chain      Live chain: bid/ask/Greeks/IV/OI
     2.  get_spread_analysis    Full V9.9 audit on live prices
     3.  get_stock_quote        Real-time price + IV snapshot
     4.  get_watchlist_scan     Scan all watchlist tickers
     5.  get_iv_rank            IV Rank from 52-week IV history

   STOCK MOMENTUM TOOLS (3) — 30-45 min intraday holds
     6.  momentum_scan          Scan momentum watchlist on 5-min bars
     7.  get_momentum_bars      5/15-min bars + indicators
     8.  trade_update           15-min check-in on open position

   OPTIONS WATCHLIST TOOLS (3)
     9.  get_watchlist          Read current bullish options watchlist
    10.  update_watchlist       Add/remove (requires confirmed=true)
    11.  scan_full_universe     Weekly NDX-100 promotion scan

   STOCK SWING TOOLS (6)
    12.  get_swing_watchlist    Read swing stock watchlist
    13.  update_swing_watchlist Add/remove swing stock (with approval)
    14.  scan_swing_universe    Weekly NDX scan for swing candidates
    15.  get_swing_setup        Full pullback/breakout analysis on ticker
    16.  get_position_size      Calculate shares for given risk
    17.  scan_pullback_setups   Daily scan: tradeable pullback setups

   PORTFOLIO TOOLS (1)
    18.  get_open_positions     Live IBKR portfolio position count

   BEAR OPTIONS TOOLS (3) — NEW in v2.4
    19.  get_bear_watchlist     Read bearish options watchlist
    20.  update_bear_watchlist  Add/remove bear ticker (approval gate)
    21.  scan_combined_setups   UNIFIED daily scan: scores all bull+bear
                                candidates, returns top 3 regardless of
                                direction. PRIMARY daily tool for V9.9.

 WATCHLIST FILES (all live next to server.py):
   watchlist.json        — bullish options universe  (10 names)
   bear_watchlist.json   — bearish options universe  (10 names)
   swing_watchlist.json  — stock swing universe      (10 names)

 PORTS:
   7497  TWS paper trading  (default, safe)
   7496  TWS live trading
   4001  IB Gateway paper
   4002  IB Gateway live
═══════════════════════════════════════════════════════════════
"""

import sys
import json
import asyncio
import threading
import logging
from datetime import datetime, timedelta
from typing import Any
from pathlib import Path

# ── Logging to stderr only (stdout is reserved for JSON-RPC) ──
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format='[IBKR-MCP] %(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── ib_async import ────────────────────────────────────────────
try:
    from ib_async import IB, Stock, Option, util
except ImportError:
    sys.stderr.write(
        "\n[IBKR-MCP] ERROR: ib_async not installed.\n"
        "Run:  pip install ib_async\n"
        "Then restart Claude Desktop.\n\n"
    )
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# SECTION 1 — CONFIGURATION
# ═══════════════════════════════════════════════════════════════

TWS_HOST      = '127.0.0.1'
TWS_PORT      = 7497        # 7497=TWS paper | 7496=TWS live
TWS_CLIENT_ID = 15          # Change if you get a client ID conflict

# ── Watchlist file paths (all live next to server.py) ─────────
WATCHLIST_FILE       = Path(__file__).parent / "watchlist.json"
SWING_WATCHLIST_FILE = Path(__file__).parent / "swing_watchlist.json"
BEAR_WATCHLIST_FILE  = Path(__file__).parent / "bear_watchlist.json"

# ── Default watchlists (used only if JSON files missing) ──────
_DEFAULT_WATCHLIST = [
    'NVDA', 'META', 'MSFT', 'MRVL', 'ARM',
    'AMZN', 'GOOGL', 'PLTR', 'AMD', 'AVGO'
]

_DEFAULT_SWING_WATCHLIST = [
    'NVDA', 'META', 'MSFT', 'AMZN', 'GOOGL',
    'AVGO', 'AMD', 'CRWD', 'NFLX', 'COST'
]

_DEFAULT_BEAR_WATCHLIST = [
    'TSLA', 'MSTR', 'SMCI', 'AMD', 'NFLX',
    'DDOG', 'PANW', 'MELI', 'RIVN', 'INTC'
]


def load_watchlist_file() -> dict:
    """
    Read the full watchlist JSON file.
    Returns a dict with tickers and metadata.
    Falls back to default if file is missing or unreadable.
    """
    if not WATCHLIST_FILE.exists():
        log.warning(f"watchlist.json not found at {WATCHLIST_FILE}")
        log.warning("Using default watchlist. Create watchlist.json to customize.")
        return {
            "watchlist_name": "DefaultWatchlist",
            "max_size": 12,
            "min_size": 6,
            "tickers": [{"symbol": s, "added_date": "default",
                         "added_reason": "default", "tier": "core"}
                        for s in _DEFAULT_WATCHLIST],
            "history": []
        }
    try:
        with open(WATCHLIST_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Cannot read watchlist.json: {e}")
        log.error("Falling back to default watchlist.")
        return {
            "watchlist_name": "DefaultWatchlist (file error)",
            "max_size": 12,
            "min_size": 6,
            "tickers": [{"symbol": s, "added_date": "default",
                         "added_reason": "default", "tier": "core"}
                        for s in _DEFAULT_WATCHLIST],
            "history": []
        }


def save_watchlist_file(data: dict) -> bool:
    """Save the watchlist JSON. Returns True on success."""
    try:
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError as e:
        log.error(f"Cannot save watchlist.json: {e}")
        return False


def get_watchlist_symbols() -> list:
    """Get just the ticker symbols. Hot-reloads from file each call."""
    data = load_watchlist_file()
    return [t["symbol"] for t in data.get("tickers", [])]


def load_swing_watchlist_file() -> dict:
    """
    Read the SWING STOCK watchlist JSON file.
    Returns a dict with tickers and metadata.
    Falls back to default if file is missing or unreadable.
    """
    if not SWING_WATCHLIST_FILE.exists():
        log.warning(f"swing_watchlist.json not found at {SWING_WATCHLIST_FILE}")
        log.warning("Using default swing watchlist. Create file to customize.")
        return {
            "watchlist_name": "DefaultSwingWatchlist",
            "max_size": 12,
            "min_size": 6,
            "tickers": [{"symbol": s, "added_date": "default",
                         "added_reason": "default", "tier": "core"}
                        for s in _DEFAULT_SWING_WATCHLIST],
            "history": []
        }
    try:
        with open(SWING_WATCHLIST_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Cannot read swing_watchlist.json: {e}")
        log.error("Falling back to default swing watchlist.")
        return {
            "watchlist_name": "DefaultSwingWatchlist (file error)",
            "max_size": 12,
            "min_size": 6,
            "tickers": [{"symbol": s, "added_date": "default",
                         "added_reason": "default", "tier": "core"}
                        for s in _DEFAULT_SWING_WATCHLIST],
            "history": []
        }


def save_swing_watchlist_file(data: dict) -> bool:
    """Save the SWING STOCK watchlist JSON. Returns True on success."""
    try:
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        with open(SWING_WATCHLIST_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError as e:
        log.error(f"Cannot save swing_watchlist.json: {e}")
        return False


def get_swing_watchlist_symbols() -> list:
    """Get just the SWING ticker symbols. Hot-reloads from file each call."""
    data = load_swing_watchlist_file()
    return [t["symbol"] for t in data.get("tickers", [])]


def load_bear_watchlist_file() -> dict:
    """
    Read the BEAR OPTIONS watchlist JSON file.
    Returns a dict with tickers and metadata.
    Falls back to default if file is missing or unreadable.
    """
    if not BEAR_WATCHLIST_FILE.exists():
        log.warning(f"bear_watchlist.json not found at {BEAR_WATCHLIST_FILE}")
        log.warning("Using default bear watchlist. Create file to customize.")
        return {
            "watchlist_name": "DefaultBearWatchlist",
            "max_size": 12,
            "min_size": 6,
            "tickers": [{"symbol": s, "added_date": "default",
                         "added_reason": "default", "tier": "core"}
                        for s in _DEFAULT_BEAR_WATCHLIST],
            "history": []
        }
    try:
        with open(BEAR_WATCHLIST_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Cannot read bear_watchlist.json: {e}")
        log.error("Falling back to default bear watchlist.")
        return {
            "watchlist_name": "DefaultBearWatchlist (file error)",
            "max_size": 12,
            "min_size": 6,
            "tickers": [{"symbol": s, "added_date": "default",
                         "added_reason": "default", "tier": "core"}
                        for s in _DEFAULT_BEAR_WATCHLIST],
            "history": []
        }


def save_bear_watchlist_file(data: dict) -> bool:
    """Save the BEAR OPTIONS watchlist JSON. Returns True on success."""
    try:
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        with open(BEAR_WATCHLIST_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError as e:
        log.error(f"Cannot save bear_watchlist.json: {e}")
        return False


def get_bear_watchlist_symbols() -> list:
    """Get just the BEAR ticker symbols. Hot-reloads from file each call."""
    data = load_bear_watchlist_file()
    return [t["symbol"] for t in data.get("tickers", [])]


# Backward-compat alias used by existing code at startup logging.
# All tools should call get_watchlist_symbols() directly to get
# fresh data on every invocation (hot reload).
WATCHLIST = get_watchlist_symbols()

DEFAULT_STRIKES_NEAR_ATM = 5

# Stock momentum watchlist (30-45 min intraday holds, $10-25 range)
SCALP_WATCHLIST = [
    'SOFI', 'RIVN', 'RIOT', 'INTC', 'SOUN',
    'HOOD', 'MARA', 'RDDT', 'LCID', 'NOK'
]

# Position sizing for $5,000 account
ACCOUNT_SIZE     = 5000
MAX_POSITION_PCT = 0.25     # 25% of account per trade = $1,250 max
MAX_RISK_DOLLARS = 25.00    # Hard cap on dollar risk per trade
COMMISSION_RT    = 2.00     # Round-trip commission estimate


# ═══════════════════════════════════════════════════════════════
# SECTION 2 — IBKR CONNECTION MANAGER
# ═══════════════════════════════════════════════════════════════

_ib: IB | None = None


async def get_ib() -> IB:
    """Return the active IB connection, creating it if needed."""
    global _ib
    if _ib is None or not _ib.isConnected():
        log.info(f"Connecting to TWS at {TWS_HOST}:{TWS_PORT} "
                 f"clientId={TWS_CLIENT_ID}")
        _ib = IB()
        try:
            await _ib.connectAsync(
                host=TWS_HOST,
                port=TWS_PORT,
                clientId=TWS_CLIENT_ID,
                readonly=True,
                timeout=15
            )
            log.info("Connected to TWS successfully")
        except Exception as e:
            _ib = None
            raise ConnectionError(
                f"Cannot connect to TWS on port {TWS_PORT}.\n"
                f"Make sure TWS is open and API is enabled.\n"
                f"TWS > Edit > Global Configuration > API > Settings\n"
                f"  Enable ActiveX and Socket Clients\n"
                f"  Socket port: {TWS_PORT}\n"
                f"Original error: {e}"
            )
    return _ib


async def disconnect_ib():
    """Clean disconnect on server shutdown."""
    global _ib
    if _ib and _ib.isConnected():
        _ib.disconnect()
        log.info("Disconnected from TWS")
    _ib = None


# ═══════════════════════════════════════════════════════════════
# SECTION 3 — OPTIONS TOOLS
# ═══════════════════════════════════════════════════════════════

async def tool_get_options_chain(args: dict) -> str:
    """
    Fetch live options chain from TWS for a given ticker and expiry.
    Returns bid/ask/last/delta/gamma/theta/vega/IV/OI per strike.
    """
    ticker      = args['ticker'].upper()
    expiry_date = args.get('expiry_date')
    option_type = args.get('option_type', 'calls')
    n_strikes   = int(args.get('strikes_near_atm', DEFAULT_STRIKES_NEAR_ATM))

    ib = await get_ib()

    stock = Stock(ticker, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stock)
    [ticker_data] = await ib.reqTickersAsync(stock)
    spot = ticker_data.marketPrice()
    if not spot or spot != spot:
        spot = ticker_data.close or 0
    log.info(f"{ticker} spot price: {spot}")

    chains = await ib.reqSecDefOptParamsAsync(
        ticker, '', 'STK', stock.conId
    )
    chain = next(
        (c for c in chains if c.exchange == 'SMART'),
        chains[0] if chains else None
    )
    if not chain:
        return f"No options chain found for {ticker} on SMART exchange."

    all_expiries = sorted(chain.expirations)
    all_strikes  = sorted(chain.strikes)

    if expiry_date:
        target_exp = expiry_date.replace('-', '')
        if target_exp not in all_expiries:
            closest    = min(all_expiries, key=lambda e: abs(int(e) - int(target_exp)))
            target_exp = closest
    else:
        today  = datetime.now().strftime('%Y%m%d')
        future = [e for e in all_expiries if e >= today]
        target_exp = future[0] if future else all_expiries[0]

    exp_display = f"{target_exp[:4]}-{target_exp[4:6]}-{target_exp[6:]}"

    near_atm = sorted(all_strikes, key=lambda s: abs(s - spot))[:n_strikes * 2 + 1]
    near_atm = sorted(near_atm)

    rights = []
    if option_type in ('calls', 'both'):
        rights.append('C')
    if option_type in ('puts', 'both'):
        rights.append('P')

    contracts = [
        Option(ticker, target_exp, strike, right, 'SMART', tradingClass=ticker)
        for right in rights
        for strike in near_atm
    ]
    qualified = await ib.qualifyContractsAsync(*contracts)
    qualified = [c for c in qualified if c is not None]

    if not qualified:
        contracts2 = [
            Option(ticker, target_exp, strike, right, 'SMART')
            for right in rights
            for strike in near_atm
        ]
        qualified = await ib.qualifyContractsAsync(*contracts2)
        qualified = [c for c in qualified if c is not None]

    if not qualified:
        return (
            f"Could not qualify option contracts for {ticker} expiry {exp_display}.\n"
            f"Possible reasons:\n"
            f"  1. Market data subscription does not include options\n"
            f"  2. This expiry has no open contracts yet\n"
            f"  3. TWS paper account permissions\n"
            f"Try a different expiry date or check TWS account management."
        )

    tickers = await ib.reqTickersAsync(*qualified)
    await asyncio.sleep(1)

    lines = []
    lines.append(f"=== IBKR LIVE OPTIONS CHAIN: {ticker} ===")
    lines.append(f"Spot Price:  ${spot:.2f}  (real-time from TWS)")
    lines.append(f"Expiry:      {exp_display}")
    lines.append(f"Connection:  TWS port {TWS_PORT} "
                 f"({'PAPER' if TWS_PORT == 7497 else 'LIVE'})")
    lines.append(f"Timestamp:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(f"Available expiries (next 8): "
                 f"{', '.join(fmt_exp(e) for e in all_expiries[:8])}")
    lines.append("")

    calls_tickers = [t for t in tickers if t.contract.right == 'C']
    puts_tickers  = [t for t in tickers if t.contract.right == 'P']

    if option_type in ('calls', 'both'):
        lines.append("-- CALLS --")
        lines.append(fmt_chain_header())
        for t in sorted(calls_tickers, key=lambda x: x.contract.strike):
            lines.append(fmt_chain_row(t, spot))

    if option_type in ('puts', 'both'):
        lines.append("")
        lines.append("-- PUTS --")
        lines.append(fmt_chain_header())
        for t in sorted(puts_tickers, key=lambda x: x.contract.strike):
            lines.append(fmt_chain_row(t, spot))

    lines.append("")
    lines.append("Real-time data from your IBKR TWS session.")
    lines.append("Always confirm bid/ask in TWS before submitting an order.")
    return '\n'.join(lines)


async def tool_get_spread_analysis(args: dict) -> str:
    """
    Fetch live prices for two specific strikes and run the full
    V9.2 protocol audit: math + all checklist items.
    """
    ticker       = args['ticker'].upper()
    expiry_date  = args['expiry_date']
    long_strike  = float(args['long_strike'])
    short_strike = float(args['short_strike'])
    option_type  = args['option_type'].upper()
    contracts    = int(args.get('contracts', 2))

    if option_type.lower() in ('call', 'c'):
        right         = 'C'
        strategy_name = 'Bull Call DEBIT Spread'
    else:
        right         = 'P'
        strategy_name = 'Bull Put CREDIT Spread'

    ib = await get_ib()
    target_exp = expiry_date.replace('-', '')

    stock = Stock(ticker, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stock)
    [stock_ticker] = await ib.reqTickersAsync(stock)
    spot = stock_ticker.marketPrice()
    if not spot or spot != spot:
        spot = stock_ticker.close or 0

    long_contract  = Option(ticker, target_exp, long_strike,
                            right, 'SMART', tradingClass=ticker)
    short_contract = Option(ticker, target_exp, short_strike,
                            right, 'SMART', tradingClass=ticker)

    qualified = await ib.qualifyContractsAsync(long_contract, short_contract)
    qualified = [c for c in qualified if c is not None]

    if len(qualified) < 2:
        long_contract2  = Option(ticker, target_exp, long_strike,  right, 'SMART')
        short_contract2 = Option(ticker, target_exp, short_strike, right, 'SMART')
        qualified = await ib.qualifyContractsAsync(long_contract2, short_contract2)
        qualified = [c for c in qualified if c is not None]
        if len(qualified) >= 1:
            long_contract  = next(
                (c for c in qualified if c.strike == long_strike),  long_contract2)
            short_contract = next(
                (c for c in qualified if c.strike == short_strike), short_contract2)

    if len(qualified) < 2:
        return (
            f"Could not qualify both strikes for {ticker}.\n"
            f"Qualified {len(qualified)}/2 contracts.\n"
            f"Check that {expiry_date} is a valid expiry and "
            f"${long_strike}/${short_strike} are valid strikes."
        )

    tickers = await ib.reqTickersAsync(long_contract, short_contract)
    await asyncio.sleep(1.5)

    long_t  = next((t for t in tickers if t.contract.strike == long_strike),  None)
    short_t = next((t for t in tickers if t.contract.strike == short_strike), None)

    if not long_t or not short_t:
        return "Could not get market data for one or both legs."

    long_bid  = long_t.bid   or 0
    long_ask  = long_t.ask   or 0
    short_bid = short_t.bid  or 0
    short_ask = short_t.ask  or 0
    long_mid  = (long_bid  + long_ask)  / 2 if long_bid  and long_ask  else 0
    short_mid = (short_bid + short_ask) / 2 if short_bid and short_ask else 0

    long_greeks  = long_t.modelGreeks  or long_t.bidGreeks
    short_greeks = short_t.modelGreeks or short_t.bidGreeks

    long_delta  = long_greeks.delta       if long_greeks  else None
    long_gamma  = long_greeks.gamma       if long_greeks  else None
    long_theta  = long_greeks.theta       if long_greeks  else None
    long_vega   = long_greeks.vega        if long_greeks  else None
    long_iv     = long_greeks.impliedVol  if long_greeks  else None
    short_delta = short_greeks.delta      if short_greeks else None
    short_iv    = short_greeks.impliedVol if short_greeks else None

    spread_width = abs(short_strike - long_strike)

    if right == 'C':
        net_debit  = long_mid - short_mid
        max_loss   = net_debit * 100 * contracts
        max_profit = (spread_width - net_debit) * 100 * contracts
        breakeven  = long_strike + net_debit
    else:
        net_credit = short_mid - long_mid
        net_debit  = -net_credit
        max_loss   = (spread_width - net_credit) * 100 * contracts
        max_profit = net_credit * 100 * contracts
        breakeven  = short_strike - net_credit

    roi_pct  = (max_profit / abs(max_loss) * 100) if max_loss != 0 else 0
    tp_total = max_profit * 0.50

    today   = datetime.now().date()
    exp_dt  = datetime.strptime(expiry_date, '%Y-%m-%d').date()
    dte     = (exp_dt - today).days
    exit_dt = exp_dt - timedelta(days=21 if right == 'C' else 10)

    long_ba_spread  = long_ask  - long_bid  if long_bid  and long_ask  else 999
    short_ba_spread = short_ask - short_bid if short_bid and short_ask else 999
    avg_ba_spread   = (long_ba_spread + short_ba_spread) / 2
    net_premium     = abs(net_debit) if abs(net_debit) > 0 else 0.01
    ba_pct          = avg_ba_spread / net_premium * 100

    checks = [
        audit_check("Risk Cap < $250",
            abs(max_loss) <= 250,
            f"Max loss = ${abs(max_loss):.2f}"),
        audit_check("ROI Floor >= 40%",
            roi_pct >= 40,
            f"ROI = {roi_pct:.1f}%"),
        audit_check("Spread Width $2.50 or $5.00",
            spread_width in (2.5, 5.0),
            f"Width = ${spread_width:.2f}"),
        audit_check("Long Leg Delta 0.40-0.55",
            long_delta is not None and 0.40 <= abs(long_delta) <= 0.55,
            f"Delta = {long_delta:.3f}" if long_delta else "Delta = N/A"),
        audit_check("Bid-Ask < 5% of premium",
            ba_pct < 5.0,
            f"Bid-ask = {ba_pct:.1f}%"),
        audit_check("DTE in correct window",
            (30 <= dte <= 45) if right == 'C' else (10 <= dte <= 15),
            f"DTE = {dte} ({'target 30-45' if right == 'C' else 'target 10-15'})"),
        audit_check("Spread width not $1.00",
            spread_width != 1.0,
            "Width OK - not $1 wide"),
    ]

    all_pass = all(c['pass'] for c in checks)

    lines = []
    lines.append(f"=== IBKR LIVE SPREAD ANALYSIS: {ticker} - {strategy_name} ===")
    lines.append(f"Spot Price: ${spot:.2f}  |  Expiry: {expiry_date}  |  DTE: {dte}")
    lines.append(f"Data: Real-time from TWS port {TWS_PORT} "
                 f"({'PAPER' if TWS_PORT == 7497 else 'LIVE'})")
    lines.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("-- LEGS (LIVE IBKR PRICES) --")
    lines.append(
        f"BUY  {contracts}x  ${long_strike:<8.2f} {right}  |  "
        f"Bid: ${long_bid:<6.2f}  Ask: ${long_ask:<6.2f}  Mid: ${long_mid:<6.2f}  |  "
        f"Delta: {fmt_greek(long_delta):<7}  "
        f"Gamma: {fmt_greek(long_gamma):<7}  "
        f"Theta: {fmt_greek(long_theta):<7}  "
        f"IV: {fmt_iv(long_iv)}"
    )
    lines.append(
        f"SELL {contracts}x  ${short_strike:<8.2f} {right}  |  "
        f"Bid: ${short_bid:<6.2f}  Ask: ${short_ask:<6.2f}  Mid: ${short_mid:<6.2f}  |  "
        f"Delta: {fmt_greek(short_delta):<7}  "
        f"IV: {fmt_iv(short_iv)}"
    )
    lines.append("")
    lines.append("-- TRADE MATH --")
    if right == 'C':
        lines.append(
            f"Net Debit:   ${net_debit:.2f}/share  |  "
            f"${net_debit*100:.2f}/contract  |  "
            f"${net_debit*100*contracts:.2f} total ({contracts} contracts)"
        )
    else:
        lines.append(
            f"Net Credit:  ${abs(net_debit):.2f}/share  |  "
            f"${abs(net_debit)*100:.2f}/contract  |  "
            f"${abs(net_debit)*100*contracts:.2f} total ({contracts} contracts)"
        )
    lines.append(f"Max Risk:       ${abs(max_loss):.2f} total")
    lines.append(f"Max Profit:     ${max_profit:.2f} total")
    lines.append(f"ROI at max:     {roi_pct:.1f}%")
    lines.append(f"Breakeven:      ${breakeven:.2f} at expiry")
    lines.append(f"50% TP Target:  ${tp_total:.2f} total profit")
    lines.append(f"Spread Width:   ${spread_width:.2f}")
    lines.append(f"Bid-Ask Cost:   ~{ba_pct:.1f}% of net premium")
    lines.append("")
    lines.append("-- V9.2 PROTOCOL CHECKLIST --")
    for c in checks:
        icon = "PASS" if c['pass'] else "FAIL"
        lines.append(f"  [{icon}] {c['label']:<32}  {c['detail']}")
    lines.append("")
    if all_pass:
        lines.append("ALL CHECKS PASSED - Proceed to Astro analysis then execution ticket.")
    else:
        failed = [c['label'] for c in checks if not c['pass']]
        lines.append(f"{len(failed)} CHECK(S) FAILED: {', '.join(failed)}")
        lines.append("Review failed items above before proceeding.")
    lines.append("")
    lines.append("-- IBKR EXECUTION REFERENCE --")
    lines.append(
        f"Entry limit:    ${abs(net_debit):.2f} net "
        f"{'debit' if right == 'C' else 'credit'} (mid-price)"
    )
    lines.append(f"Mandatory exit: {exit_dt} "
                 f"({'21' if right == 'C' else '10'} DTE)")
    lines.append(f"Set GTC order:  ${tp_total:.2f} total profit immediately after fill")
    lines.append("")
    lines.append("Prices are real-time from your IBKR TWS session.")
    return '\n'.join(lines)


async def tool_get_stock_quote(args: dict) -> str:
    """
    Real-time price and IV snapshot for a single ticker.
    """
    ticker = args['ticker'].upper()
    ib     = await get_ib()

    stock = Stock(ticker, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stock)
    [tkr] = await ib.reqTickersAsync(stock)

    bid   = tkr.bid   or 0
    ask   = tkr.ask   or 0
    last  = tkr.last  or tkr.close or 0
    vol   = tkr.volume or 0
    spread_pct = ((ask - bid) / last * 100) if last > 0 else 0

    lines = []
    lines.append(f"=== REAL-TIME QUOTE: {ticker} ===")
    lines.append(f"Last:    ${last:.2f}")
    lines.append(f"Bid:     ${bid:.2f}  |  Ask: ${ask:.2f}  "
                 f"|  Spread: {spread_pct:.3f}%")
    lines.append(f"Volume:  {vol:,}")
    lines.append(f"Time:    {datetime.now().strftime('%H:%M:%S')}")
    lines.append(f"Source:  TWS port {TWS_PORT} "
                 f"({'PAPER' if TWS_PORT == 7497 else 'LIVE'})")
    return '\n'.join(lines)


async def tool_get_watchlist_scan(args: dict) -> str:
    """
    Quick real-time quote scan across all options watchlist tickers.
    Hot-reloads watchlist from watchlist.json on every call.
    Returns last price, spread, and volume for each.
    """
    ib    = await get_ib()
    current_watchlist = get_watchlist_symbols()  # hot-reload from JSON
    lines = []
    lines.append(f"=== OPTIONS WATCHLIST SCAN ({len(current_watchlist)} tickers) ===")
    lines.append(f"Time: {datetime.now().strftime('%H:%M:%S')}")
    lines.append("")
    lines.append(f"{'Ticker':<8} {'Last':>8} {'Bid':>8} {'Ask':>8} "
                 f"{'Spread%':>9} {'Volume':>12}")
    lines.append("-" * 60)

    for sym in current_watchlist:
        try:
            stock = Stock(sym, 'SMART', 'USD')
            await ib.qualifyContractsAsync(stock)
            [tkr] = await ib.reqTickersAsync(stock)
            bid   = tkr.bid   or 0
            ask   = tkr.ask   or 0
            last  = tkr.last  or tkr.close or 0
            vol   = tkr.volume or 0
            sp    = ((ask - bid) / last * 100) if last > 0 else 0
            lines.append(
                f"{sym:<8} ${last:>7.2f} ${bid:>7.2f} ${ask:>7.2f} "
                f"{sp:>8.3f}% {vol:>12,}"
            )
        except Exception as e:
            lines.append(f"{sym:<8}  Error: {e}")

    lines.append("")
    lines.append("Real-time data from your IBKR TWS session.")
    return '\n'.join(lines)


async def tool_get_iv_rank(args: dict) -> str:
    """
    Estimate IV Rank for a ticker using 52-week historical volatility data.
    Requires TWS historical data subscription.
    """
    ticker = args['ticker'].upper()
    ib     = await get_ib()

    stock = Stock(ticker, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stock)

    bars = await ib.reqHistoricalDataAsync(
        stock,
        endDateTime='',
        durationStr='1 Y',
        barSizeSetting='1 day',
        whatToShow='OPTION_IMPLIED_VOLATILITY',
        useRTH=True,
        formatDate=1
    )

    if not bars or len(bars) < 20:
        return (
            f"Insufficient IV history for {ticker}. "
            f"Check your TWS market data subscription includes "
            f"historical implied volatility."
        )

    iv_vals  = [b.close for b in bars if b.close and b.close > 0]
    iv_cur   = iv_vals[-1]
    iv_min   = min(iv_vals)
    iv_max   = max(iv_vals)
    iv_rank  = ((iv_cur - iv_min) / (iv_max - iv_min) * 100
                if iv_max != iv_min else 50)

    if iv_rank >= 50:
        regime = "HIGH IV - Favour credit spreads / selling premium"
    elif iv_rank >= 30:
        regime = "MEDIUM IV - Either direction OK"
    else:
        regime = "LOW IV - Favour debit spreads / buying premium"

    lines = []
    lines.append(f"=== IV RANK: {ticker} ===")
    lines.append(f"Current IV:  {iv_cur*100:.1f}%")
    lines.append(f"52-week low: {iv_min*100:.1f}%")
    lines.append(f"52-week high:{iv_max*100:.1f}%")
    lines.append(f"IV Rank:     {iv_rank:.1f}/100")
    lines.append(f"Regime:      {regime}")
    lines.append(f"Time:        {datetime.now().strftime('%H:%M:%S')}")
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 4 — STOCK MOMENTUM TOOLS  (30-45 min intraday holds)
# ═══════════════════════════════════════════════════════════════

async def tool_get_momentum_bars(args: dict) -> str:
    """
    Fetch 5-min or 15-min bars for a single ticker.
    Calculates EMA9/21/50, VWAP, RSI14, ATR14, MACD,
    Bollinger Bands and returns full bias picture for
    a 30-45 min intraday momentum trade.
    """
    ticker   = args['ticker'].upper()
    bar_size = args.get('bar_size', '5 mins')
    lookback = args.get('lookback', '1 D')

    ib = await get_ib()
    stock = Stock(ticker, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stock)

    bars = await ib.reqHistoricalDataAsync(
        stock,
        endDateTime='',
        durationStr=lookback,
        barSizeSetting=bar_size,
        whatToShow='TRADES',
        useRTH=True,
        formatDate=1
    )

    if not bars or len(bars) < 20:
        return (
            f"Insufficient bar data for {ticker} "
            f"({len(bars) if bars else 0} bars returned). "
            f"Market may be closed or data subscription issue."
        )

    closes  = [b.close  for b in bars]
    highs   = [b.high   for b in bars]
    lows    = [b.low    for b in bars]
    volumes = [b.volume for b in bars]

    ema9             = calc_ema(closes, 9)
    ema21            = calc_ema(closes, 21)
    ema50            = calc_ema(closes, 50)
    vwap             = calc_vwap(bars)
    rsi              = calc_rsi(closes, 14)
    atr              = calc_atr(highs, lows, closes, 14)
    _, _, histogram  = calc_macd(closes)
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes, 20, 2.0)

    curr    = closes[-1]
    e9      = ema9[-1]      if ema9      else None
    e21     = ema21[-1]     if ema21     else None
    e50     = ema50[-1]     if ema50     else None
    rsi_val = rsi[-1]       if rsi       else None
    atr_val = atr[-1]       if atr       else None
    macd_h  = histogram[-1] if histogram else None
    bb_u    = bb_upper[-1]  if bb_upper  else None
    bb_l    = bb_lower[-1]  if bb_lower  else None

    avg_vol   = sum(volumes[-21:-1]) / 20 if len(volumes) > 20 else 1
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 0

    bias, bias_strength = assess_bias(curr, e9, e21, e50, vwap, rsi_val, macd_h)

    lines = []
    lines.append(f"=== {ticker}  |  {bar_size} chart  |  "
                 f"{datetime.now().strftime('%H:%M:%S')} ===")
    lines.append("")
    lines.append(f"  Price:     ${curr:.2f}")
    if e9:
        lines.append(f"  EMA 9:    ${e9:.2f}  "
                     f"({'above' if curr > e9 else 'below'})")
    if e21:
        lines.append(f"  EMA 21:   ${e21:.2f}  "
                     f"({'above' if curr > e21 else 'below'})")
    if e50:
        lines.append(f"  EMA 50:   ${e50:.2f}  "
                     f"({'above' if curr > e50 else 'below'})")
    lines.append(f"  VWAP:     ${vwap:.2f}  "
                 f"({'above' if curr > vwap else 'below'})")
    if rsi_val:
        label = 'strong' if rsi_val > 60 else 'weak' if rsi_val < 40 else 'neutral'
        lines.append(f"  RSI 14:   {rsi_val:.1f}  ({label})")
    if atr_val:
        lines.append(f"  ATR 14:   ${atr_val:.3f}  (average bar range)")
    if macd_h is not None:
        lines.append(f"  MACD:     {'bullish' if macd_h > 0 else 'bearish'} "
                     f"histogram ({macd_h:.4f})")
    if bb_u and bb_l:
        lines.append(f"  BB Upper: ${bb_u:.2f}  |  BB Lower: ${bb_l:.2f}")
    lines.append(f"  Volume:   {vol_ratio:.1f}x average")
    lines.append("")
    lines.append(f"  BIAS: {bias}  [{bias_strength}]")
    lines.append("")

    if atr_val and e9:
        stop_dist    = round(atr_val * 1.2, 2)
        target1_dist = round(stop_dist * 1.5, 2)
        target2_dist = round(stop_dist * 2.5, 2)
        if 'BULL' in bias:
            lines.append("  Indicative levels (LONG):")
            lines.append(f"    Entry:   ${curr:.2f}")
            lines.append(f"    Stop:    ${curr - stop_dist:.2f}  "
                         f"(-${stop_dist:.2f}  |  1.2x ATR)")
            lines.append(f"    Target1: ${curr + target1_dist:.2f}  "
                         f"(+${target1_dist:.2f}  |  R:R 1.5:1)")
            lines.append(f"    Target2: ${curr + target2_dist:.2f}  "
                         f"(+${target2_dist:.2f}  |  R:R 2.5:1)")
        elif 'BEAR' in bias:
            lines.append("  Indicative levels (SHORT):")
            lines.append(f"    Entry:   ${curr:.2f}")
            lines.append(f"    Stop:    ${curr + stop_dist:.2f}  "
                         f"(+${stop_dist:.2f}  |  1.2x ATR)")
            lines.append(f"    Target1: ${curr - target1_dist:.2f}  "
                         f"(-${target1_dist:.2f}  |  R:R 1.5:1)")
            lines.append(f"    Target2: ${curr - target2_dist:.2f}  "
                         f"(-${target2_dist:.2f}  |  R:R 2.5:1)")

    return '\n'.join(lines)


async def tool_momentum_scan(args: dict) -> str:
    """
    Scan all 10 stock momentum watchlist tickers on 5-min bars.
    Checks QQQ for market bias first.
    Scores each ticker on: EMA stack, VWAP position, RSI,
    MACD histogram, volume surge.
    Returns top 3 setups with full position sizing for $5K account.
    """
    ib = await get_ib()

    # Step 1: QQQ market bias
    market_bias = 'NEUTRAL'
    try:
        qqq = Stock('QQQ', 'SMART', 'USD')
        await ib.qualifyContractsAsync(qqq)
        qqq_bars = await ib.reqHistoricalDataAsync(
            qqq, endDateTime='', durationStr='1 D',
            barSizeSetting='5 mins', whatToShow='TRADES',
            useRTH=True, formatDate=1
        )
        if qqq_bars and len(qqq_bars) >= 21:
            qc    = [b.close for b in qqq_bars]
            qe21  = calc_ema(qc, 21)
            qvwap = calc_vwap(qqq_bars)
            qrsi  = calc_rsi(qc, 14)
            qlast = qc[-1]
            if qe21 and qrsi:
                if qlast > qe21[-1] and qlast > qvwap and qrsi[-1] > 52:
                    market_bias = 'BULLISH'
                elif qlast < qe21[-1] and qlast < qvwap and qrsi[-1] < 48:
                    market_bias = 'BEARISH'
    except Exception as e:
        log.warning(f"QQQ bias check failed: {e}")

    # Step 2: Scan each ticker
    results = []

    for sym in SCALP_WATCHLIST:
        try:
            stock = Stock(sym, 'SMART', 'USD')
            await ib.qualifyContractsAsync(stock)

            [tkr] = await ib.reqTickersAsync(stock)
            bid   = tkr.bid   or 0
            ask   = tkr.ask   or 0
            last  = tkr.last  or tkr.close or 0
            if last <= 0:
                continue
            spread_pct = ((ask - bid) / last * 100) if last > 0 else 99

            bars = await ib.reqHistoricalDataAsync(
                stock, endDateTime='', durationStr='1 D',
                barSizeSetting='5 mins', whatToShow='TRADES',
                useRTH=True, formatDate=1
            )

            if not bars or len(bars) < 20:
                continue

            closes  = [b.close  for b in bars]
            highs   = [b.high   for b in bars]
            lows    = [b.low    for b in bars]
            volumes = [b.volume for b in bars]

            ema9            = calc_ema(closes, 9)
            ema21           = calc_ema(closes, 21)
            ema50           = calc_ema(closes, 50)
            vwap            = calc_vwap(bars)
            rsi             = calc_rsi(closes, 14)
            atr             = calc_atr(highs, lows, closes, 14)
            _, _, histogram = calc_macd(closes)

            if not (ema9 and ema21 and rsi and atr):
                continue

            curr    = closes[-1]
            e9      = ema9[-1]
            e21     = ema21[-1]
            e50     = ema50[-1] if ema50 else e21
            rsi_val = rsi[-1]
            atr_val = atr[-1]
            macd_h  = histogram[-1] if histogram else 0

            avg_vol   = sum(volumes[-21:-1]) / 20 if len(volumes) > 20 else 1
            vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 0

            direction = 'LONG' if curr > e21 else 'SHORT'
            score     = 0

            # Market bias filter
            if market_bias == 'BULLISH' and direction == 'SHORT':
                score -= 30
            elif market_bias == 'BEARISH' and direction == 'LONG':
                score -= 30

            # EMA stack (max 30)
            if direction == 'LONG':
                if curr > e9 > e21 > e50:
                    score += 30
                elif curr > e9 > e21:
                    score += 20
                elif curr > e21:
                    score += 10
            else:
                if curr < e9 < e21 < e50:
                    score += 30
                elif curr < e9 < e21:
                    score += 20
                elif curr < e21:
                    score += 10

            # VWAP (max 20)
            if direction == 'LONG' and curr > vwap:
                score += 20
            elif direction == 'SHORT' and curr < vwap:
                score += 20
            else:
                score -= 15

            # RSI (max 20)
            if direction == 'LONG':
                if 55 <= rsi_val <= 75:
                    score += 20
                elif 50 <= rsi_val < 55:
                    score += 10
                elif rsi_val > 75:
                    score += 5
                else:
                    score -= 10
            else:
                if 25 <= rsi_val <= 45:
                    score += 20
                elif 45 < rsi_val <= 50:
                    score += 10
                elif rsi_val < 25:
                    score += 5
                else:
                    score -= 10

            # MACD histogram (max 15)
            if direction == 'LONG' and macd_h > 0:
                score += 15
            elif direction == 'SHORT' and macd_h < 0:
                score += 15

            # Volume surge (max 15)
            if vol_ratio >= 2.5:
                score += 15
            elif vol_ratio >= 2.0:
                score += 10
            elif vol_ratio >= 1.5:
                score += 5

            # Spread penalty
            if spread_pct > 0.08:
                score -= 20
            elif spread_pct > 0.04:
                score -= 8

            # Position sizing
            stop_dist    = round(atr_val * 1.2, 2)
            target1_dist = round(stop_dist * 1.5, 2)
            target2_dist = round(stop_dist * 2.5, 2)
            max_position = ACCOUNT_SIZE * MAX_POSITION_PCT

            shares_by_pos  = int(max_position / ask) if ask > 0 else 0
            shares_by_risk = int(MAX_RISK_DOLLARS / stop_dist) if stop_dist > 0 else 0
            shares         = min(shares_by_pos, shares_by_risk, 500)

            dollar_risk   = round(shares * stop_dist + COMMISSION_RT, 2)
            dollar_profit = round(shares * target1_dist - COMMISSION_RT, 2)
            rr            = round(target1_dist / stop_dist, 2) if stop_dist > 0 else 0

            if direction == 'LONG':
                entry = ask
                stop  = round(entry - stop_dist, 2)
                tgt1  = round(entry + target1_dist, 2)
                tgt2  = round(entry + target2_dist, 2)
            else:
                entry = bid
                stop  = round(entry + stop_dist, 2)
                tgt1  = round(entry - target1_dist, 2)
                tgt2  = round(entry - target2_dist, 2)

            setup = identify_momentum_setup(
                bars, e9, e21, e50, vwap, rsi_val, macd_h, direction
            )

            results.append({
                'sym':          sym,
                'score':        score,
                'direction':    direction,
                'setup':        setup,
                'last':         curr,
                'bid':          bid,
                'ask':          ask,
                'entry':        entry,
                'stop':         stop,
                'tgt1':         tgt1,
                'tgt2':         tgt2,
                'shares':       shares,
                'dollar_risk':  dollar_risk,
                'dollar_profit':dollar_profit,
                'rr':           rr,
                'rsi':          rsi_val,
                'atr':          atr_val,
                'vol_ratio':    vol_ratio,
                'vwap':         vwap,
                'e9':           e9,
                'e21':          e21,
                'e50':          e50,
                'spread_pct':   spread_pct,
                'macd_h':       macd_h,
            })

        except Exception as e:
            log.warning(f"Scan error {sym}: {e}")
            continue

    results.sort(key=lambda x: x['score'], reverse=True)
    top3 = results[:3]

    bias_icon = 'BULLISH' if market_bias == 'BULLISH' else \
                'BEARISH' if market_bias == 'BEARISH' else 'NEUTRAL'

    lines = []
    lines.append("=== INTRADAY MOMENTUM SCAN  |  30-45 MIN HOLDS ===")
    lines.append(f"Time:            {datetime.now().strftime('%H:%M:%S ET')}")
    lines.append(f"Market bias:     QQQ {bias_icon}")
    lines.append(f"Account:         ${ACCOUNT_SIZE:,}  "
                 f"|  Max risk per trade: ${MAX_RISK_DOLLARS:.0f}")
    lines.append(f"Tickers scanned: {len(SCALP_WATCHLIST)}  "
                 f"|  Qualified: {len(results)}")
    lines.append("")

    for rank, r in enumerate(top3, 1):
        direction_label = r['direction']
        warn = ''
        if market_bias == 'BULLISH' and r['direction'] == 'SHORT':
            warn = '  [WARNING: AGAINST MARKET BIAS]'
        elif market_bias == 'BEARISH' and r['direction'] == 'LONG':
            warn = '  [WARNING: AGAINST MARKET BIAS]'

        lines.append("-" * 68)
        lines.append(
            f"#{rank}  {r['sym']}  {direction_label}  |  "
            f"Score: {r['score']}/100  |  {r['setup']}{warn}"
        )
        lines.append(
            f"     Price:   ${r['last']:.2f}  "
            f"(Bid ${r['bid']:.2f} / Ask ${r['ask']:.2f}  "
            f"Spread {r['spread_pct']:.3f}%)"
        )
        lines.append(
            f"     EMAs:    9=${r['e9']:.2f}  21=${r['e21']:.2f}  "
            f"50=${r['e50']:.2f}  |  VWAP=${r['vwap']:.2f}"
        )
        lines.append(
            f"     RSI:     {r['rsi']:.1f}  |  "
            f"MACD: {'up' if r['macd_h'] > 0 else 'down'} {r['macd_h']:.4f}  |  "
            f"Vol: {r['vol_ratio']:.1f}x avg  |  ATR: ${r['atr']:.3f}"
        )
        lines.append("")
        lines.append(f"     -- TRADE TICKET --")
        lines.append(f"     ENTRY:     ${r['entry']:.2f}  (limit order)")
        lines.append(f"     STOP:      ${r['stop']:.2f}  "
                     f"(${abs(r['entry']-r['stop']):.2f} away  |  1.2x ATR)")
        lines.append(f"     TARGET 1:  ${r['tgt1']:.2f}  "
                     f"(${abs(r['tgt1']-r['entry']):.2f} profit  |  R:R {r['rr']}:1)")
        lines.append(f"     TARGET 2:  ${r['tgt2']:.2f}  (stretch)")
        lines.append(f"     SHARES:    {r['shares']}  "
                     f"(${r['shares'] * r['entry']:.2f} position)")
        lines.append(f"     RISK:      ${r['dollar_risk']:.2f}  (incl. commissions)")
        lines.append(f"     PROFIT@T1: ${r['dollar_profit']:.2f}")
        lines.append(f"     HOLD MAX:  45 min - hard exit regardless")
        lines.append("")

    lines.append("-" * 68)
    lines.append("Set stop in TWS immediately after fill.")
    lines.append("Call trade_update at the 15-min mark for hold/exit decision.")
    lines.append("Max 2 trades per day. One position at a time.")
    lines.append("Confirm all prices in TWS before submitting.")
    return '\n'.join(lines)


async def tool_trade_update(args: dict) -> str:
    """
    Re-assess an open position at the 15-min check-in.
    Returns HOLD / TRAIL STOP / TAKE PARTIAL / EXIT NOW
    with updated stop level where applicable.
    """
    ticker       = args['ticker'].upper()
    entry_price  = float(args['entry_price'])
    direction    = args['direction'].upper()
    entry_stop   = float(args['stop_price'])
    shares       = int(args.get('shares', 100))
    minutes_held = int(args.get('minutes_held', 15))

    ib = await get_ib()
    stock = Stock(ticker, 'SMART', 'USD')
    await ib.qualifyContractsAsync(stock)

    [tkr] = await ib.reqTickersAsync(stock)
    curr  = tkr.last or tkr.close or 0

    bars = await ib.reqHistoricalDataAsync(
        stock, endDateTime='', durationStr='1 D',
        barSizeSetting='5 mins', whatToShow='TRADES',
        useRTH=True, formatDate=1
    )

    if not bars or len(bars) < 14:
        return f"Cannot get update data for {ticker}."

    closes = [b.close for b in bars]
    highs  = [b.high  for b in bars]
    lows   = [b.low   for b in bars]

    ema9            = calc_ema(closes, 9)
    ema21           = calc_ema(closes, 21)
    vwap            = calc_vwap(bars)
    rsi             = calc_rsi(closes, 14)
    atr             = calc_atr(highs, lows, closes, 14)
    _, _, histogram = calc_macd(closes)

    e9      = ema9[-1]      if ema9      else None
    e21     = ema21[-1]     if ema21     else None
    rsi_val = rsi[-1]       if rsi       else 50
    atr_val = atr[-1]       if atr       else 0.20
    macd_h  = histogram[-1] if histogram else 0

    if direction == 'LONG':
        pnl       = (curr - entry_price) * shares - COMMISSION_RT
        in_profit = curr > entry_price
    else:
        pnl       = (entry_price - curr) * shares - COMMISSION_RT
        in_profit = curr < entry_price

    pnl_pct        = (pnl / (entry_price * shares) * 100) if entry_price * shares > 0 else 0
    time_remaining = 45 - minutes_held

    decision = 'HOLD'
    reason   = []
    new_stop = entry_stop

    if minutes_held >= 40:
        decision = 'EXIT NOW'
        reason.append("45-min hard time limit approaching - close the position")
    elif direction == 'LONG':
        if e9 and curr < e9 and rsi_val < 45:
            decision = 'EXIT NOW'
            reason.append("Price broke below EMA9 and RSI is weakening")
        elif curr < vwap:
            decision = 'EXIT NOW'
            reason.append("Price fell below VWAP - uptrend broken")
        elif pnl >= MAX_RISK_DOLLARS * 1.5 and macd_h < 0:
            decision = 'TAKE PARTIAL'
            reason.append("At 1.5x profit target with weakening MACD - exit half now")
        elif pnl >= MAX_RISK_DOLLARS and in_profit:
            decision = 'TRAIL STOP'
            new_stop = round(curr - atr_val * 0.8, 2)
            reason.append(
                f"Move stop up to ${new_stop:.2f} (0.8x ATR below current price)"
            )
        elif e9 and e21 and curr > e9 > e21 and curr > vwap and rsi_val > 50:
            decision = 'HOLD'
            reason.append("Trend intact - price above EMA9 > EMA21 > VWAP, RSI healthy")
        else:
            decision = 'HOLD'
            reason.append("Mixed signals - hold but watch closely for VWAP loss")
    else:
        if e9 and curr > e9 and rsi_val > 55:
            decision = 'EXIT NOW'
            reason.append("Price broke above EMA9 and RSI is strengthening")
        elif curr > vwap:
            decision = 'EXIT NOW'
            reason.append("Price reclaimed VWAP - downtrend broken")
        elif pnl >= MAX_RISK_DOLLARS * 1.5 and macd_h > 0:
            decision = 'TAKE PARTIAL'
            reason.append("At 1.5x profit target with improving MACD - exit half now")
        elif pnl >= MAX_RISK_DOLLARS and in_profit:
            decision = 'TRAIL STOP'
            new_stop = round(curr + atr_val * 0.8, 2)
            reason.append(
                f"Move stop down to ${new_stop:.2f} (0.8x ATR above current price)"
            )
        elif e9 and e21 and curr < e9 < e21 and curr < vwap and rsi_val < 50:
            decision = 'HOLD'
            reason.append("Short trend intact - price below EMA9 < EMA21 < VWAP")
        else:
            decision = 'HOLD'
            reason.append("Mixed signals - hold but watch for VWAP reclaim")

    lines = []
    lines.append(f"=== TRADE UPDATE: {ticker}  {direction} ===")
    lines.append(f"Time held:    {minutes_held} min  |  Remaining: {time_remaining} min")
    lines.append(f"Entry:        ${entry_price:.2f}  |  "
                 f"Current: ${curr:.2f}  |  "
                 f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")
    if e9 and e21:
        lines.append(f"EMA9: ${e9:.2f}  EMA21: ${e21:.2f}  "
                     f"VWAP: ${vwap:.2f}  RSI: {rsi_val:.1f}")
    lines.append("")
    lines.append(f"DECISION: {decision}")
    for r in reason:
        lines.append(f"  -> {r}")
    if decision == 'TRAIL STOP':
        lines.append(f"  -> Update your stop in TWS to ${new_stop:.2f}")
    lines.append("")
    lines.append(f"Hard exit at 45 min regardless of outcome.")
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 5 — HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

# -- Options formatting helpers --------------------------------

# ═══════════════════════════════════════════════════════════════
# SECTION 4 — WATCHLIST MANAGEMENT TOOLS  (added v2.1)
# ═══════════════════════════════════════════════════════════════

async def tool_get_watchlist(args: dict) -> str:
    """Return the current watchlist contents and recent history."""
    data = load_watchlist_file()

    lines = []
    lines.append("=" * 60)
    lines.append(f"WATCHLIST: {data.get('watchlist_name', 'Unnamed')}")
    lines.append(f"Last updated: {data.get('last_updated', 'unknown')}")
    lines.append(f"Size: {len(data.get('tickers', []))}/{data.get('max_size', 12)}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("CURRENT TICKERS:")
    lines.append(f"{'#':<4}{'Symbol':<8}{'Tier':<10}{'Added':<14}{'Reason'}")
    lines.append("-" * 60)
    for i, t in enumerate(data.get("tickers", []), 1):
        sym = t.get("symbol", "?")
        tier = t.get("tier", "core")
        added = t.get("added_date", "?")
        reason = (t.get("added_reason", "") or "")[:35]
        lines.append(f"{i:<4}{sym:<8}{tier:<10}{added:<14}{reason}")
    lines.append("")

    history = data.get("history", [])
    if history:
        lines.append("RECENT HISTORY (last 10 entries):")
        lines.append("-" * 60)
        for h in history[-10:]:
            lines.append(f"  {h.get('date','?'):<12} "
                         f"{h.get('action','?'):<10} "
                         f"{h.get('symbol','?'):<8} "
                         f"{h.get('reason','')[:30]}")
    return "\n".join(lines)


async def tool_update_watchlist(args: dict) -> str:
    """
    Add or remove a ticker from the watchlist.
    REQUIRES confirmed=True to actually modify the file.
    Otherwise returns a dry-run preview.
    """
    action = args.get("action", "").lower()
    symbol = args.get("symbol", "").upper().strip()
    reason = args.get("reason", "No reason given")
    tier = args.get("tier", "core").lower()
    confirmed = args.get("confirmed", False)

    if action not in ("add", "remove"):
        return "ERROR: action must be 'add' or 'remove'"
    if not symbol:
        return "ERROR: symbol is required"
    if not confirmed:
        return (f"DRY RUN: Would {action} {symbol}.\n"
                f"Reason: {reason}\n"
                f"To execute, set confirmed=true in the tool call.")

    data = load_watchlist_file()
    tickers = data.get("tickers", [])
    history = data.get("history", [])
    today = datetime.now().strftime("%Y-%m-%d")

    if action == "add":
        if any(t["symbol"] == symbol for t in tickers):
            return f"NO CHANGE: {symbol} is already in the watchlist."
        if len(tickers) >= data.get("max_size", 12):
            return (f"BLOCKED: Watchlist is full ({data.get('max_size', 12)} max). "
                    f"Remove a ticker first.")
        tickers.append({
            "symbol": symbol,
            "added_date": today,
            "added_reason": reason,
            "tier": tier
        })
        history.append({
            "date": today, "action": "added",
            "symbol": symbol, "reason": reason
        })
        msg = f"ADDED: {symbol} (tier={tier})"
    else:  # remove
        before = len(tickers)
        tickers = [t for t in tickers if t["symbol"] != symbol]
        if len(tickers) == before:
            return f"NO CHANGE: {symbol} is not in the watchlist."
        if len(tickers) < data.get("min_size", 6):
            return (f"BLOCKED: Removing {symbol} would drop watchlist below "
                    f"min_size {data.get('min_size', 6)}. Add a replacement first.")
        history.append({
            "date": today, "action": "removed",
            "symbol": symbol, "reason": reason
        })
        msg = f"REMOVED: {symbol}"

    data["tickers"] = tickers
    data["history"] = history
    if not save_watchlist_file(data):
        return "ERROR: Could not save watchlist.json. Check file permissions."

    return (f"{msg}\n"
            f"Reason: {reason}\n"
            f"Watchlist size: {len(tickers)}/{data.get('max_size', 12)}\n"
            f"Changes apply immediately - no restart needed.")


async def tool_scan_full_universe(args: dict) -> str:
    """
    Weekly NASDAQ-100 scan to identify promotion candidates.
    Pulls live IBKR data for the full NDX universe and scores
    each name against the 4 promotion criteria.

    Default threshold is 'moderate' (3 of 4 criteria).
    Use 'strict' for all 4 required.
    """
    # NASDAQ-100 ticker list (current as of 2026 Q2)
    NDX_UNIVERSE = [
        "AAPL","ABNB","ADBE","ADI","ADP","ADSK","AEP","AMAT","AMD","AMGN",
        "AMZN","ANSS","APP","ARM","ASML","AVGO","AXON","AZN","BIIB","BKNG",
        "BKR","CCEP","CDNS","CDW","CEG","CHTR","CMCSA","COST","CPRT","CRWD",
        "CSCO","CSGP","CSX","CTAS","CTSH","DASH","DDOG","DLTR","DXCM","EA",
        "EXC","FANG","FAST","FTNT","GEHC","GFS","GILD","GOOG","GOOGL","HON",
        "IDXX","INTC","INTU","ISRG","KDP","KHC","KLAC","LIN","LRCX","LULU",
        "MAR","MCHP","MDB","MDLZ","MELI","META","MNST","MRVL","MSFT","MSTR",
        "MU","NFLX","NVDA","NXPI","ODFL","ON","ORLY","PANW","PAYX","PCAR",
        "PDD","PEP","PLTR","PYPL","QCOM","REGN","ROP","ROST","SBUX","SMCI",
        "SNPS","TEAM","TMUS","TSLA","TTD","TTWO","TXN","VRSK","VRTX","WBD",
        "WDAY","XEL","ZS"
    ]

    threshold = args.get("threshold", "moderate").lower()
    min_pass = 4 if threshold == "strict" else 3

    ib = await get_ib()
    today_data = load_watchlist_file()
    current_tickers = {t["symbol"] for t in today_data.get("tickers", [])}

    lines = []
    lines.append("=" * 70)
    lines.append(f"WEEKLY UNIVERSE SCAN (NASDAQ-100, {threshold} threshold)")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"Universe size: {len(NDX_UNIVERSE)} | "
                 f"Current watchlist: {len(current_tickers)}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"NOTE: Scoring against 4 criteria. Need >= {min_pass} to pass.")
    lines.append("  C1 = Above 50-day SMA AND 50-SMA above 200-SMA")
    lines.append("  C2 = 50-day return > QQQ 50-day return (RS positive)")
    lines.append("  C3 = Positive 20-day return (recent momentum)")
    lines.append("  C4 = Volume >= 1M avg daily (liquidity proxy for OI)")
    lines.append("")

    # Get QQQ benchmark for RS calculation
    try:
        qqq = Stock("QQQ", "SMART", "USD")
        await ib.qualifyContractsAsync(qqq)
        qqq_bars = await ib.reqHistoricalDataAsync(
            qqq, endDateTime="", durationStr="3 M",
            barSizeSetting="1 day", whatToShow="TRADES",
            useRTH=True, formatDate=1
        )
        qqq_50d_return = ((qqq_bars[-1].close / qqq_bars[-50].close) - 1) * 100 \
                         if len(qqq_bars) >= 50 else 0
    except Exception as e:
        return f"ERROR: Could not fetch QQQ benchmark: {e}"

    candidates = []
    skipped = 0

    for sym in NDX_UNIVERSE:
        try:
            stk = Stock(sym, "SMART", "USD")
            await ib.qualifyContractsAsync(stk)
            bars = await ib.reqHistoricalDataAsync(
                stk, endDateTime="", durationStr="1 Y",
                barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=True, formatDate=1
            )
            if len(bars) < 200:
                skipped += 1
                continue

            closes = [b.close for b in bars]
            volumes = [b.volume for b in bars]
            cmp_price = closes[-1]
            sma_50 = sum(closes[-50:]) / 50
            sma_200 = sum(closes[-200:]) / 200
            ret_50 = ((cmp_price / closes[-50]) - 1) * 100
            ret_20 = ((cmp_price / closes[-20]) - 1) * 100
            avg_vol = sum(volumes[-20:]) / 20

            c1 = cmp_price > sma_50 and sma_50 > sma_200
            c2 = ret_50 > qqq_50d_return
            c3 = ret_20 > 0
            c4 = avg_vol >= 1_000_000

            score = sum([c1, c2, c3, c4])
            rs = ret_50 - qqq_50d_return

            if score >= min_pass:
                candidates.append({
                    "symbol": sym, "score": score, "rs": rs,
                    "ret_20": ret_20, "ret_50": ret_50,
                    "in_watchlist": sym in current_tickers,
                    "c1": c1, "c2": c2, "c3": c3, "c4": c4
                })
        except Exception as e:
            log.warning(f"Skip {sym}: {e}")
            skipped += 1
            continue

    candidates.sort(key=lambda x: (x["score"], x["rs"]), reverse=True)

    promotions = [c for c in candidates if not c["in_watchlist"]][:10]
    in_wl = [c for c in candidates if c["in_watchlist"]]
    in_wl_syms = {c["symbol"] for c in in_wl}
    failing_wl = [s for s in current_tickers if s not in in_wl_syms]

    lines.append(f"QQQ 50-day return: {qqq_50d_return:+.1f}%")
    lines.append(f"Universe scanned: {len(NDX_UNIVERSE) - skipped}/{len(NDX_UNIVERSE)} "
                 f"({skipped} skipped due to data issues)")
    lines.append("")

    lines.append("CURRENT WATCHLIST HEALTH:")
    lines.append(f"  Passing threshold: {len(in_wl)}/{len(current_tickers)}")
    if failing_wl:
        lines.append(f"  WARNING - FAILING (consider demoting): {', '.join(failing_wl)}")
    else:
        lines.append("  OK - All current watchlist tickers passing")
    lines.append("")

    lines.append(f"TOP {len(promotions)} PROMOTION CANDIDATES:")
    lines.append(f"{'Rank':<6}{'Sym':<8}{'Score':<7}{'RS':<10}"
                 f"{'20d%':<10}{'50d%':<10}{'Criteria'}")
    lines.append("-" * 70)
    for i, c in enumerate(promotions, 1):
        crit = "".join(["Y" if c[k] else "N" for k in ("c1","c2","c3","c4")])
        lines.append(f"{i:<6}{c['symbol']:<8}{c['score']}/4    "
                     f"{c['rs']:+.1f}%    "
                     f"{c['ret_20']:+.1f}%    {c['ret_50']:+.1f}%    {crit}")
    lines.append("")
    lines.append("Next steps:")
    lines.append("  - Review failing watchlist names; use update_watchlist to remove")
    lines.append("  - Review promotion candidates; use update_watchlist to add")
    lines.append("  - All changes require confirmed=true to execute")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 4B — STOCK SWING TRADING TOOLS  (added v2.2)
# Used by: Swing Alpha project (separate from options spreads)
# ═══════════════════════════════════════════════════════════════

# ─── helpers used by swing tools ───────────────────────────────

def _calc_atr(bars, period=14):
    """Calculate ATR(period) from a list of bars with high/low/close."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l = bars[i].high, bars[i].low
        pc = bars[i-1].close
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    # simple moving avg of last 'period' TRs
    return sum(trs[-period:]) / period


def _calc_ema(values, period):
    """Calculate EMA series from a list of values."""
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    ema = [sum(values[:period]) / period]  # seed with SMA
    for v in values[period:]:
        ema.append(v * k + ema[-1] * (1 - k))
    # Pad front with Nones
    return [None] * (period - 1) + ema


def _find_swing_lows(bars, lookback=5):
    """Identify swing lows: bar where low < lows of N bars before AND after."""
    swing_lows = []
    for i in range(lookback, len(bars) - lookback):
        center_low = bars[i].low
        is_swing = all(bars[i-j].low >= center_low for j in range(1, lookback+1)) \
                   and all(bars[i+j].low >= center_low for j in range(1, lookback+1))
        if is_swing:
            swing_lows.append((i, center_low))
    return swing_lows


def _find_swing_highs(bars, lookback=5):
    """Identify swing highs."""
    swing_highs = []
    for i in range(lookback, len(bars) - lookback):
        center_high = bars[i].high
        is_swing = all(bars[i-j].high <= center_high for j in range(1, lookback+1)) \
                   and all(bars[i+j].high <= center_high for j in range(1, lookback+1))
        if is_swing:
            swing_highs.append((i, center_high))
    return swing_highs


def _detect_candle_pattern(o, h, l, c, prev_o, prev_c):
    """Identify last candle pattern. Returns (name, bullish_bias)."""
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    rng = h - l
    if rng <= 0:
        return ("Doji-degenerate", False)
    body_pct = body / rng
    is_bull = c > o
    is_bear = c < o

    # Doji: body < 10% of range
    if body_pct < 0.10:
        return ("Doji - indecision", False)

    # Hammer: small body near top, lower wick > 2x body, low upper wick
    if lower_wick > 2 * body and upper_wick < body and is_bull:
        return ("Hammer - bullish reversal at support", True)

    # Bullish Engulfing: large bull candle covers prior bear body
    if is_bull and prev_c < prev_o and o < prev_c and c > prev_o:
        return ("Bullish Engulfing - strong reversal signal", True)

    # Strong Bullish Marubozu: large bull, minimal wicks
    if is_bull and body_pct > 0.7:
        return ("Strong Bullish - buyers dominant", True)

    # Strong Bearish: large bear, minimal wicks
    if is_bear and body_pct > 0.7:
        return ("Strong Bearish - sellers dominant", False)

    # Shooting Star: small body bottom, upper wick > 2x body
    if upper_wick > 2 * body and lower_wick < body:
        return ("Shooting Star - bearish at resistance", False)

    return (f"{'Bullish' if is_bull else 'Bearish'} candle (no pattern)", is_bull)


# ─── tool implementations ──────────────────────────────────────

async def tool_get_swing_watchlist(args: dict) -> str:
    """Return the current SWING watchlist contents and recent history."""
    data = load_swing_watchlist_file()

    lines = []
    lines.append("=" * 60)
    lines.append(f"SWING WATCHLIST: {data.get('watchlist_name', 'Unnamed')}")
    lines.append(f"Last updated: {data.get('last_updated', 'unknown')}")
    lines.append(f"Size: {len(data.get('tickers', []))}/{data.get('max_size', 12)}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("CURRENT TICKERS:")
    lines.append(f"{'#':<4}{'Symbol':<8}{'Tier':<10}{'Added':<14}{'Reason'}")
    lines.append("-" * 60)
    for i, t in enumerate(data.get("tickers", []), 1):
        sym = t.get("symbol", "?")
        tier = t.get("tier", "core")
        added = t.get("added_date", "?")
        reason = (t.get("added_reason", "") or "")[:35]
        lines.append(f"{i:<4}{sym:<8}{tier:<10}{added:<14}{reason}")
    lines.append("")

    history = data.get("history", [])
    if history:
        lines.append("RECENT HISTORY (last 10 entries):")
        lines.append("-" * 60)
        for h in history[-10:]:
            lines.append(f"  {h.get('date','?'):<12} "
                         f"{h.get('action','?'):<10} "
                         f"{h.get('symbol','?'):<8} "
                         f"{h.get('reason','')[:30]}")
    return "\n".join(lines)


async def tool_update_swing_watchlist(args: dict) -> str:
    """
    Add or remove a ticker from the SWING watchlist.
    REQUIRES confirmed=True to actually modify the file.
    """
    action = args.get("action", "").lower()
    symbol = args.get("symbol", "").upper().strip()
    reason = args.get("reason", "No reason given")
    tier = args.get("tier", "core").lower()
    confirmed = args.get("confirmed", False)

    if action not in ("add", "remove"):
        return "ERROR: action must be 'add' or 'remove'"
    if not symbol:
        return "ERROR: symbol is required"
    if not confirmed:
        return (f"DRY RUN: Would {action} {symbol} from SWING watchlist.\n"
                f"Reason: {reason}\n"
                f"To execute, set confirmed=true in the tool call.")

    data = load_swing_watchlist_file()
    tickers = data.get("tickers", [])
    history = data.get("history", [])
    today = datetime.now().strftime("%Y-%m-%d")

    if action == "add":
        if any(t["symbol"] == symbol for t in tickers):
            return f"NO CHANGE: {symbol} is already in the swing watchlist."
        if len(tickers) >= data.get("max_size", 12):
            return (f"BLOCKED: Swing watchlist full ({data.get('max_size', 12)} max). "
                    f"Remove a ticker first.")
        tickers.append({
            "symbol": symbol, "added_date": today,
            "added_reason": reason, "tier": tier
        })
        history.append({"date": today, "action": "added",
                        "symbol": symbol, "reason": reason})
        msg = f"ADDED to swing watchlist: {symbol} (tier={tier})"
    else:
        before = len(tickers)
        tickers = [t for t in tickers if t["symbol"] != symbol]
        if len(tickers) == before:
            return f"NO CHANGE: {symbol} is not in the swing watchlist."
        if len(tickers) < data.get("min_size", 6):
            return (f"BLOCKED: Removing {symbol} would drop below "
                    f"min_size {data.get('min_size', 6)}. Add a replacement first.")
        history.append({"date": today, "action": "removed",
                        "symbol": symbol, "reason": reason})
        msg = f"REMOVED from swing watchlist: {symbol}"

    data["tickers"] = tickers
    data["history"] = history
    if not save_swing_watchlist_file(data):
        return "ERROR: Could not save swing_watchlist.json. Check file permissions."

    return (f"{msg}\n"
            f"Reason: {reason}\n"
            f"Swing watchlist size: {len(tickers)}/{data.get('max_size', 12)}\n"
            f"Changes apply immediately - no restart needed.")


async def tool_scan_swing_universe(args: dict) -> str:
    """
    Weekly NDX-100 scan for SWING TRADING promotion candidates.
    Different from options scan: emphasizes pullback-friendly
    characteristics (smooth trends, decent ADR, beta near 1+).
    """
    NDX_UNIVERSE = [
        "AAPL","ABNB","ADBE","ADI","ADP","ADSK","AEP","AMAT","AMD","AMGN",
        "AMZN","ANSS","APP","ARM","ASML","AVGO","AXON","AZN","BIIB","BKNG",
        "BKR","CCEP","CDNS","CDW","CEG","CHTR","CMCSA","COST","CPRT","CRWD",
        "CSCO","CSGP","CSX","CTAS","CTSH","DASH","DDOG","DLTR","DXCM","EA",
        "EXC","FANG","FAST","FTNT","GEHC","GFS","GILD","GOOG","GOOGL","HON",
        "IDXX","INTC","INTU","ISRG","KDP","KHC","KLAC","LIN","LRCX","LULU",
        "MAR","MCHP","MDB","MDLZ","MELI","META","MNST","MRVL","MSFT","MSTR",
        "MU","NFLX","NVDA","NXPI","ODFL","ON","ORLY","PANW","PAYX","PCAR",
        "PDD","PEP","PLTR","PYPL","QCOM","REGN","ROP","ROST","SBUX","SMCI",
        "SNPS","TEAM","TMUS","TSLA","TTD","TTWO","TXN","VRSK","VRTX","WBD",
        "WDAY","XEL","ZS"
    ]

    threshold = args.get("threshold", "moderate").lower()
    min_pass = 5 if threshold == "strict" else 4

    ib = await get_ib()
    today_data = load_swing_watchlist_file()
    current_tickers = {t["symbol"] for t in today_data.get("tickers", [])}

    lines = []
    lines.append("=" * 70)
    lines.append(f"SWING UNIVERSE SCAN (NASDAQ-100, {threshold} threshold)")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"Universe: {len(NDX_UNIVERSE)} | "
                 f"Current swing watchlist: {len(current_tickers)}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"NOTE: Scoring against 6 criteria. Need >= {min_pass} to pass.")
    lines.append("  C1 = Above 50-SMA AND 50-SMA above 200-SMA (uptrend)")
    lines.append("  C2 = 50-day return > QQQ 50-day return (relative strength)")
    lines.append("  C3 = Positive 20-day return (recent momentum)")
    lines.append("  C4 = Avg daily volume >= 2M (liquidity for shares)")
    lines.append("  C5 = ATR/price 1.5%-5% (tradeable volatility - not too tight, not chaotic)")
    lines.append("  C6 = No big gap (max 1-day move <= 8% in last 20 days)")
    lines.append("")

    # QQQ benchmark
    try:
        qqq = Stock("QQQ", "SMART", "USD")
        await ib.qualifyContractsAsync(qqq)
        qqq_bars = await ib.reqHistoricalDataAsync(
            qqq, endDateTime="", durationStr="3 M",
            barSizeSetting="1 day", whatToShow="TRADES",
            useRTH=True, formatDate=1
        )
        qqq_50d_return = ((qqq_bars[-1].close / qqq_bars[-50].close) - 1) * 100 \
                         if len(qqq_bars) >= 50 else 0
    except Exception as e:
        return f"ERROR: Could not fetch QQQ benchmark: {e}"

    candidates = []
    skipped = 0

    for sym in NDX_UNIVERSE:
        try:
            stk = Stock(sym, "SMART", "USD")
            await ib.qualifyContractsAsync(stk)
            bars = await ib.reqHistoricalDataAsync(
                stk, endDateTime="", durationStr="1 Y",
                barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=True, formatDate=1
            )
            if len(bars) < 200:
                skipped += 1
                continue

            closes = [b.close for b in bars]
            volumes = [b.volume for b in bars]
            cmp_price = closes[-1]
            sma_50 = sum(closes[-50:]) / 50
            sma_200 = sum(closes[-200:]) / 200
            ret_50 = ((cmp_price / closes[-50]) - 1) * 100
            ret_20 = ((cmp_price / closes[-20]) - 1) * 100
            avg_vol = sum(volumes[-20:]) / 20
            atr = _calc_atr(bars, 14)
            atr_pct = (atr / cmp_price * 100) if atr else 0

            # Max single-day move in last 20 days
            max_gap = max(
                abs((bars[-i].close / bars[-i-1].close) - 1) * 100
                for i in range(1, 21) if i < len(bars)
            )

            c1 = cmp_price > sma_50 and sma_50 > sma_200
            c2 = ret_50 > qqq_50d_return
            c3 = ret_20 > 0
            c4 = avg_vol >= 2_000_000
            c5 = 1.5 <= atr_pct <= 5.0
            c6 = max_gap <= 8.0

            score = sum([c1, c2, c3, c4, c5, c6])
            rs = ret_50 - qqq_50d_return

            if score >= min_pass:
                candidates.append({
                    "symbol": sym, "score": score, "rs": rs,
                    "ret_20": ret_20, "ret_50": ret_50,
                    "atr_pct": atr_pct,
                    "in_watchlist": sym in current_tickers,
                    "c1": c1, "c2": c2, "c3": c3,
                    "c4": c4, "c5": c5, "c6": c6
                })
        except Exception as e:
            log.warning(f"Skip {sym}: {e}")
            skipped += 1
            continue

    candidates.sort(key=lambda x: (x["score"], x["rs"]), reverse=True)

    promotions = [c for c in candidates if not c["in_watchlist"]][:10]
    in_wl = [c for c in candidates if c["in_watchlist"]]
    in_wl_syms = {c["symbol"] for c in in_wl}
    failing_wl = [s for s in current_tickers if s not in in_wl_syms]

    lines.append(f"QQQ 50-day return: {qqq_50d_return:+.1f}%")
    lines.append(f"Universe scanned: {len(NDX_UNIVERSE) - skipped}/{len(NDX_UNIVERSE)} "
                 f"({skipped} skipped)")
    lines.append("")

    lines.append("CURRENT SWING WATCHLIST HEALTH:")
    lines.append(f"  Passing threshold: {len(in_wl)}/{len(current_tickers)}")
    if failing_wl:
        lines.append(f"  WARNING - FAILING (consider demoting): {', '.join(failing_wl)}")
    else:
        lines.append("  OK - All current swing watchlist tickers passing")
    lines.append("")

    lines.append(f"TOP {len(promotions)} PROMOTION CANDIDATES:")
    lines.append(f"{'Rank':<6}{'Sym':<8}{'Score':<7}{'RS':<10}"
                 f"{'ATR%':<8}{'20d%':<10}{'50d%':<10}{'Criteria'}")
    lines.append("-" * 75)
    for i, c in enumerate(promotions, 1):
        crit = "".join(["Y" if c[k] else "N"
                        for k in ("c1","c2","c3","c4","c5","c6")])
        lines.append(f"{i:<6}{c['symbol']:<8}{c['score']}/6    "
                     f"{c['rs']:+.1f}%    "
                     f"{c['atr_pct']:.1f}%   "
                     f"{c['ret_20']:+.1f}%    {c['ret_50']:+.1f}%    {crit}")
    lines.append("")
    lines.append("Next steps: review failing names + top candidates,")
    lines.append("then use update_swing_watchlist with confirmed=true.")

    return "\n".join(lines)


async def tool_get_position_size(args: dict) -> str:
    """
    Calculate exact share count for a swing trade given:
      - account_size (default $5000)
      - risk_pct (default 1.0 = 1%)
      - entry_price
      - stop_price
    """
    account = args.get("account_size", 5000)
    risk_pct = args.get("risk_pct", 1.0)
    entry = args.get("entry_price")
    stop = args.get("stop_price")

    if entry is None or stop is None:
        return "ERROR: entry_price and stop_price are required"

    risk_dollars = account * (risk_pct / 100)
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return "ERROR: entry and stop cannot be equal"

    shares = int(risk_dollars / risk_per_share)
    capital_required = shares * entry
    capital_pct = capital_required / account * 100

    lines = []
    lines.append("=" * 50)
    lines.append("POSITION SIZE CALCULATOR")
    lines.append("=" * 50)
    lines.append(f"Account size:         ${account:,.2f}")
    lines.append(f"Risk per trade:       {risk_pct}% = ${risk_dollars:.2f}")
    lines.append(f"Entry price:          ${entry:.2f}")
    lines.append(f"Stop price:           ${stop:.2f}")
    lines.append(f"Risk per share:       ${risk_per_share:.2f}")
    lines.append("-" * 50)
    lines.append(f"SHARES TO BUY:        {shares}")
    lines.append(f"Capital required:     ${capital_required:,.2f}  ({capital_pct:.1f}% of account)")
    lines.append(f"Total risk if stop:   ${shares * risk_per_share:.2f}")
    lines.append("")
    if capital_pct > 50:
        lines.append("⚠️  WARNING: Position uses >50% of account capital.")
        lines.append("    Consider lower share count or different setup.")
    return "\n".join(lines)


async def tool_get_swing_setup(args: dict) -> str:
    """
    Full pullback/breakout analysis on a single ticker.
    Pulls 6 months of daily data, computes EMAs, ATR, swing
    structure, candle pattern, and outputs a complete setup
    assessment with entry/stop/target levels and position size.
    """
    ticker = args.get("ticker", "").upper()
    setup_type = args.get("setup_type", "pullback").lower()
    account = args.get("account_size", 5000)
    risk_pct = args.get("risk_pct", 1.0)

    if not ticker:
        return "ERROR: ticker is required"

    ib = await get_ib()
    try:
        stk = Stock(ticker, "SMART", "USD")
        await ib.qualifyContractsAsync(stk)
        bars = await ib.reqHistoricalDataAsync(
            stk, endDateTime="", durationStr="6 M",
            barSizeSetting="1 day", whatToShow="TRADES",
            useRTH=True, formatDate=1
        )
    except Exception as e:
        return f"ERROR: Could not fetch data for {ticker}: {e}"

    if len(bars) < 50:
        return f"ERROR: Insufficient data for {ticker} ({len(bars)} bars)"

    closes = [b.close for b in bars]
    cmp_price = closes[-1]

    # EMAs
    ema21 = _calc_ema(closes, 21)
    ema50 = _calc_ema(closes, 50)
    sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None

    # ATR
    atr = _calc_atr(bars, 14)
    atr_pct = (atr / cmp_price * 100) if atr else 0

    # Recent swing high (resistance / target)
    swing_highs = _find_swing_highs(bars, lookback=5)
    recent_swing_high = swing_highs[-1][1] if swing_highs else max(closes[-20:])

    # Recent swing low (invalidation / stop)
    swing_lows = _find_swing_lows(bars, lookback=5)
    recent_swing_low = swing_lows[-1][1] if swing_lows else min(closes[-20:])

    # Trend check
    above_50 = ema50[-1] is not None and cmp_price > ema50[-1]
    s50_above_200 = (sma200 is not None and ema50[-1] is not None
                     and ema50[-1] > sma200)
    in_uptrend = above_50 and s50_above_200

    # Last candle
    last = bars[-1]
    prev = bars[-2]
    pattern, bullish_pattern = _detect_candle_pattern(
        last.open, last.high, last.low, last.close,
        prev.open, prev.close
    )

    # Distance from EMAs
    dist_21 = (cmp_price - ema21[-1]) / ema21[-1] * 100 if ema21[-1] else 0
    dist_50 = (cmp_price - ema50[-1]) / ema50[-1] * 100 if ema50[-1] else 0

    # Pullback validation (for pullback setup)
    # 1. Recent swing high in last 15 bars
    # 2. Pullback at least 3 days from that high
    # 3. Pullback retracement 3-8% from high
    is_pullback = False
    pullback_pct = 0
    days_into_pullback = 0
    if swing_highs:
        last_high_idx, last_high_val = swing_highs[-1]
        bars_since_high = len(bars) - 1 - last_high_idx
        if bars_since_high >= 3 and bars_since_high <= 15:
            pullback_pct = (last_high_val - cmp_price) / last_high_val * 100
            if 3.0 <= pullback_pct <= 10.0:
                is_pullback = True
                days_into_pullback = bars_since_high

    # Volume check on pullback (volume should be lower on pullback)
    volume_check = "N/A"
    if is_pullback and last_high_idx is not None:
        adv_vol = (sum(b.volume for b in bars[max(0, last_high_idx-5):last_high_idx]) / 5
                   if last_high_idx >= 5 else 0)
        pullback_vol = sum(b.volume for b in bars[last_high_idx+1:]) / max(1, bars_since_high)
        if pullback_vol < adv_vol:
            volume_check = "✓ Lower volume on pullback (healthy)"
        else:
            volume_check = "✗ Higher volume on pullback (warning)"

    # Compute entry / stop / target
    if setup_type == "pullback" and is_pullback:
        entry_price = round(last.high + 0.01, 2)
        stop_price = round(min(recent_swing_low, cmp_price - 1.5 * atr), 2) if atr else recent_swing_low
        risk_per_share = entry_price - stop_price
        t1 = round(entry_price + 2 * risk_per_share, 2)
        t2 = round(entry_price + 4 * risk_per_share, 2)
    elif setup_type == "breakout":
        entry_price = round(recent_swing_high + 0.05, 2)
        stop_price = round(recent_swing_high - 1.5 * atr, 2) if atr else recent_swing_high * 0.97
        risk_per_share = entry_price - stop_price
        t1 = round(entry_price + 2 * risk_per_share, 2)
        t2 = round(entry_price + 4 * risk_per_share, 2)
    else:
        entry_price = round(last.high + 0.01, 2)
        stop_price = round(min(recent_swing_low, cmp_price - 1.5 * atr), 2) if atr else recent_swing_low
        risk_per_share = entry_price - stop_price
        t1 = round(entry_price + 2 * risk_per_share, 2)
        t2 = round(entry_price + 4 * risk_per_share, 2)

    # Position sizing
    risk_dollars = account * (risk_pct / 100)
    shares = int(risk_dollars / risk_per_share) if risk_per_share > 0 else 0
    capital = shares * entry_price
    capital_pct = capital / account * 100 if account > 0 else 0

    # Build output
    lines = []
    lines.append("=" * 65)
    lines.append(f"SWING SETUP ANALYSIS: {ticker} ({setup_type.upper()})")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("=" * 65)
    lines.append("")
    lines.append("STRUCTURE")
    lines.append(f"  Current Price:    ${cmp_price:.2f}")
    lines.append(f"  21-EMA:           ${ema21[-1]:.2f}  (price {dist_21:+.1f}% vs EMA)")
    lines.append(f"  50-EMA:           ${ema50[-1]:.2f}  (price {dist_50:+.1f}% vs EMA)")
    if sma200:
        lines.append(f"  200-SMA:          ${sma200:.2f}")
    lines.append(f"  ATR(14):          ${atr:.2f}  ({atr_pct:.1f}% of price)")
    lines.append(f"  Recent Swing High: ${recent_swing_high:.2f}")
    lines.append(f"  Recent Swing Low:  ${recent_swing_low:.2f}")
    lines.append("")

    lines.append("TREND ASSESSMENT")
    lines.append(f"  Above 50-EMA?              {'YES ✓' if above_50 else 'NO ✗'}")
    lines.append(f"  50-EMA above 200-SMA?      {'YES ✓' if s50_above_200 else 'NO ✗'}")
    lines.append(f"  Confirmed uptrend?         {'YES ✓' if in_uptrend else 'NO ✗ — skip this trade'}")
    lines.append("")

    if setup_type == "pullback":
        lines.append("PULLBACK VALIDATION")
        lines.append(f"  Days into pullback:        {days_into_pullback}")
        lines.append(f"  Pullback depth:            {pullback_pct:.1f}% from swing high")
        lines.append(f"  Valid pullback?            {'YES ✓' if is_pullback else 'NO ✗'}")
        lines.append(f"  Volume check:              {volume_check}")
    lines.append("")

    lines.append(f"LAST CANDLE: {pattern}")
    lines.append(f"  O=${last.open:.2f} H=${last.high:.2f} "
                 f"L=${last.low:.2f} C=${last.close:.2f}")
    lines.append("")

    lines.append("PROPOSED ENTRY PLAN")
    lines.append(f"  Entry trigger:    BUY STOP at ${entry_price:.2f} "
                 f"(above today's high)")
    lines.append(f"  Stop loss:        ${stop_price:.2f}")
    lines.append(f"  Risk per share:   ${risk_per_share:.2f}")
    lines.append(f"  Target T1 (+2R):  ${t1:.2f}  → close 50% here")
    lines.append(f"  Target T2 (+4R):  ${t2:.2f}  → trail remaining 50%")
    lines.append("")

    lines.append("POSITION SIZING")
    lines.append(f"  Account: ${account} | Risk: {risk_pct}% = ${risk_dollars:.2f}")
    lines.append(f"  SHARES TO BUY:    {shares}")
    lines.append(f"  Capital required: ${capital:,.2f}  ({capital_pct:.1f}% of account)")
    lines.append(f"  Total risk:       ${shares * risk_per_share:.2f}")
    lines.append("")

    # Verdict
    verdict = "SKIP"
    reasons = []
    if not in_uptrend:
        reasons.append("not in confirmed uptrend")
    if setup_type == "pullback" and not is_pullback:
        reasons.append("not a valid pullback")
    if not bullish_pattern:
        reasons.append("no bullish reversal candle")
    if capital_pct > 60:
        reasons.append(f"position too large ({capital_pct:.0f}% capital)")

    if not reasons:
        verdict = "TRADEABLE ✓"
    elif len(reasons) == 1 and reasons[0] == "no bullish reversal candle":
        verdict = "WAIT for confirmation candle"

    lines.append(f"VERDICT: {verdict}")
    if reasons:
        lines.append(f"  Reasons: {', '.join(reasons)}")

    return "\n".join(lines)


async def tool_scan_pullback_setups(args: dict) -> str:
    """
    Scan the SWING watchlist for tickers currently in tradeable
    pullback setups. Reports which names are setting up RIGHT
    NOW vs which are extended, breaking down, or just sideways.
    """
    account = args.get("account_size", 5000)
    risk_pct = args.get("risk_pct", 1.0)

    ib = await get_ib()
    swing_tickers = get_swing_watchlist_symbols()

    lines = []
    lines.append("=" * 70)
    lines.append(f"PULLBACK SCANNER — Swing Watchlist ({len(swing_tickers)} tickers)")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("=" * 70)
    lines.append("")

    tradeable = []
    monitoring = []
    blocked = []

    for sym in swing_tickers:
        try:
            stk = Stock(sym, "SMART", "USD")
            await ib.qualifyContractsAsync(stk)
            bars = await ib.reqHistoricalDataAsync(
                stk, endDateTime="", durationStr="6 M",
                barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=True, formatDate=1
            )
            if len(bars) < 50:
                blocked.append((sym, "insufficient data"))
                continue

            closes = [b.close for b in bars]
            cmp_price = closes[-1]
            ema50 = _calc_ema(closes, 50)
            sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
            atr = _calc_atr(bars, 14)

            above_50 = ema50[-1] is not None and cmp_price > ema50[-1]
            s50_above_200 = (sma200 is not None and ema50[-1] is not None
                             and ema50[-1] > sma200)
            in_uptrend = above_50 and s50_above_200

            if not in_uptrend:
                blocked.append((sym, "not in uptrend"))
                continue

            swing_highs = _find_swing_highs(bars, lookback=5)
            swing_lows = _find_swing_lows(bars, lookback=5)
            if not swing_highs:
                monitoring.append((sym, "no recent swing high"))
                continue

            last_high_idx, last_high_val = swing_highs[-1]
            bars_since_high = len(bars) - 1 - last_high_idx
            pullback_pct = (last_high_val - cmp_price) / last_high_val * 100

            last = bars[-1]
            prev = bars[-2]
            pattern, bullish = _detect_candle_pattern(
                last.open, last.high, last.low, last.close,
                prev.open, prev.close
            )

            if bars_since_high < 3:
                monitoring.append((sym, f"extended (only {bars_since_high}d from high)"))
            elif bars_since_high > 15:
                monitoring.append((sym, "pullback too old, look for new structure"))
            elif pullback_pct < 3.0:
                monitoring.append((sym, f"shallow pullback ({pullback_pct:.1f}%)"))
            elif pullback_pct > 10.0:
                blocked.append((sym, f"deep pullback ({pullback_pct:.1f}%) — caution"))
            elif not bullish:
                monitoring.append((sym, f"day {bars_since_high}, "
                                        f"-{pullback_pct:.1f}%, awaiting reversal candle"))
            else:
                # TRADEABLE
                recent_swing_low = swing_lows[-1][1] if swing_lows else min(closes[-20:])
                entry = round(last.high + 0.01, 2)
                stop = round(min(recent_swing_low, cmp_price - 1.5 * atr), 2) if atr else recent_swing_low
                risk_per_share = entry - stop
                t1 = round(entry + 2 * risk_per_share, 2)
                shares = int((account * risk_pct / 100) / risk_per_share) if risk_per_share > 0 else 0
                tradeable.append({
                    "sym": sym, "entry": entry, "stop": stop, "t1": t1,
                    "risk": risk_per_share, "shares": shares,
                    "pullback_pct": pullback_pct, "days": bars_since_high,
                    "pattern": pattern
                })
        except Exception as e:
            blocked.append((sym, f"error: {e}"))
            continue

    if tradeable:
        lines.append("✓ TRADEABLE PULLBACK SETUPS:")
        lines.append("-" * 70)
        for t in tradeable:
            lines.append(f"  {t['sym']:<6}  Day {t['days']} of pullback "
                         f"(-{t['pullback_pct']:.1f}%)")
            lines.append(f"          Entry ${t['entry']}  Stop ${t['stop']}  "
                         f"T1 ${t['t1']}  Shares: {t['shares']}")
            lines.append(f"          Pattern: {t['pattern']}")
            lines.append("")
    else:
        lines.append("✓ TRADEABLE: None right now")
        lines.append("")

    if monitoring:
        lines.append("⚠ MONITORING (not yet ready):")
        for sym, reason in monitoring:
            lines.append(f"  {sym:<6}  {reason}")
        lines.append("")

    if blocked:
        lines.append("✗ BLOCKED:")
        for sym, reason in blocked:
            lines.append(f"  {sym:<6}  {reason}")

    return "\n".join(lines)


async def tool_get_open_positions(args: dict) -> str:
    """
    Return live count of current open positions from the IBKR
    account. Used to enforce the 5-position maximum across both
    options spreads and swing trades.

    Returns:
      - Total count
      - Breakdown by instrument type (stock vs option)
      - Detail list of each position (symbol, qty, side)
    """
    ib = await get_ib()
    try:
        positions = ib.positions()
    except Exception as e:
        return f"ERROR: Could not fetch positions from IBKR: {e}"

    if not positions:
        return ("=" * 50 + "\n"
                "OPEN POSITIONS: 0\n"
                "=" * 50 + "\n"
                "No open positions in account.\n"
                "Full risk budget available for new entries.\n"
                f"Max allowed: 5 simultaneous positions")

    stocks = []
    options = []
    for pos in positions:
        if pos.position == 0:
            continue  # skip zero-quantity ghost entries
        c = pos.contract
        if c.secType == "STK":
            stocks.append({
                "symbol": c.symbol,
                "qty": pos.position,
                "avg_cost": pos.avgCost
            })
        elif c.secType == "OPT":
            options.append({
                "symbol": c.symbol,
                "qty": pos.position,
                "strike": c.strike,
                "right": c.right,
                "expiry": c.lastTradeDateOrContractMonth,
                "avg_cost": pos.avgCost
            })

    # Group options by underlying — multi-leg spreads count as ONE position
    options_by_underlying = {}
    for o in options:
        sym = o["symbol"]
        options_by_underlying.setdefault(sym, []).append(o)

    spread_count = len(options_by_underlying)
    stock_count = len(stocks)
    total_positions = spread_count + stock_count

    lines = []
    lines.append("=" * 50)
    lines.append(f"OPEN POSITIONS: {total_positions}")
    lines.append(f"  Stock positions:    {stock_count}")
    lines.append(f"  Option spreads:     {spread_count}")
    lines.append(f"  (multi-leg counted as 1)")
    lines.append("=" * 50)

    if stocks:
        lines.append("\nSTOCK POSITIONS (swing trades):")
        for s in stocks:
            lines.append(f"  {s['symbol']:<6} qty={int(s['qty']):>4}  "
                         f"avg ${s['avg_cost']:.2f}")

    if options_by_underlying:
        lines.append("\nOPTION SPREADS (options trades):")
        for sym, legs in options_by_underlying.items():
            lines.append(f"  {sym}: {len(legs)}-leg spread")
            for o in legs:
                side = "LONG" if o["qty"] > 0 else "SHORT"
                lines.append(f"     {side} {abs(int(o['qty']))}x "
                             f"${o['strike']:.2f} {o['right']} "
                             f"exp {o['expiry']}")

    lines.append("")
    lines.append(f"Max allowed: 5 simultaneous positions")
    if total_positions >= 5:
        lines.append("STATUS: AT CAP — no new entries allowed until close")
    elif total_positions == 4:
        lines.append("STATUS: WARNING — only 1 slot remaining")
    else:
        lines.append(f"STATUS: OK — {5 - total_positions} slots available")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 4C — BEAR OPTIONS TOOLS  (added v2.4)
# Used by: Options Alpha V9.9 (bidirectional options trading)
# ═══════════════════════════════════════════════════════════════

async def tool_get_bear_watchlist(args: dict) -> str:
    """Return the current BEAR OPTIONS watchlist contents and history."""
    data = load_bear_watchlist_file()
    lines = []
    lines.append("=" * 60)
    lines.append(f"BEAR WATCHLIST: {data.get('watchlist_name', 'Unnamed')}")
    lines.append(f"Last updated: {data.get('last_updated', 'unknown')}")
    lines.append(f"Size: {len(data.get('tickers', []))}/{data.get('max_size', 12)}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("CURRENT TICKERS (bearish put spread candidates):")
    lines.append(f"{'#':<4}{'Symbol':<8}{'Tier':<10}{'Added':<14}{'Reason'}")
    lines.append("-" * 60)
    for i, t in enumerate(data.get("tickers", []), 1):
        sym = t.get("symbol", "?")
        tier = t.get("tier", "core")
        added = t.get("added_date", "?")
        reason = (t.get("added_reason", "") or "")[:35]
        lines.append(f"{i:<4}{sym:<8}{tier:<10}{added:<14}{reason}")
    lines.append("")
    history = data.get("history", [])
    if history:
        lines.append("RECENT HISTORY (last 10 entries):")
        lines.append("-" * 60)
        for h in history[-10:]:
            lines.append(f"  {h.get('date','?'):<12} "
                         f"{h.get('action','?'):<10} "
                         f"{h.get('symbol','?'):<8} "
                         f"{h.get('reason','')[:30]}")
    return "\n".join(lines)


async def tool_update_bear_watchlist(args: dict) -> str:
    """
    Add or remove a ticker from the BEAR OPTIONS watchlist.
    REQUIRES confirmed=True to actually modify the file.
    """
    action = args.get("action", "").lower()
    symbol = args.get("symbol", "").upper().strip()
    reason = args.get("reason", "No reason given")
    tier = args.get("tier", "core").lower()
    confirmed = args.get("confirmed", False)

    if action not in ("add", "remove"):
        return "ERROR: action must be 'add' or 'remove'"
    if not symbol:
        return "ERROR: symbol is required"
    if not confirmed:
        return (f"DRY RUN: Would {action} {symbol} from BEAR watchlist.\n"
                f"Reason: {reason}\n"
                f"To execute, set confirmed=true in the tool call.")

    data = load_bear_watchlist_file()
    tickers = data.get("tickers", [])
    history = data.get("history", [])
    today = datetime.now().strftime("%Y-%m-%d")

    if action == "add":
        if any(t["symbol"] == symbol for t in tickers):
            return f"NO CHANGE: {symbol} is already in the bear watchlist."
        if len(tickers) >= data.get("max_size", 12):
            return (f"BLOCKED: Bear watchlist full ({data.get('max_size', 12)} max). "
                    f"Remove a ticker first.")
        tickers.append({
            "symbol": symbol, "added_date": today,
            "added_reason": reason, "tier": tier
        })
        history.append({"date": today, "action": "added",
                        "symbol": symbol, "reason": reason})
        msg = f"ADDED to bear watchlist: {symbol}"
    else:
        before = len(tickers)
        tickers = [t for t in tickers if t["symbol"] != symbol]
        if len(tickers) == before:
            return f"NO CHANGE: {symbol} is not in the bear watchlist."
        if len(tickers) < data.get("min_size", 6):
            return (f"BLOCKED: Removing {symbol} would drop below "
                    f"min_size {data.get('min_size', 6)}. Add a replacement first.")
        history.append({"date": today, "action": "removed",
                        "symbol": symbol, "reason": reason})
        msg = f"REMOVED from bear watchlist: {symbol}"

    data["tickers"] = tickers
    data["history"] = history
    if not save_bear_watchlist_file(data):
        return "ERROR: Could not save bear_watchlist.json. Check file permissions."

    return (f"{msg}\n"
            f"Reason: {reason}\n"
            f"Bear watchlist size: {len(tickers)}/{data.get('max_size', 12)}\n"
            f"Changes apply immediately - no restart needed.")


async def tool_scan_combined_setups(args: dict) -> str:
    """
    UNIFIED daily scan for Options Alpha V9.9.
    Scores ALL names across both the bullish watchlist (10 names)
    and bearish watchlist (10 names) using the 14-point weighted
    scorecard. Both directional setups are evaluated for every name.
    Same-ticker conflict: only the higher-scoring direction is kept.
    Returns the top 3 ranked setups regardless of direction.

    This is the PRIMARY daily analysis tool — replaces running
    separate bullish and bearish scans.
    """
    ib = await get_ib()
    bull_tickers = get_watchlist_symbols()
    bear_tickers = get_bear_watchlist_symbols()

    # Build full candidate set (unique symbols, tagged with eligible directions)
    # A symbol on both lists is eligible for both directions
    all_symbols = {}
    for sym in bull_tickers:
        all_symbols.setdefault(sym, set()).add("bull")
    for sym in bear_tickers:
        all_symbols.setdefault(sym, set()).add("bear")

    lines = []
    lines.append("=" * 70)
    lines.append("UNIFIED SETUP SCAN — Options Alpha V9.9")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Bull watchlist: {len(bull_tickers)} | "
                 f"Bear watchlist: {len(bear_tickers)} | "
                 f"Unique names: {len(all_symbols)}")
    lines.append("=" * 70)
    lines.append("")

    # Get QQQ regime data
    try:
        qqq_stk = Stock("QQQ", "SMART", "USD")
        await ib.qualifyContractsAsync(qqq_stk)
        qqq_bars = await ib.reqHistoricalDataAsync(
            qqq_stk, endDateTime="", durationStr="1 Y",
            barSizeSetting="1 day", whatToShow="TRADES",
            useRTH=True, formatDate=1
        )
        qqq_closes = [b.close for b in qqq_bars]
        qqq_cmp = qqq_closes[-1]
        qqq_sma50 = sum(qqq_closes[-50:]) / 50
        qqq_sma200 = sum(qqq_closes[-200:]) / 200
        qqq_50d_ret = ((qqq_cmp / qqq_closes[-50]) - 1) * 100
        qqq_slope_rising = qqq_sma50 > sum(qqq_closes[-70:-50]) / 20

        # ADX proxy: compare recent avg range to prior avg range
        recent_ranges = [abs(qqq_bars[-i].high - qqq_bars[-i].low)
                         for i in range(1, 15)]
        adx_proxy = sum(recent_ranges) / len(recent_ranges)

        if (qqq_cmp > qqq_sma50 and qqq_sma50 > qqq_sma200
                and qqq_slope_rising):
            regime = "🟢 STRONG UPTREND"
            regime_bull_pts = 3.0
            regime_bear_pts = 0.5  # bears harder in strong uptrend
        elif qqq_cmp > qqq_sma50 and qqq_sma50 > qqq_sma200:
            regime = "🟡 WEAK UPTREND"
            regime_bull_pts = 1.5
            regime_bear_pts = 1.0
        elif qqq_cmp < qqq_sma50 and qqq_sma50 < qqq_sma200:
            regime = "🔴 DOWNTREND"
            regime_bull_pts = 0.5
            regime_bear_pts = 3.0  # bears score full pts in downtrend
        else:
            regime = "🟠 NEUTRAL/TRANSITION"
            regime_bull_pts = 1.0
            regime_bear_pts = 1.5
    except Exception as e:
        return f"ERROR: Could not fetch QQQ regime data: {e}"

    lines.append(f"Regime: {regime}")
    lines.append(f"QQQ: ${qqq_cmp:.2f} | 50-SMA: ${qqq_sma50:.2f} | "
                 f"200-SMA: ${qqq_sma200:.2f}")
    lines.append("")

    # Score each candidate
    scored = []
    skipped = 0

    for sym, directions in all_symbols.items():
        try:
            stk = Stock(sym, "SMART", "USD")
            await ib.qualifyContractsAsync(stk)
            bars = await ib.reqHistoricalDataAsync(
                stk, endDateTime="", durationStr="6 M",
                barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=True, formatDate=1
            )
            if len(bars) < 50:
                skipped += 1
                continue

            closes = [b.close for b in bars]
            cmp = closes[-1]
            ema21 = _calc_ema(closes, 21)[-1] or cmp
            ema50 = _calc_ema(closes, 50)[-1] or cmp
            sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
            atr = _calc_atr(bars, 14) or (cmp * 0.02)
            ret_20 = ((cmp / closes[-20]) - 1) * 100 if len(closes) >= 20 else 0
            ret_50 = ((cmp / closes[-50]) - 1) * 100 if len(closes) >= 50 else 0
            rs_20 = ret_20 - ((qqq_cmp / qqq_closes[-20]) - 1) * 100
            rs_50 = ret_50 - qqq_50d_ret

            # Candle data
            last = bars[-1]
            prev = bars[-2]
            pattern, bullish_candle = _detect_candle_pattern(
                last.open, last.high, last.low, last.close,
                prev.open, prev.close
            )

            # Swing highs/lows for structure
            swing_highs = _find_swing_highs(bars, lookback=5)
            swing_lows = _find_swing_lows(bars, lookback=5)

            # --- SCORE BULLISH SETUP ---
            bull_score = 0.0
            if "bull" in directions:
                # 1. Regime (3.0)
                bull_score += regime_bull_pts
                # 2. RS Leader (2.5)
                if rs_20 > 0 and rs_50 > 0:
                    bull_score += 2.5
                elif rs_20 > 0 or rs_50 > 0:
                    bull_score += 1.0
                # 3. Uptrend HH+HL (2.0)
                if cmp > ema50 and (sma200 is None or ema50 > sma200):
                    bull_score += 2.0
                elif cmp > ema50:
                    bull_score += 1.0
                # 4. Price near proven support (1.5)
                if swing_lows:
                    nearest_low = min(swing_lows, key=lambda x: abs(x[1] - cmp))
                    dist_pct = abs(cmp - nearest_low[1]) / cmp * 100
                    if dist_pct <= 3.0:
                        bull_score += 1.5
                    elif dist_pct <= 6.0:
                        bull_score += 0.5
                # 5. RR to resistance (1.5)
                if swing_highs:
                    nearest_res = min(
                        [h for h in swing_highs if h[1] > cmp],
                        key=lambda x: x[1], default=None
                    )
                    if nearest_res:
                        rr = (nearest_res[1] - cmp) / atr
                        if rr >= 3.0:
                            bull_score += 1.5
                        elif rr >= 1.5:
                            bull_score += 0.75
                # 6. Stop distance (1.0)
                if swing_lows:
                    stop = min(
                        [l for l in swing_lows if l[1] < cmp],
                        key=lambda x: abs(x[1] - cmp), default=None
                    )
                    if stop:
                        stop_atr = (cmp - stop[1]) / atr
                        if 1.5 <= stop_atr <= 3.0:
                            bull_score += 1.0
                        elif 1.0 <= stop_atr <= 4.0:
                            bull_score += 0.5
                # 7. Bullish candle signal (0.75)
                if bullish_candle:
                    bull_score += 0.75
                # 8. No major resistance overhead (0.75)
                if swing_highs:
                    overhead = [h for h in swing_highs if h[1] > cmp]
                    if overhead:
                        nearest = min(overhead, key=lambda x: x[1])
                        gap_pct = (nearest[1] - cmp) / cmp * 100
                        if gap_pct >= 4.0:
                            bull_score += 0.75
                # 9. Breakout structure (0.5) — price within 2% of recent high
                if swing_highs:
                    recent_high = swing_highs[-1][1]
                    if abs(cmp - recent_high) / recent_high * 100 <= 2.0:
                        bull_score += 0.5
                # 10. Volume expansion (0.5)
                avg_vol_10 = sum(b.volume for b in bars[-11:-1]) / 10
                if bars[-1].volume > avg_vol_10 * 1.2:
                    bull_score += 0.5

            # --- SCORE BEARISH SETUP ---
            bear_score = 0.0
            if "bear" in directions:
                # 1. Regime (3.0) — inverted
                bear_score += regime_bear_pts
                # 2. RS Laggard (2.5) — inverted
                if rs_20 < 0 and rs_50 < 0:
                    bear_score += 2.5
                elif rs_20 < 0 or rs_50 < 0:
                    bear_score += 1.0
                # 3. Downtrend (2.0)
                if cmp < ema50 or (sma200 is not None and ema50 < sma200):
                    bear_score += 2.0
                elif cmp < ema21:
                    bear_score += 1.0
                # 4. Price near proven resistance (1.5)
                if swing_highs:
                    overhead_res = [h for h in swing_highs if h[1] > cmp]
                    if overhead_res:
                        nearest_res = min(overhead_res, key=lambda x: x[1])
                        dist_pct = abs(nearest_res[1] - cmp) / cmp * 100
                        if dist_pct <= 3.0:
                            bear_score += 1.5
                        elif dist_pct <= 6.0:
                            bear_score += 0.5
                # 5. Room to fall (1.5)
                if swing_lows:
                    below = [l for l in swing_lows if l[1] < cmp]
                    if below:
                        nearest_sup = max(below, key=lambda x: x[1])
                        fall_pct = (cmp - nearest_sup[1]) / cmp * 100
                        if fall_pct >= 4.0:
                            bear_score += 1.5
                        elif fall_pct >= 2.0:
                            bear_score += 0.75
                # 6. Stop distance above entry (1.0)
                if swing_highs:
                    above = [h for h in swing_highs if h[1] > cmp]
                    if above:
                        nearest = min(above, key=lambda x: x[1])
                        stop_atr = (nearest[1] - cmp) / atr
                        if 1.5 <= stop_atr <= 3.0:
                            bear_score += 1.0
                        elif 1.0 <= stop_atr <= 4.0:
                            bear_score += 0.5
                # 7. Bearish candle signal (0.75)
                if not bullish_candle and last.close < last.open:
                    bear_score += 0.75
                # 8. No major support directly below (0.75)
                if swing_lows:
                    below = [l for l in swing_lows if l[1] < cmp]
                    if below:
                        nearest_below = max(below, key=lambda x: x[1])
                        gap_pct = (cmp - nearest_below[1]) / cmp * 100
                        if gap_pct >= 4.0:
                            bear_score += 0.75
                # 9. Lower high structure (0.5)
                if len(swing_highs) >= 2:
                    if swing_highs[-1][1] < swing_highs[-2][1]:
                        bear_score += 0.5
                # 10. Volume on bearish candle (0.5)
                avg_vol_10 = sum(b.volume for b in bars[-11:-1]) / 10
                if bars[-1].volume > avg_vol_10 * 1.2 and not bullish_candle:
                    bear_score += 0.5

            # Resolve same-ticker conflict: take higher score only
            if "bull" in directions and "bear" in directions:
                if bull_score >= bear_score:
                    final_dir = "bull"
                    final_score = bull_score
                else:
                    final_dir = "bear"
                    final_score = bear_score
            elif "bull" in directions:
                final_dir = "bull"
                final_score = bull_score
            else:
                final_dir = "bear"
                final_score = bear_score

            # Grade
            if final_score >= 11.0:
                grade = "A"
            elif final_score >= 8.5:
                grade = "B"
            elif final_score >= 5.5:
                grade = "C"
            else:
                grade = "D"

            scored.append({
                "sym": sym, "direction": final_dir,
                "score": final_score, "grade": grade,
                "cmp": cmp, "ema21": ema21, "ema50": ema50,
                "atr": atr, "rs_20": rs_20, "rs_50": rs_50,
                "pattern": pattern, "bullish_candle": bullish_candle,
                "bull_score": bull_score if "bull" in directions else None,
                "bear_score": bear_score if "bear" in directions else None,
            })

        except Exception as e:
            log.warning(f"Skip {sym}: {e}")
            skipped += 1
            continue

    # Sort by score descending, take top 3 that meet minimum grade
    tradeable = [s for s in scored if s["grade"] in ("A", "B")]
    tradeable.sort(key=lambda x: x["score"], reverse=True)
    top3 = tradeable[:3]

    if not top3:
        lines.append("No A or B grade setups found today.")
        lines.append(f"Skipped: {skipped} | Scored: {len(scored)}")
        lines.append("Consider checking back after market close or "
                     "waiting for regime improvement.")
        return "\n".join(lines)

    lines.append(f"Scored {len(scored)} names | Skipped {skipped} | "
                 f"Tradeable (A/B): {len(tradeable)}")
    lines.append("")
    lines.append("TOP 3 RANKED SETUPS (all directions combined):")
    lines.append("=" * 70)

    direction_emoji = {"bull": "📈 BULL CALL SPREAD", "bear": "📉 BEAR PUT SPREAD"}

    for rank, s in enumerate(top3, 1):
        dir_label = direction_emoji[s["direction"]]
        contracts = "2 contracts" if s["grade"] == "A" else "1 contract (B-grade)"
        dte_note = "30-45 DTE" if s["direction"] == "bull" else "15-30 DTE"
        tp_note = "50% of max" if s["direction"] == "bull" else "60% of max"
        time_stop = "5 days" if s["direction"] == "bull" else "3 days"

        lines.append(f"")
        lines.append(f"RANK {rank}: {s['sym']}  {dir_label}")
        lines.append(f"  Score: {s['score']:.1f}/14  |  Grade: {s['grade']}  "
                     f"|  Size: {contracts}")
        lines.append(f"  CMP: ${s['cmp']:.2f}  |  21-EMA: ${s['ema21']:.2f}  "
                     f"|  ATR: ${s['atr']:.2f}")
        lines.append(f"  RS_20: {s['rs_20']:+.1f}%  |  RS_50: {s['rs_50']:+.1f}%")
        lines.append(f"  Last candle: {s['pattern']}")
        lines.append(f"  Expiry target: {dte_note}  |  TP: {tp_note}  "
                     f"|  Time stop: {time_stop}")
        if s["bull_score"] is not None and s["bear_score"] is not None:
            lines.append(f"  (On both lists — bull {s['bull_score']:.1f} vs "
                         f"bear {s['bear_score']:.1f} — took higher)")
        lines.append(f"  → Run: 'Analyze {s['sym']} {"bullish" if s['direction'] == "bull" else "bearish"} spread'")
        lines.append("  " + "-" * 66)

    if len(tradeable) > 3:
        lines.append(f"\nAlso scored (grade B, not in top 3): "
                     f"{', '.join(s['sym'] for s in tradeable[3:])}")

    lines.append("")
    lines.append("Run full Phase 0A→4 analysis on any ranked setup.")
    lines.append("All trades still require full 9/10-point audit before entry.")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 5 — FORMATTING / CALCULATION HELPERS
# ═══════════════════════════════════════════════════════════════

def fmt_exp(e: str) -> str:
    """Format YYYYMMDD expiry to YYYY-MM-DD."""
    return f"{e[:4]}-{e[4:6]}-{e[6:]}"


def fmt_chain_header() -> str:
    return (
        f"{'Strike':>8}  {'Bid':>7}  {'Ask':>7}  {'Last':>7}  "
        f"{'Delta':>7}  {'Gamma':>7}  {'Theta':>7}  "
        f"{'Vega':>7}  {'IV':>7}  {'OI':>7}"
    )


def fmt_chain_row(t, spot: float) -> str:
    strike = t.contract.strike
    atm    = " <-- ATM" if abs(strike - spot) / spot < 0.005 else ""
    greeks = t.modelGreeks or t.bidGreeks
    return (
        f"{strike:>8.2f}  "
        f"{fmt_price(t.bid):>7}  "
        f"{fmt_price(t.ask):>7}  "
        f"{fmt_price(t.last):>7}  "
        f"{fmt_greek(greeks.delta if greeks else None):>7}  "
        f"{fmt_greek(greeks.gamma if greeks else None):>7}  "
        f"{fmt_greek(greeks.theta if greeks else None):>7}  "
        f"{fmt_greek(greeks.vega  if greeks else None):>7}  "
        f"{fmt_iv(greeks.impliedVol if greeks else None):>7}  "
        f"{t.contract.conId:>7}"
        f"{atm}"
    )


def fmt_price(v) -> str:
    if v is None or v != v or v <= 0:
        return "  --   "
    return f"${v:.2f}"


def fmt_greek(v) -> str:
    if v is None or v != v:
        return "  --  "
    return f"{v:.3f}"


def fmt_iv(v) -> str:
    if v is None or v != v or v <= 0:
        return "  --  "
    return f"{v*100:.1f}%"


def audit_check(label: str, passed: bool, detail: str) -> dict:
    return {'label': label, 'pass': passed, 'detail': detail}


# -- Shared calculation helpers --------------------------------

def calc_ema(values: list, period: int) -> list:
    """Exponential Moving Average."""
    if len(values) < period:
        return []
    k   = 2 / (period + 1)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def calc_vwap(bars) -> float:
    """VWAP from OHLCV bars (typical price weighted by volume)."""
    total_vol = sum(b.volume for b in bars)
    if total_vol == 0:
        return bars[-1].close if bars else 0
    tp_vol = sum(
        ((b.high + b.low + b.close) / 3) * b.volume
        for b in bars
    )
    return tp_vol / total_vol


def calc_rsi(closes: list, period: int = 14) -> list:
    """RSI using Wilder smoothing."""
    if len(closes) < period + 1:
        return []
    gains  = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals = []
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 100
        rsi_vals.append(100 - (100 / (1 + rs)))
    return rsi_vals


def calc_atr(highs: list, lows: list, closes: list,
             period: int = 14) -> list:
    """Average True Range."""
    if len(highs) < period + 1:
        return []
    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1])
        )
        trs.append(tr)
    atr = [sum(trs[:period]) / period]
    for tr in trs[period:]:
        atr.append((atr[-1] * (period - 1) + tr) / period)
    return atr


def calc_macd(closes: list,
              fast: int = 12, slow: int = 26,
              signal: int = 9):
    """MACD line, signal line, histogram."""
    if len(closes) < slow + signal:
        return [], [], []
    ema_fast  = calc_ema(closes, fast)
    ema_slow  = calc_ema(closes, slow)
    min_len   = min(len(ema_fast), len(ema_slow))
    macd_line = [
        ema_fast[-min_len + i] - ema_slow[-min_len + i]
        for i in range(min_len)
    ]
    sig_line  = calc_ema(macd_line, signal)
    min_len2  = min(len(macd_line), len(sig_line))
    histogram = [
        macd_line[-min_len2 + i] - sig_line[-min_len2 + i]
        for i in range(min_len2)
    ]
    return macd_line, sig_line, histogram


def calc_bollinger(closes: list, period: int = 20,
                   std_dev: float = 2.0):
    """Bollinger Bands: upper, mid, lower."""
    if len(closes) < period:
        return [], [], []
    upper, mid, lower = [], [], []
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1: i + 1]
        mean   = sum(window) / period
        std    = (sum((x - mean) ** 2 for x in window) / period) ** 0.5
        upper.append(round(mean + std_dev * std, 4))
        mid.append(round(mean, 4))
        lower.append(round(mean - std_dev * std, 4))
    return upper, mid, lower


def assess_bias(curr, e9, e21, e50, vwap, rsi, macd_h):
    """Return (bias_string, strength_label) from indicator readings."""
    if None in (e9, e21, rsi):
        return 'NEUTRAL', 'INSUFFICIENT DATA'
    bull = sum([
        curr > e9,
        curr > e21,
        e50 is not None and curr > e50,
        curr > vwap,
        rsi > 52,
        macd_h is not None and macd_h > 0
    ])
    bear = sum([
        curr < e9,
        curr < e21,
        e50 is not None and curr < e50,
        curr < vwap,
        rsi < 48,
        macd_h is not None and macd_h < 0
    ])
    total = 6
    if bull >= 5:
        return 'STRONG BULL', f'{bull}/{total} signals'
    elif bull == 4:
        return 'BULL', f'{bull}/{total} signals'
    elif bear >= 5:
        return 'STRONG BEAR', f'{bear}/{total} signals'
    elif bear == 4:
        return 'BEAR', f'{bear}/{total} signals'
    else:
        return 'NEUTRAL / CHOPPY', f'Mixed {bull}B/{bear}S'


def identify_momentum_setup(bars, e9, e21, e50, vwap,
                             rsi, macd_h, direction) -> str:
    """Identify the dominant pattern driving the setup."""
    closes = [b.close for b in bars]
    curr   = closes[-1]

    # EMA 9/21 golden or death cross (fresh)
    if len(closes) >= 3:
        prev_ema9  = calc_ema(closes[:-1], 9)
        prev_ema21 = calc_ema(closes[:-1], 21)
        if prev_ema9 and prev_ema21:
            if direction == 'LONG' and prev_ema9[-1] <= prev_ema21[-1] and e9 > e21:
                return "EMA 9/21 Golden Cross"
            if direction == 'SHORT' and prev_ema9[-1] >= prev_ema21[-1] and e9 < e21:
                return "EMA 9/21 Death Cross"

    # VWAP reclaim or breakdown (3 bars below/above then cross)
    if len(closes) >= 4:
        was_below = all(c < vwap for c in closes[-4:-1])
        was_above = all(c > vwap for c in closes[-4:-1])
        if direction == 'LONG' and was_below and curr > vwap:
            return "VWAP Reclaim"
        if direction == 'SHORT' and was_above and curr < vwap:
            return "VWAP Breakdown"

    # EMA 50 bounce
    if e50 and abs(curr - e50) / e50 < 0.003:
        return "EMA 50 Bounce"

    # HOD or LOD break
    recent_high = max(b.high for b in bars[-20:])
    recent_low  = min(b.low  for b in bars[-20:])
    if direction == 'LONG' and curr >= recent_high * 0.998:
        return "HOD Breakout"
    if direction == 'SHORT' and curr <= recent_low * 1.002:
        return "LOD Breakdown"

    # RSI momentum
    if direction == 'LONG' and rsi >= 60:
        return "RSI Momentum Continuation"
    if direction == 'SHORT' and rsi <= 40:
        return "RSI Momentum Continuation"

    return "EMA Trend Continuation"


# ═══════════════════════════════════════════════════════════════
# SECTION 6 — MCP JSON-RPC HANDLER
# ═══════════════════════════════════════════════════════════════

TOOLS_DISPATCH = {
    # Options tools
    "get_options_chain":   tool_get_options_chain,
    "get_spread_analysis": tool_get_spread_analysis,
    "get_stock_quote":     tool_get_stock_quote,
    "get_watchlist_scan":  tool_get_watchlist_scan,
    "get_iv_rank":         tool_get_iv_rank,
    # Stock momentum tools
    "get_momentum_bars":   tool_get_momentum_bars,
    "momentum_scan":       tool_momentum_scan,
    "trade_update":        tool_trade_update,
    # Watchlist management tools (v2.1)
    "get_watchlist":       tool_get_watchlist,
    "update_watchlist":    tool_update_watchlist,
    "scan_full_universe":  tool_scan_full_universe,
    # Stock swing tools (v2.2)
    "get_swing_watchlist":     tool_get_swing_watchlist,
    "update_swing_watchlist":  tool_update_swing_watchlist,
    "scan_swing_universe":     tool_scan_swing_universe,
    "get_swing_setup":         tool_get_swing_setup,
    "get_position_size":       tool_get_position_size,
    "scan_pullback_setups":    tool_scan_pullback_setups,
    # Portfolio tools (v2.3)
    "get_open_positions":      tool_get_open_positions,
    # Bear options tools (v2.4)
    "get_bear_watchlist":      tool_get_bear_watchlist,
    "update_bear_watchlist":   tool_update_bear_watchlist,
    "scan_combined_setups":    tool_scan_combined_setups,
}

TOOLS_LIST = [
    # ── Options tools ──────────────────────────────────────────
    {
        "name": "get_options_chain",
        "description": (
            "Fetch a live IBKR options chain for any ticker and expiry. "
            "Returns bid/ask/last/delta/gamma/theta/vega/IV per strike, "
            "real-time from your TWS session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker":           {"type": "string"},
                "expiry_date":      {"type": "string",
                                     "description": "YYYY-MM-DD format"},
                "option_type":      {"type": "string",
                                     "enum": ["calls", "puts", "both"],
                                     "default": "calls"},
                "strikes_near_atm": {"type": "integer", "default": 5}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_spread_analysis",
        "description": (
            "Fetch live prices for two specific strikes and run the full "
            "V9.2 protocol audit: trade math and all checklist items."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker":       {"type": "string"},
                "expiry_date":  {"type": "string"},
                "long_strike":  {"type": "number"},
                "short_strike": {"type": "number"},
                "option_type":  {"type": "string",
                                 "enum": ["call", "put", "C", "P"]},
                "contracts":    {"type": "integer", "default": 2}
            },
            "required": ["ticker", "expiry_date",
                         "long_strike", "short_strike", "option_type"]
        }
    },
    {
        "name": "get_stock_quote",
        "description": "Real-time bid/ask/last/volume snapshot for a single ticker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_watchlist_scan",
        "description": (
            "Quick real-time quote scan across all 10 options watchlist tickers. "
            "Returns last, bid, ask, spread, and volume for each."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_iv_rank",
        "description": (
            "IV Rank for a ticker using 52-week historical implied volatility. "
            "Classifies regime as high, medium, or low IV."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"}
            },
            "required": ["ticker"]
        }
    },
    # ── Stock momentum tools ───────────────────────────────────
    {
        "name": "momentum_scan",
        "description": (
            "Scan the 10-stock watchlist on 5-min bars. "
            "Checks QQQ market bias first. "
            "Scores each ticker on EMA stack, VWAP, RSI, MACD, volume. "
            "Returns top 3 setups with entry, stop, targets, and full "
            "position sizing for a $5,000 account."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_momentum_bars",
        "description": (
            "Fetch 5-min or 15-min bar detail for one ticker. "
            "Calculates EMA9/21/50, VWAP, RSI14, ATR14, MACD, "
            "Bollinger Bands, and directional bias."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker":   {"type": "string"},
                "bar_size": {"type": "string",
                             "enum": ["5 mins", "15 mins"],
                             "default": "5 mins"},
                "lookback": {"type": "string", "default": "1 D"}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "trade_update",
        "description": (
            "Re-assess an open stock momentum position at the 15-min check-in. "
            "Returns HOLD, TRAIL STOP, TAKE PARTIAL, or EXIT NOW "
            "with an updated stop level where applicable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker":       {"type": "string"},
                "entry_price":  {"type": "number"},
                "direction":    {"type": "string",
                                 "enum": ["LONG", "SHORT"]},
                "stop_price":   {"type": "number"},
                "shares":       {"type": "integer", "default": 100},
                "minutes_held": {"type": "integer", "default": 15}
            },
            "required": ["ticker", "entry_price", "direction", "stop_price"]
        }
    },
    # ── Watchlist management tools (v2.1) ──────────────────────
    {
        "name": "get_watchlist",
        "description": (
            "Read the current trading watchlist from watchlist.json. "
            "Returns ticker list, tier, added date, reason, and recent "
            "change history. Use at session start to know which tickers "
            "to focus on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "update_watchlist",
        "description": (
            "Add or remove a ticker from watchlist.json. "
            "REQUIRES confirmed=true to execute - otherwise returns dry run. "
            "This is the approval gate: always call once with confirmed=false "
            "first to preview the change, then again with confirmed=true after "
            "the user approves."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action":    {"type": "string", "enum": ["add", "remove"]},
                "symbol":    {"type": "string"},
                "reason":    {"type": "string",
                              "description": "Why this change is being made"},
                "tier":      {"type": "string",
                              "enum": ["core", "watch"], "default": "core"},
                "confirmed": {"type": "boolean", "default": False}
            },
            "required": ["action", "symbol", "reason"]
        }
    },
    {
        "name": "scan_full_universe",
        "description": (
            "Weekly NASDAQ-100 scan for watchlist promotion candidates. "
            "Scores each NDX name against 4 criteria: trend (above 50-SMA, "
            "50 above 200), relative strength (50d return vs QQQ), recent "
            "momentum (20d positive), and liquidity (1M+ avg volume). "
            "Returns top 10 candidates not currently in watchlist plus "
            "health check on existing names. Run weekly. Takes ~3-5 minutes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "string",
                              "enum": ["moderate", "strict"],
                              "default": "moderate",
                              "description": "moderate=3 of 4 criteria, "
                                             "strict=all 4 required"}
            }
        }
    },
    # ── Stock swing tools (v2.2) ───────────────────────────────
    {
        "name": "get_swing_watchlist",
        "description": (
            "Read the current SWING STOCK watchlist from "
            "swing_watchlist.json. Used by the Swing Alpha project "
            "(separate from options watchlist). Returns ticker list, "
            "tier, added date, reason, and recent change history."
        ),
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "update_swing_watchlist",
        "description": (
            "Add or remove a ticker from swing_watchlist.json. "
            "REQUIRES confirmed=true to execute - otherwise dry run. "
            "Approval gate: always preview with confirmed=false first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action":    {"type": "string", "enum": ["add", "remove"]},
                "symbol":    {"type": "string"},
                "reason":    {"type": "string"},
                "tier":      {"type": "string",
                              "enum": ["core", "watch"], "default": "core"},
                "confirmed": {"type": "boolean", "default": False}
            },
            "required": ["action", "symbol", "reason"]
        }
    },
    {
        "name": "scan_swing_universe",
        "description": (
            "Weekly NASDAQ-100 scan for SWING STOCK promotion candidates. "
            "Scores against 6 criteria: trend, RS vs QQQ, recent momentum, "
            "liquidity (2M+ vol), tradeable volatility (ATR 1.5-5%), and "
            "no big gaps (max 8% single day). Different from options scan: "
            "emphasizes pullback-friendly characteristics. Takes ~3-5 min."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "string",
                              "enum": ["moderate", "strict"],
                              "default": "moderate",
                              "description": "moderate=4 of 6, strict=5 of 6"}
            }
        }
    },
    {
        "name": "get_swing_setup",
        "description": (
            "Full pullback or breakout swing trade analysis on a single "
            "ticker. Pulls 6mo daily data, computes EMAs, ATR, swing "
            "highs/lows, candle pattern, validates pullback structure, "
            "and outputs entry/stop/T1/T2 with exact share count for "
            "1% risk on a $5K account. Returns TRADEABLE / WAIT / SKIP "
            "verdict with reasons."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker":       {"type": "string"},
                "setup_type":   {"type": "string",
                                 "enum": ["pullback", "breakout"],
                                 "default": "pullback"},
                "account_size": {"type": "number", "default": 5000},
                "risk_pct":     {"type": "number", "default": 1.0,
                                 "description": "Risk % per trade (1.0 = 1%)"}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_position_size",
        "description": (
            "Calculate exact share count for a swing trade given "
            "account size, risk %, entry, and stop. Returns shares, "
            "capital required, and total risk in dollars."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_size": {"type": "number", "default": 5000},
                "risk_pct":     {"type": "number", "default": 1.0},
                "entry_price":  {"type": "number"},
                "stop_price":   {"type": "number"}
            },
            "required": ["entry_price", "stop_price"]
        }
    },
    {
        "name": "scan_pullback_setups",
        "description": (
            "DAILY swing scanner: scans the swing watchlist for tickers "
            "currently in tradeable pullback setups. Categorizes each "
            "name as TRADEABLE (entry plan ready), MONITORING (close but "
            "not yet), or BLOCKED (broken trend/no setup). Returns full "
            "entry/stop/T1/share count for tradeable names. Run at start "
            "of every trading session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_size": {"type": "number", "default": 5000},
                "risk_pct":     {"type": "number", "default": 1.0}
            }
        }
    },
    # ── Portfolio tools (v2.3) ─────────────────────────────────
    {
        "name": "get_open_positions",
        "description": (
            "Return live count of current open positions from the IBKR "
            "account. Used to enforce the 5-position cap across both "
            "options spreads and swing trades. Multi-leg option spreads "
            "are counted as 1 position. Returns total count, breakdown "
            "by type (stock/option), and STATUS (OK / WARNING / AT CAP)."
        ),
        "inputSchema": {"type": "object", "properties": {}}
    },
    # ── Bear options tools (v2.4) ───────────────────────────────
    {
        "name": "get_bear_watchlist",
        "description": (
            "Read the current BEAR OPTIONS watchlist from "
            "bear_watchlist.json. Used by Options Alpha V9.9 for "
            "bear put debit spread candidates. Separate from the "
            "bullish options watchlist. Returns tickers, added dates, "
            "reasons, and recent change history."
        ),
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "update_bear_watchlist",
        "description": (
            "Add or remove a ticker from bear_watchlist.json. "
            "REQUIRES confirmed=true to execute - otherwise dry run. "
            "Approval gate: always preview with confirmed=false first. "
            "Min 6, max 12 tickers enforced."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action":    {"type": "string", "enum": ["add", "remove"]},
                "symbol":    {"type": "string"},
                "reason":    {"type": "string"},
                "tier":      {"type": "string",
                              "enum": ["core", "watch"], "default": "core"},
                "confirmed": {"type": "boolean", "default": False}
            },
            "required": ["action", "symbol", "reason"]
        }
    },
    {
        "name": "scan_combined_setups",
        "description": (
            "UNIFIED daily scanner for Options Alpha V9.9. Scores ALL "
            "names across both the bullish watchlist (10 names) AND the "
            "bearish watchlist (10 names) using the 14-point weighted "
            "scorecard. Evaluates both bull and bear setups for every "
            "name. Same-ticker conflict resolution: takes the "
            "higher-scoring direction only. Returns the top 3 ranked "
            "setups regardless of direction with full entry guidance. "
            "This is the PRIMARY daily analysis tool — use it at the "
            "start of every session instead of running separate scans."
        ),
        "inputSchema": {"type": "object", "properties": {}}
    },
]


async def handle_request(req: dict, loop: asyncio.AbstractEventLoop) -> dict:
    """Route a single JSON-RPC request and return a response dict."""
    req_id  = req.get('id')
    method  = req.get('method', '')
    params  = req.get('params', {})

    # Notifications have no id — must send no response at all
    if req_id is None:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    # Claude Desktop does not accept JSON-RPC error objects.
    # All error conditions must return a result with error text in content.
    def err(message):
        return ok({
            "content": [{"type": "text", "text": f"ERROR: {message}"}]
        })

    try:
        if method == 'initialize':
            return ok({
                "protocolVersion": "2024-11-05",
                "capabilities":    {"tools": {}},
                "serverInfo":      {"name": "ibkr-options-mcp", "version": "2.4"}
            })

        elif method == 'tools/list':
            return ok({"tools": TOOLS_LIST})

        elif method == 'tools/call':
            tool_name = params.get('name')
            tool_args = params.get('arguments', {})

            if tool_name not in TOOLS_DISPATCH:
                return err(f"Unknown tool: {tool_name}")

            try:
                result_text = await TOOLS_DISPATCH[tool_name](tool_args)
                return ok({
                    "content": [{"type": "text", "text": result_text}]
                })
            except ConnectionError as e:
                return ok({
                    "content": [{
                        "type": "text",
                        "text": f"TWS CONNECTION ERROR\n\n{e}"
                    }]
                })
            except Exception as e:
                log.exception(f"Tool {tool_name} raised an exception")
                return ok({
                    "content": [{
                        "type": "text",
                        "text": f"Tool error in {tool_name}: {e}"
                    }]
                })

        elif method == 'ping':
            return ok({})

        else:
            # Unknown method — return empty result rather than an error object
            return ok({})

    except Exception as e:
        log.exception("Unhandled error in handle_request")
        return err(f"Internal error: {e}")


# ═══════════════════════════════════════════════════════════════
# SECTION 7 — MAIN ENTRY POINT
# Threading-based stdin reader + asyncio event loop.
# Workaround for Python 3.14 Windows asyncio regression
# (_ProactorReadPipeTransport).
# ═══════════════════════════════════════════════════════════════

async def main():
    log.info("IBKR MCP Server v2.4 starting (stdio transport)")
    log.info(f"TWS target: {TWS_HOST}:{TWS_PORT}  clientId={TWS_CLIENT_ID}")
    _wl  = get_watchlist_symbols()
    _bwl = get_bear_watchlist_symbols()
    _swl = get_swing_watchlist_symbols()
    log.info(f"Bull options watchlist ({len(_wl)}):  {', '.join(_wl)}")
    log.info(f"Bear options watchlist ({len(_bwl)}): {', '.join(_bwl)}")
    log.info(f"Swing watchlist ({len(_swl)}):        {', '.join(_swl)}")
    log.info(f"Momentum watchlist: {', '.join(SCALP_WATCHLIST)}")
    log.info(f"Tools exposed: 21 (v2.4 adds bear watchlist + scan_combined_setups)")
    log.info(f"Tools exposed: 18 (v2.3 adds get_open_positions)")

    loop      = asyncio.get_event_loop()
    queue     = asyncio.Queue()
    shutdown  = asyncio.Event()

    def stdin_reader():
        """Runs in a background thread; puts lines onto the async queue."""
        try:
            for raw_line in sys.stdin:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError as e:
                    log.warning(f"Invalid JSON on stdin: {e}  line={raw_line!r}")
                    continue
                loop.call_soon_threadsafe(queue.put_nowait, obj)
        except Exception as e:
            log.error(f"stdin_reader error: {e}")
        finally:
            loop.call_soon_threadsafe(shutdown.set)

    thread = threading.Thread(target=stdin_reader, daemon=True)
    thread.start()
    log.info("stdin reader thread started")

    while not shutdown.is_set() or not queue.empty():
        try:
            req = await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue

        response = await handle_request(req, loop)

        if response is not None:
            try:
                sys.stdout.write(json.dumps(response) + '\n')
                sys.stdout.flush()
            except Exception as e:
                log.error(f"Failed to write response: {e}")

    await disconnect_ib()
    log.info("Server shutdown complete")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
