#!/usr/bin/env python3
"""Re-run only strategies that failed before, with fixed class name detection."""
import subprocess, re, os
from pathlib import Path
from collections import defaultdict

STRATEGIES_DIR = Path("/Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot/user_data/strategies")
FREQTRADE = "/Users/wentixiaogege/anaconda3/envs/freqtrade/bin/freqtrade"
CONFIG = "/Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot/user_data/config_boneblade.json"
DATADIR = "/Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot/user_data/data/binance"
USERDIR = "/Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot/user_data"
TIMERANGE = "20260416-20260516"

def get_class_name(filepath):
    """Extract the strategy class name from a .py file."""
    try:
        content = filepath.read_text()
        for line in content.split('\n'):
            m = re.match(r'class\s+(\w+)\s*\(\s*IStrategy\s*\)', line)
            if m:
                return m.group(1)
        return None
    except:
        return None

# Previously failed strategies (from output)
failed = [
    # No data found (timeframe mismatch)
    ("berlinguyinca", "AverageStrategy"),
    ("berlinguyinca", "BinHV45"),
    ("berlinguyinca", "CCIStrategy"),
    ("berlinguyinca", "Low_BB"),
    ("berlinguyinca", "ReinforcedAverageStrategy"),
    ("berlinguyinca", "ReinforcedSmoothScalp"),
    ("berlinguyinca", "Scalp"),
    ("berlinguyinca", "SmoothScalp"),
    ("complex", "GodStra"),
    ("complex", "Heracles"),
    ("complex", "NostalgiaForInfinityX7"),
    ("experimental", "FreqAIStrategy"),
    ("experimental", "HyperoptableStrategy"),
    ("experimental", "PatternRecognition"),
    ("experimental", "hlhb"),
    ("experimental", "mabStra"),
    ("experimental", "multi_tf"),
    ("futures", "FSupertrendStrategy"),
    ("lookahead_bias", "DevilStra"),
    ("lookahead_bias", "GodStraNew"),
    ("lookahead_bias", "Zeus"),
    ("lookahead_bias", "wtc"),
    ("trend", "CryptoBreakoutStrategy"),
    ("trend", "HyperliquidMomentumStrategy"),
    ("trend", "MultiMa"),
    # Class name mismatch
    ("deepseek", "ghost_v6_5m_short"),
    ("deepseek", "strategy_athena_advanced"),
    ("deepseek", "strategy_bone_blade_advanced"),
    ("deepseek", "strategy_ghost_advanced"),
    ("deepseek", "strategy_whale"),
    ("deepseek", "strategy_whale_advanced"),
    ("ichimoku", "ichiV1"),
    ("ichimoku", "ichiV1_niu"),
    ("ichimoku", "ichiV1_zd"),
    ("experimental", "HyperoptableStrategy"),
    # Other errors
    ("grid", "ScalpingStrategy"),
    ("risk", "FixedRiskRewardLoss"),
]

results = []
for subdir, fname in failed:
    filepath = STRATEGIES_DIR / subdir / f"{fname}.py"
    if not filepath.exists():
        print(f"SKIP: {subdir}/{fname} - file gone", flush=True)
        continue

    # Get actual class name
    class_name = get_class_name(filepath)
    if not class_name:
        print(f"SKIP: {subdir}/{fname} - no class found", flush=True)
        continue

    strategy_path = str(STRATEGIES_DIR / subdir)
    label = f"{subdir}/{class_name}"
    print(f"[rerun] {label}...", flush=True, end=" ")

    try:
        result = subprocess.run([
            FREQTRADE, "backtesting",
            "--config", CONFIG,
            "--strategy", class_name,
            "--strategy-path", strategy_path,
            "--datadir", DATADIR,
            "--userdir", USERDIR,
            "--timerange", TIMERANGE,
        ], capture_output=True, text=True, timeout=120)

        output = result.stdout + result.stderr

        match = re.search(
            r"│\s*" + re.escape(class_name) + r"\s*│\s*(\d+)\s*│\s*([-\d.]+)\s*│\s*([-\d.]+)\s*│\s*([-\d.]+)\s*│.*?│\s*(\d+)\s+\d+\s+(\d+)\s+([\d.]+)\s*│\s*(.*?)\s*│",
            output
        )

        if match:
            trades = int(match.group(1))
            tot_profit = float(match.group(4))
            wins = int(match.group(5))
            losses = int(match.group(6))
            win_rate = float(match.group(7))
            dd = match.group(8).strip()
            results.append((subdir, class_name, fname, trades, tot_profit, win_rate, dd))
            print(f"{trades}T, {tot_profit:+.2f}%, {win_rate:.1f}% win, DD {dd}", flush=True)
        elif "No trades made" in output:
            results.append((subdir, class_name, fname, 0, 0.0, 0.0, "0.00%"))
            print("0 trades", flush=True)
        elif "ERROR" in output or "Impossible" in output:
            err = [l.strip() for l in output.split('\n') if 'ERROR' in l or 'Impossible' in l or 'error' in l.lower()]
            msg = err[-1][-120:] if err else "unknown"
            results.append((subdir, class_name, fname, 0, None, None, f"ERR: {msg}"))
            print(f"ERR: {msg[:100]}", flush=True)
        else:
            results.append((subdir, class_name, fname, 0, None, None, "parse failed"))
            print("parse failed", flush=True)

    except subprocess.TimeoutExpired:
        results.append((subdir, class_name, fname, 0, None, None, "TIMEOUT"))
        print("TIMEOUT", flush=True)

# Print results
print("\n" + "=" * 100)
print(f"{'Subdir':<22} {'Class':<30} {'File':<35} {'Trades':>6} {'Profit%':>9} {'Win%':>7} {'Drawdown/Error'}")
print("-" * 100)

sorted_results = sorted(results, key=lambda x: x[4] if x[4] is not None else -999, reverse=True)

now_working = 0
for subdir, class_name, fname, trades, profit, win_rate, dd in sorted_results:
    profit_str = f"{profit:+.2f}%" if profit is not None else "ERR"
    win_str = f"{win_rate:.1f}%" if isinstance(win_rate, (int, float)) and trades > 0 else "-"
    trades_str = str(trades) if trades > 0 else "0"
    dd_str = dd if dd else ""
    print(f"{subdir:<22} {class_name:<30} {fname:<35} {trades_str:>6} {profit_str:>9} {win_str:>7} {dd_str}")
    if profit is not None and trades > 0:
        now_working += 1

print(f"\nFixed: {now_working}/{len(results)} previously failed strategies now working")
