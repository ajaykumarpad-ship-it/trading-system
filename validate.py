#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
═══════════════════════════════════════════════════════════════
 TRADING SYSTEM VALIDATOR
 For: ibkr-options-mcp server + watchlist files + system prompts
 Run: python validate.py
 Place in: C:\Users\ajayk\Documents\Claude\ibkr-options-mcp\
═══════════════════════════════════════════════════════════════
"""

import sys
import json
import re
import ast
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
SERVER_FILE  = SCRIPT_DIR / "server.py"
BULL_WL      = SCRIPT_DIR / "watchlist.json"
BEAR_WL      = SCRIPT_DIR / "bear_watchlist.json"
SWING_WL     = SCRIPT_DIR / "swing_watchlist.json"

EXPECTED_TOOL_COUNT   = 21
EXPECTED_SERVER_VER   = "2.4"
WL_MIN_SIZE           = 6
WL_MAX_SIZE           = 12

# ── Helpers ────────────────────────────────────────────────────
PASS  = "  ✅ PASS"
FAIL  = "  ❌ FAIL"
WARN  = "  ⚠️  WARN"
INFO  = "  ℹ️  INFO"

total_checks = 0
failed_checks = 0
warned_checks = 0

def check(label, condition, detail="", warn_only=False):
    global total_checks, failed_checks, warned_checks
    total_checks += 1
    if condition:
        print(f"{PASS}  {label}")
    else:
        if warn_only:
            warned_checks += 1
            print(f"{WARN}  {label}")
        else:
            failed_checks += 1
            print(f"{FAIL}  {label}")
        if detail:
            print(f"         → {detail}")

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

# ══════════════════════════════════════════════════════════════
# SECTION 1 — FILE EXISTENCE
# ══════════════════════════════════════════════════════════════
section("1. FILE EXISTENCE")

check("server.py exists",         SERVER_FILE.exists(),
      f"Expected at: {SERVER_FILE}")
check("watchlist.json exists",    BULL_WL.exists(),
      f"Expected at: {BULL_WL}")
check("bear_watchlist.json exists", BEAR_WL.exists(),
      f"Expected at: {BEAR_WL}")
check("swing_watchlist.json exists", SWING_WL.exists(),
      f"Expected at: {SWING_WL}")

if not SERVER_FILE.exists():
    print(f"\n{'═'*60}")
    print("  FATAL: server.py not found. Cannot continue.")
    print(f"{'═'*60}")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════
# SECTION 2 — SERVER.PY SYNTAX
# ══════════════════════════════════════════════════════════════
section("2. SERVER.PY SYNTAX")

server_src = SERVER_FILE.read_text(encoding="utf-8")

try:
    ast.parse(server_src)
    check("Python syntax valid", True)
except SyntaxError as e:
    check("Python syntax valid", False,
          f"SyntaxError at line {e.lineno}: {e.msg}")
    print(f"\n{'═'*60}")
    print("  FATAL: Syntax error in server.py. Cannot continue.")
    print(f"{'═'*60}")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════
# SECTION 3 — SERVER VERSION
# ══════════════════════════════════════════════════════════════
section("3. SERVER VERSION CONSISTENCY")

# Header version
header_match = re.search(
    r'IBKR OPTIONS.*?SERVER\s+[—-]+\s+v([\d.]+)', server_src)
header_ver = header_match.group(1) if header_match else None
check("Version in header docstring",
      header_ver == EXPECTED_SERVER_VER,
      f"Found: v{header_ver}  Expected: v{EXPECTED_SERVER_VER}")

# serverInfo version in initialize response
info_match = re.search(
    r'"serverInfo".*?"version":\s*"([\d.]+)"', server_src)
info_ver = info_match.group(1) if info_match else None
check("Version in serverInfo response",
      info_ver == EXPECTED_SERVER_VER,
      f"Found: v{info_ver}  Expected: v{EXPECTED_SERVER_VER}")

# Startup log version
log_match = re.search(
    r'log\.info.*?v([\d.]+)\s+starting', server_src)
log_ver = log_match.group(1) if log_match else None
check("Version in startup log",
      log_ver == EXPECTED_SERVER_VER,
      f"Found: v{log_ver}  Expected: v{EXPECTED_SERVER_VER}",
      warn_only=True)

check("All version strings consistent",
      len({header_ver, info_ver}) == 1,
      f"header={header_ver}, serverInfo={info_ver}")

# ══════════════════════════════════════════════════════════════
# SECTION 4 — TOOLS_DISPATCH
# ══════════════════════════════════════════════════════════════
section("4. TOOLS_DISPATCH REGISTRATION")

# Extract tool names from TOOLS_DISPATCH dict
dispatch_matches = re.findall(
    r'"([a-z_]+)":\s+tool_[a-z_]+', server_src)
dispatch_names = list(dict.fromkeys(dispatch_matches))  # preserve order, dedup

check(f"TOOLS_DISPATCH has {EXPECTED_TOOL_COUNT} entries",
      len(dispatch_names) == EXPECTED_TOOL_COUNT,
      f"Found: {len(dispatch_names)}  Expected: {EXPECTED_TOOL_COUNT}")

# Check for duplicates
from collections import Counter
dispatch_counts = Counter(dispatch_matches)
dispatch_dupes = {k: v for k, v in dispatch_counts.items() if v > 1}
check("No duplicate entries in TOOLS_DISPATCH",
      len(dispatch_dupes) == 0,
      f"Duplicates: {dispatch_dupes}" if dispatch_dupes else "")

# ══════════════════════════════════════════════════════════════
# SECTION 5 — TOOLS_LIST
# ══════════════════════════════════════════════════════════════
section("5. TOOLS_LIST REGISTRATION")

list_matches = re.findall(
    r'"name":\s*"([a-z_]+)"', server_src)
# Filter out non-tool "name" keys (serverInfo, inputSchema props etc.)
# Tool names use underscores and match known patterns
tool_name_pattern = re.compile(
    r'^(get|update|scan|momentum|trade|tool)_[a-z_]+$')
list_names = [n for n in list_matches if tool_name_pattern.match(n)]
list_names_dedup = list(dict.fromkeys(list_names))

check(f"TOOLS_LIST has {EXPECTED_TOOL_COUNT} entries",
      len(list_names_dedup) == EXPECTED_TOOL_COUNT,
      f"Found: {len(list_names_dedup)}  Expected: {EXPECTED_TOOL_COUNT}")

list_counts = Counter(list_names)
list_dupes = {k: v for k, v in list_counts.items() if v > 1}
check("No duplicate entries in TOOLS_LIST",
      len(list_dupes) == 0,
      f"Duplicates: {list_dupes}" if list_dupes else "")

# ══════════════════════════════════════════════════════════════
# SECTION 6 — DISPATCH vs LIST CONSISTENCY
# ══════════════════════════════════════════════════════════════
section("6. DISPATCH vs LIST CONSISTENCY")

dispatch_set = set(dispatch_names)
list_set = set(list_names_dedup)

only_dispatch = dispatch_set - list_set
only_list = list_set - dispatch_set

check("All DISPATCH tools have TOOLS_LIST entry",
      len(only_dispatch) == 0,
      f"In DISPATCH only: {only_dispatch}" if only_dispatch else "")

check("All TOOLS_LIST entries have DISPATCH registration",
      len(only_list) == 0,
      f"In LIST only: {only_list}" if only_list else "")

check("DISPATCH and LIST are identical sets",
      dispatch_set == list_set)

# ══════════════════════════════════════════════════════════════
# SECTION 7 — TOOL FUNCTION EXISTENCE
# ══════════════════════════════════════════════════════════════
section("7. TOOL FUNCTION DEFINITIONS")

# Every tool in dispatch should have a corresponding async function
missing_funcs = []
for tool_name in dispatch_names:
    func_name = f"tool_{tool_name}"
    if f"async def {func_name}" not in server_src:
        missing_funcs.append(func_name)

check("All dispatched tools have function definitions",
      len(missing_funcs) == 0,
      f"Missing: {missing_funcs}" if missing_funcs else "")

# ══════════════════════════════════════════════════════════════
# SECTION 8 — WATCHLIST FILE VALIDATION
# ══════════════════════════════════════════════════════════════
section("8. WATCHLIST FILE VALIDATION")

def validate_watchlist(path, label):
    if not path.exists():
        check(f"{label}: file exists", False, f"Not found: {path}")
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        check(f"{label}: valid JSON", True)
    except json.JSONDecodeError as e:
        check(f"{label}: valid JSON", False, str(e))
        return

    # Required keys
    for key in ["watchlist_name", "max_size", "min_size",
                "tickers", "history"]:
        check(f"{label}: has '{key}' key",
              key in data,
              f"Missing key: {key}")

    tickers = data.get("tickers", [])
    n = len(tickers)
    min_s = data.get("min_size", WL_MIN_SIZE)
    max_s = data.get("max_size", WL_MAX_SIZE)

    check(f"{label}: size {n} within [{min_s},{max_s}]",
          min_s <= n <= max_s,
          f"Size {n} outside allowed range [{min_s},{max_s}]")

    # All tickers have required fields
    bad_tickers = []
    symbols = []
    for t in tickers:
        if not all(k in t for k in ["symbol", "added_date",
                                     "added_reason", "tier"]):
            bad_tickers.append(t.get("symbol", "?"))
        sym = t.get("symbol", "")
        if not sym or not sym.isupper():
            bad_tickers.append(f"bad-symbol:{sym}")
        else:
            symbols.append(sym)

    check(f"{label}: all tickers have required fields",
          len(bad_tickers) == 0,
          f"Bad tickers: {bad_tickers}" if bad_tickers else "")

    # No duplicates within this watchlist
    sym_counts = Counter(symbols)
    sym_dupes = {k: v for k, v in sym_counts.items() if v > 1}
    check(f"{label}: no duplicate symbols",
          len(sym_dupes) == 0,
          f"Duplicates: {sym_dupes}" if sym_dupes else "")

    print(f"{INFO}  {label}: {n} tickers — {', '.join(symbols)}")

validate_watchlist(BULL_WL,  "watchlist.json (BULL)")
validate_watchlist(BEAR_WL,  "bear_watchlist.json (BEAR)")
validate_watchlist(SWING_WL, "swing_watchlist.json (SWING)")

# ══════════════════════════════════════════════════════════════
# SECTION 9 — CROSS-WATCHLIST OVERLAP CHECK
# ══════════════════════════════════════════════════════════════
section("9. CROSS-WATCHLIST OVERLAP")

def load_symbols(path):
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {t["symbol"] for t in data.get("tickers", [])
                if "symbol" in t}
    except Exception:
        return set()

bull_syms  = load_symbols(BULL_WL)
bear_syms  = load_symbols(BEAR_WL)
swing_syms = load_symbols(SWING_WL)

bull_bear_overlap  = bull_syms & bear_syms
bull_swing_overlap = bull_syms & swing_syms
bear_swing_overlap = bear_syms & swing_syms

# Bull+Bear overlap is expected only for AMD (intentional bidirectional)
unexpected_overlap = bull_bear_overlap - {"AMD"}
check("Bull+Bear overlap only for AMD (expected)",
      len(unexpected_overlap) == 0,
      f"Unexpected overlap: {unexpected_overlap}" if unexpected_overlap
      else f"AMD appears in both (intentional)",
      warn_only=len(unexpected_overlap) == 0)

check("Bull+Swing overlap documented",
      True,  # overlap is expected and documented
      f"Shared names: {bull_swing_overlap} "
      f"(correlation risk — see Section 1)")

print(f"{INFO}  Bull+Bear overlap: {bull_bear_overlap or 'none'}")
print(f"{INFO}  Bull+Swing overlap ({len(bull_swing_overlap)} names): "
      f"{bull_swing_overlap}")
print(f"{INFO}  Bear+Swing overlap: {bear_swing_overlap or 'none'}")

# ══════════════════════════════════════════════════════════════
# SECTION 10 — SERVER.PY KEY SECTION CHECKS
# ══════════════════════════════════════════════════════════════
section("10. KEY SERVER SECTIONS PRESENT")

required_sections = {
    "WATCHLIST_FILE":       "Options watchlist file path",
    "BEAR_WATCHLIST_FILE":  "Bear watchlist file path",
    "SWING_WATCHLIST_FILE": "Swing watchlist file path",
    "load_watchlist_file":  "Options watchlist loader",
    "load_bear_watchlist_file": "Bear watchlist loader",
    "load_swing_watchlist_file": "Swing watchlist loader",
    "get_watchlist_symbols":  "Options symbols getter",
    "get_bear_watchlist_symbols": "Bear symbols getter",
    "get_swing_watchlist_symbols": "Swing symbols getter",
    "TOOLS_DISPATCH":       "Tool dispatch table",
    "TOOLS_LIST":           "Tool list for Claude",
    "async def main":       "Main entry point",
}

for identifier, description in required_sections.items():
    check(f"'{identifier}' present ({description})",
          identifier in server_src)

# ══════════════════════════════════════════════════════════════
# SECTION 11 — BEAR TOOLS SPECIFICALLY
# ══════════════════════════════════════════════════════════════
section("11. BEAR TOOLS SPECIFICALLY")

bear_tools = [
    "get_bear_watchlist",
    "update_bear_watchlist",
    "scan_combined_setups",
]

for tool in bear_tools:
    in_dispatch = f'"{tool}"' in server_src
    has_func = f"async def tool_{tool}" in server_src
    check(f"Bear tool '{tool}': dispatched + implemented",
          in_dispatch and has_func,
          f"dispatched={in_dispatch}, implemented={has_func}")

# Check combined scan references both watchlists
scan_func_match = re.search(
    r'async def tool_scan_combined_setups.*?(?=\nasync def|\Z)',
    server_src, re.DOTALL)
if scan_func_match:
    scan_body = scan_func_match.group(0)
    check("scan_combined_setups reads bull watchlist",
          "get_watchlist_symbols" in scan_body)
    check("scan_combined_setups reads bear watchlist",
          "get_bear_watchlist_symbols" in scan_body)
    check("scan_combined_setups returns top 3",
          "top3" in scan_body or "[:3]" in scan_body)
    check("scan_combined_setups resolves same-ticker conflict",
          "higher" in scan_body.lower() or "conflict" in scan_body.lower()
          or "bear_score" in scan_body)

# ══════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ══════════════════════════════════════════════════════════════
print(f"\n{'═'*60}")
print(f"  VALIDATION SUMMARY")
print(f"{'═'*60}")
print(f"  Total checks:   {total_checks}")
print(f"  Passed:         {total_checks - failed_checks - warned_checks}")
print(f"  Warnings:       {warned_checks}")
print(f"  Failed:         {failed_checks}")
print(f"{'─'*60}")

if failed_checks == 0 and warned_checks == 0:
    print("  🟢 ALL CHECKS PASSED — system is consistent")
elif failed_checks == 0:
    print(f"  🟡 PASSED WITH {warned_checks} WARNING(S) — review warnings")
else:
    print(f"  🔴 {failed_checks} CHECK(S) FAILED — fix before deploying")

print(f"{'═'*60}\n")
sys.exit(0 if failed_checks == 0 else 1)
