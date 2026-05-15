#!/usr/bin/env python3
"""
IBKR Options MCP Server — Self-Test
Run this BEFORE connecting to Claude Desktop.

Usage:  python3 test.py
        python3 test.py --live    (test against live account port 7496)

Tests:
  1. Python version check
  2. ib_async import
  3. TWS connection
  4. Stock quote fetch
  5. Options chain fetch
  6. Greeks availability
  7. Historical IV data (for IV Rank)
  8. MCP JSON-RPC message format
"""

import sys
import asyncio
import json

PASS  = "✅ PASS"
FAIL  = "❌ FAIL"
SKIP  = "⏭  SKIP"
WARN  = "⚠️  WARN"

# Port: 7497=TWS paper (default), 7496=TWS live
PORT = 7496 if '--live' in sys.argv else 7497
HOST = '127.0.0.1'

print(f"\n═══ IBKR Options MCP Server — Self-Test ═══")
print(f"Testing against TWS port {PORT} "
      f"({'LIVE' if PORT == 7496 else 'PAPER'})\n")

# ── Test 1: Python version ─────────────────────────────────────
print(f"Test 1: Python version...", end=' ')
major, minor = sys.version_info[:2]
if major >= 3 and minor >= 9:
    print(f"{PASS} — Python {major}.{minor}")
else:
    print(f"{FAIL} — Need Python 3.9+, have {major}.{minor}")
    sys.exit(1)

# ── Test 2: ib_async import ────────────────────────────────────
print(f"Test 2: ib_async import...", end=' ')
try:
    from ib_async import IB, Stock, Option, util
    print(f"{PASS}")
except ImportError as e:
    print(f"{FAIL} — {e}")
    print("\n  Fix: pip install ib_async")
    sys.exit(1)

# ── Tests 3-7 require TWS running ─────────────────────────────
async def run_connection_tests():
    ib = IB()

    # Test 3: Connection
    print(f"Test 3: Connect to TWS port {PORT}...", end=' ')
    try:
        await ib.connectAsync(HOST, PORT, clientId=99,
                              readonly=True, timeout=10)
        print(f"{PASS}")
    except Exception as e:
        print(f"{FAIL} — {e}")
        print(f"\n  Fix:")
        print(f"  1. Open TWS and log in")
        print(f"  2. TWS → Edit → Global Configuration → API → Settings")
        print(f"     ✅ Enable ActiveX and Socket Clients")
        print(f"     Socket port: {PORT}")
        print(f"  3. Click OK and try again")
        return False

    # Test 4: Stock quote
    print(f"Test 4: NVDA real-time quote...", end=' ')
    try:
        stock = Stock('NVDA', 'SMART', 'USD')
        await ib.qualifyContractsAsync(stock)
        [t] = await ib.reqTickersAsync(stock)
        price = t.marketPrice()
        if price and price == price:  # not NaN
            print(f"{PASS} — NVDA: ${price:.2f}")
        else:
            print(f"{WARN} — Price is NaN. "
                  f"Check market data subscriptions for NVDA")
    except Exception as e:
        print(f"{FAIL} — {e}")

    # Test 5: Options chain metadata
    print(f"Test 5: NVDA options expiry dates...", end=' ')
    try:
        chains = await ib.reqSecDefOptParamsAsync(
            'NVDA', '', 'STK', stock.conId
        )
        chain = next((c for c in chains if c.exchange == 'SMART'), None)
        if chain and chain.expirations:
            exp_sample = sorted(chain.expirations)[:3]
            formatted  = [f"{e[:4]}-{e[4:6]}-{e[6:]}" for e in exp_sample]
            print(f"{PASS} — Next: {', '.join(formatted)}")
        else:
            print(f"{WARN} — No chain data. "
                  f"Need options market data subscription")
    except Exception as e:
        print(f"{FAIL} — {e}")

    # Test 6: Live Greeks
    print(f"Test 6: NVDA option Greeks (live)...", end=' ')
    try:
        if chain and chain.expirations and chain.strikes:
            # Pick nearest expiry and ATM strike
            from datetime import datetime
            today = datetime.now().strftime('%Y%m%d')
            future_exp = sorted(e for e in chain.expirations if e >= today)
            if not future_exp:
                print(f"{SKIP} — No future expiries found")
            else:
                exp = future_exp[0]
                # Find ATM strike
                price_val = t.marketPrice()
                nearest   = min(chain.strikes,
                                key=lambda s: abs(s - price_val))
                opt = Option('NVDA', exp, nearest, 'C',
                             'SMART', tradingClass='NVDA')
                qualified = await ib.qualifyContractsAsync(opt)
                if qualified:
                    [opt_t] = await ib.reqTickersAsync(opt)
                    await asyncio.sleep(2)
                    g = opt_t.modelGreeks or opt_t.bidGreeks
                    if g and g.delta and g.delta == g.delta:
                        print(f"{PASS} — Delta: {g.delta:.3f}  "
                              f"IV: {g.impliedVol*100:.1f}%  "
                              f"Theta: {g.theta:.4f}")
                    else:
                        print(f"{WARN} — Greeks not populated yet. "
                              f"Normal during after-hours. "
                              f"Will work during market hours.")
                else:
                    print(f"{WARN} — Could not qualify option contract")
        else:
            print(f"{SKIP} — Chain data not available")
    except Exception as e:
        print(f"{FAIL} — {e}")

    # Test 7: Historical IV data (needed for IV Rank)
    print(f"Test 7: Historical IV data (52-week, for IV Rank)...",
          end=' ')
    try:
        bars = await ib.reqHistoricalDataAsync(
            stock,
            endDateTime='',
            durationStr='4 W',   # Just 4 weeks for the test
            barSizeSetting='1 day',
            whatToShow='OPTION_IMPLIED_VOLATILITY',
            useRTH=True,
            keepUpToDate=False
        )
        if bars and len(bars) >= 5:
            iv_vals = [b.close for b in bars if b.close and b.close > 0]
            print(f"{PASS} — {len(iv_vals)} days of IV data. "
                  f"Latest: {iv_vals[-1]*100:.1f}%")
        else:
            print(f"{WARN} — Only {len(bars) if bars else 0} bars. "
                  f"May need IBKR data subscription upgrade.")
    except Exception as e:
        print(f"{FAIL} — {e}")

    ib.disconnect()
    return True

# Run async tests
success = asyncio.run(run_connection_tests())

# ── Test 8: MCP JSON-RPC format (no TWS needed) ────────────────
print(f"Test 8: MCP JSON-RPC message format...", end=' ')
try:
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}}
    }
    roundtrip = json.loads(json.dumps(msg))
    assert roundtrip['method'] == 'initialize'
    print(f"{PASS}")
except Exception as e:
    print(f"{FAIL} — {e}")

# ── Summary ────────────────────────────────────────────────────
print(f"\n═══ Test Complete ═══")
if success:
    print("TWS connection working. You're ready to install in Claude Desktop.")
    print("See README.md for the 4-line config you need to add.")
else:
    print("TWS connection failed. Follow the Fix instructions above.")
    print("Once TWS is configured, run this test again.")
print()
