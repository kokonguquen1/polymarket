#!/usr/bin/env python3
"""
Simmer FastLoop Trading Skill

Trades Polymarket fast markets using CEX momentum.
SYNCED to Binance 1m candle close.
Only trades when <120s to expiry.
"""

import os
import sys
import json
import argparse
import time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# Force line-buffered stdout (Railway / Docker safe)
sys.stdout.reconfigure(line_buffering=True)

# =============================================================================
# CONFIG
# =============================================================================

CONFIG_SCHEMA = {
    "entry_threshold": {"default": 0.05, "env": "SIMMER_SPRINT_ENTRY", "type": float},
    "min_momentum_pct": {"default": 0.5, "env": "SIMMER_SPRINT_MOMENTUM", "type": float},
    "max_position": {"default": 5.0, "env": "SIMMER_SPRINT_MAX_POSITION", "type": float},
    "signal_source": {"default": "binance", "env": "SIMMER_SPRINT_SIGNAL", "type": str},
    "lookback_minutes": {"default": 5, "env": "SIMMER_SPRINT_LOOKBACK", "type": int},
    "min_time_remaining": {"default": 60, "env": "SIMMER_SPRINT_MIN_TIME", "type": int},
    "asset": {"default": "BTC", "env": "SIMMER_SPRINT_ASSET", "type": str},
    "window": {"default": "5m", "env": "SIMMER_SPRINT_WINDOW", "type": str},
    "volume_confidence": {"default": True, "env": "SIMMER_SPRINT_VOL_CONF", "type": bool},
}

TRADE_SOURCE = "sdk:fastloop"
SMART_SIZING_PCT = 0.05
MIN_SHARES_PER_ORDER = 5

# ⏱ fast-market timing window
MIN_TIME_TO_EXPIRY = 60
MAX_TIME_TO_EXPIRY = 120  # 🎯 ONLY trade <120s

SIMMER_BASE = os.environ.get("SIMMER_API_BASE", "https://api.simmer.markets")

ASSET_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

ASSET_PATTERNS = {
    "BTC": ["bitcoin up or down"],
    "ETH": ["ethereum up or down"],
    "SOL": ["solana up or down"],
}

# =============================================================================
# UTILS
# =============================================================================

def sleep_until_next_minute(offset=2):
    """
    Sleep until just AFTER Binance 1m candle close
    offset=2s ensures candle is finalized
    """
    now = datetime.now(timezone.utc)
    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    sleep_sec = (next_minute - now).total_seconds() + offset
    time.sleep(max(0, sleep_sec))


def get_api_key():
    key = os.getenv("SIMMER_API_KEY") or os.getenv("RAILWAY_SIMMER_API_KEY")
    if not key:
        print("❌ Error: SIMMER_API_KEY environment variable not set")
        sys.exit(1)
    return key


def _api_request(url, method="GET", data=None, headers=None, timeout=15):
    try:
        headers = headers or {}
        headers.setdefault("User-Agent", "simmer-fastloop/1.0")
        body = None
        if data:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}


def simmer_request(path, method="GET", data=None, api_key=None):
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return _api_request(f"{SIMMER_BASE}{path}", method, data, headers)

# =============================================================================
# MARKET DISCOVERY
# =============================================================================

def discover_fast_markets(asset="BTC", window="5m"):
    url = (
        "https://gamma-api.polymarket.com/markets"
        "?limit=20&closed=false&tag=crypto&order=createdAt&ascending=false"
    )
    data = _api_request(url)
    if not isinstance(data, list):
        return []

    results = []
    for m in data:
        q = (m.get("question") or "").lower()
        slug = m.get("slug", "")
        if any(p in q for p in ASSET_PATTERNS[asset]) and f"-{window}-" in slug:
            end_time = parse_end_time(m.get("question", ""))
            results.append({
                "question": m.get("question"),
                "slug": slug,
                "end_time": end_time,
                "outcome_prices": m.get("outcomePrices", "[]"),
                "fee_rate_bps": int(m.get("fee_rate_bps", 0)),
            })
    return results


def parse_end_time(question):
    import re
    match = re.search(r'(\w+ \d+).*?-\s*(\d{1,2}:\d{2}(AM|PM))', question)
    if not match:
        return None
    try:
        year = datetime.now(timezone.utc).year
        dt = datetime.strptime(
            f"{match.group(1)} {year} {match.group(2)}",
            "%B %d %Y %I:%M%p"
        )
        return dt.replace(tzinfo=timezone.utc) + timedelta(hours=5)
    except Exception:
        return None


def select_tradeable_market(markets):
    now = datetime.now(timezone.utc)
    candidates = []
    for m in markets:
        if not m["end_time"]:
            continue
        remaining = (m["end_time"] - now).total_seconds()
        if MIN_TIME_TO_EXPIRY < remaining <= MAX_TIME_TO_EXPIRY:
            candidates.append((remaining, m))
    if not candidates:
        return None
    return sorted(candidates, key=lambda x: x[0])[0][1]

# =============================================================================
# SIGNAL
# =============================================================================

def get_binance_momentum(symbol, lookback):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit={lookback}"
    candles = _api_request(url)
    if not isinstance(candles, list) or len(candles) < 2:
        return None

    open_price = float(candles[0][1])
    close_price = float(candles[-1][4])
    momentum = ((close_price - open_price) / open_price) * 100
    return {
        "momentum_pct": momentum,
        "direction": "up" if momentum > 0 else "down",
        "price_now": close_price,
    }

# =============================================================================
# STRATEGY
# =============================================================================

def run_fast_market_strategy(dry_run=True):
    api_key = get_api_key()
    print("\n⚡ FastLoop tick @", datetime.utcnow().strftime("%H:%M:%S"))

    markets = discover_fast_markets("BTC", "5m")
    market = select_tradeable_market(markets)
    if not market:
        print("⏸ No market in 60–120s window")
        return

    prices = json.loads(market["outcome_prices"] or "[]")
    yes_price = float(prices[0]) if prices else 0.5

    signal = get_binance_momentum("BTCUSDT", 5)
    if not signal:
        print("❌ No momentum data")
        return

    if abs(signal["momentum_pct"]) < 0.5:
        print("⏸ Weak momentum")
        return

    side = "yes" if signal["direction"] == "up" else "no"
    print(f"🎯 SIGNAL {side.upper()} | momentum {signal['momentum_pct']:+.2f}% | YES {yes_price:.3f}")

    if dry_run:
        print("🧪 DRY RUN — no trade executed")

# =============================================================================
# MAIN LOOP (SYNCED)
# =============================================================================

def main_loop(args):
    while True:
        try:
            run_fast_market_strategy(dry_run=not args.live)
        except Exception as e:
            print("🔥 Fatal:", e)
        sleep_until_next_minute(offset=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    main_loop(args)
