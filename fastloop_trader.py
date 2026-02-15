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
        head
