#!/usr/bin/env python3
"""
ANTI-CRASH Simmer FastLoop Bot
- Sync Binance 1m candle
- Trade only when 60–120s to expiry
- Never crash on bad API / bad data
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request

# ===================== SAFE STDOUT =====================
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ===================== CONSTANTS =====================
SIMMER_BASE = os.environ.get("SIMMER_API_BASE", "https://api.simmer.markets")

MIN_TIME_TO_EXPIRY = 60
MAX_TIME_TO_EXPIRY = 120

ASSET_SYMBOL = "BTCUSDT"
LOOKBACK = 5

# ===================== UTILS =====================
def now_utc():
    return datetime.now(timezone.utc)


def sleep_until_next_minute(offset=2):
    now = now_utc()
    nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    time.sleep(max(0, (nxt - now).total_seconds() + offset))


def log(msg):
    print(f"[{now_utc().strftime('%H:%M:%S')}] {msg}")


def get_api_key():
    key = os.getenv("SIMMER_API_KEY") or os.getenv("RAILWAY_SIMMER_API_KEY")
    if not key:
        log("❌ SIMMER_API_KEY not set — sleeping")
        time.sleep(30)
        return None
    return key


def safe_request(url, method="GET", data=None, headers=None, timeout=10):
    try:
        headers = headers or {}
        headers.setdefault("User-Agent", "fastloop-bot/1.0")
        body = json.dumps(data).encode() if data else None
        req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log(f"⚠️ request failed: {e}")
        return None


def simmer_request(path, api_key, method="GET", data=None):
    headers = {"Authorization": f"Bearer {api_key}"}
    return safe_request(SIMMER_BASE + path, method, data, headers)


# ===================== MARKET DISCOVERY =====================
def parse_end_time(question):
    import re
    m = re.search(r'(\w+ \d+).*?-\s*(\d{1,2}:\d{2}(AM|PM))', question or "")
    if not m:
        return None
    try:
        year = now_utc().year
        dt = datetime.strptime(
            f"{m.group(1)} {year} {m.group(2)}",
            "%B %d %Y %I:%M%p"
        )
        return dt.replace(tzinfo=timezone.utc) + timedelta(hours=5)
    except Exception:
        return None


def discover_markets():
    url = (
        "https://gamma-api.polymarket.com/markets"
        "?limit=20&closed=false&tag=crypto&order=createdAt&ascending=false"
    )
    data = safe_request(url)
    if not isinstance(data, list):
        return []

    markets = []
    for m in data:
        q = (m.get("question") or "").lower()
        if "bitcoin up or down" not in q:
            continue
        end_time = parse_end_time(m.get("question"))
        markets.append({
            "slug": m.get("slug"),
            "question": m.get("question"),
            "end_time": end_time,
            "outcome_prices": m.get("outcomePrices"),
        })
    return markets


def select_market(markets):
    now = now_utc()
    for m in markets:
        if not m["end_time"]:
            continue
        rem = (m["end_time"] - now).total_seconds()
        if MIN_TIME_TO_EXPIRY < rem <= MAX_TIME_TO_EXPIRY:
            return m
    return None


# ===================== SIGNAL =====================
def get_binance_momentum():
    url = f"https://api.binance.com/api/v3/klines?symbol={ASSET_SYMBOL}&interval=1m&limit={LOOKBACK}"
    candles = safe_request(url)
    if not isinstance(candles, list) or len(candles) < 2:
        return None

    try:
        open_p = float(candles[0][1])
        close_p = float(candles[-1][4])
        pct = (close_p - open_p) / open_p * 100
        return {
            "pct": pct,
            "dir": "up" if pct > 0 else "down",
        }
    except Exception:
        return None


# ===================== STRATEGY =====================
def run_cycle():
    api_key = get_api_key()
    if not api_key:
        return

    markets = discover_markets()
    if not markets:
        log("⏸ no markets")
        return

    market = select_market(markets)
    if not market:
        log("⏸ no market in 60–120s window")
        return

    # Safe price parse
    try:
        raw = market["outcome_prices"]
        prices = raw if isinstance(raw, list) else json.loads(raw or "[]")
        yes_price = float(prices[0]) if prices else 0.5
    except Exception:
        yes_price = 0.5

    signal = get_binance_momentum()
    if not signal:
        log("⚠️ no momentum data")
        return

    if abs(signal["pct"]) < 0.5:
        log("⏸ weak momentum")
        return

    side = "YES" if signal["dir"] == "up" else "NO"
    log(f"🎯 SIGNAL {side} | momentum {signal['pct']:+.2f}% | YES {yes_price:.3f}")

    # 👉 Tại đây bạn gắn trade thật nếu muốn


# ===================== MAIN LOOP =====================
def main():
    log("🚀 FastLoop ANTI-CRASH started")
    while True:
        try:
            run_cycle()
        except Exception as e:
            log(f"🔥 UNCAUGHT ERROR: {e}")
        sleep_until_next_minute(offset=2)


if __name__ == "__main__":
    main()
