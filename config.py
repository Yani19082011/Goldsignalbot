"""
Configuration for GoldSignalBot, loaded from environment variables.
Copy .env.example to .env locally, or set these as Environment Variables
in your Render service settings.
"""
import os


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    val = os.environ.get(name)
    try:
        return float(val) if val is not None else default
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    val = os.environ.get(name)
    try:
        return int(val) if val is not None else default
    except ValueError:
        return default


# --- Data sources ------------------------------------------------------------
# Candles are fetched from several providers, in this order, so a single
# provider being down, rate-limited, or blocking cloud IPs doesn't take the
# bot offline:
#   1. Yahoo Finance, primary ticker
#   2. Yahoo Finance, fallback ticker
#   3. Twelve Data (only if TWELVEDATA_API_KEY is set - free key at
#      twelvedata.com, no card required, 800 requests/day on the free plan)
#   4. stooq.com daily candles (last resort, coarser, but keeps the bot alive)
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")

GOLD_TICKER = os.environ.get("GOLD_TICKER", "XAUUSD=X")
GOLD_TICKER_FALLBACK = os.environ.get("GOLD_TICKER_FALLBACK", "GC=F")
GOLD_TWELVEDATA_SYMBOL = os.environ.get("TWELVEDATA_SYMBOL", os.environ.get("GOLD_TWELVEDATA_SYMBOL", "XAU/USD"))
GOLD_STOOQ_SYMBOL = os.environ.get("GOLD_STOOQ_SYMBOL", "xauusd")

# USA Tech 100 / Nasdaq-100 (the "TECH100" CFD most brokers, incl. eToro,
# offer). Tracked via Nasdaq-100 futures (NQ=F) primarily - futures trade
# near-24h like the CFD does, unlike the ^NDX cash index which only moves
# during US market hours. QQQ (the Nasdaq-100 ETF) is used for Twelve Data
# since Twelve Data's free plan covers US-listed ETFs/stocks, not futures.
ENABLE_TECH100 = _bool("ENABLE_TECH100", True)
TECH100_TICKER = os.environ.get("TECH100_TICKER", "NQ=F")
TECH100_TICKER_FALLBACK = os.environ.get("TECH100_TICKER_FALLBACK", "^NDX")
TECH100_TWELVEDATA_SYMBOL = os.environ.get("TECH100_TWELVEDATA_SYMBOL", "QQQ")
TECH100_STOOQ_SYMBOL = os.environ.get("TECH100_STOOQ_SYMBOL", "qqq.us")

# Every instrument the background loop checks each cycle. Set ENABLE_TECH100
# to false in Render's Environment tab to go back to gold-only.
INSTRUMENTS = [
    {
        "key": "GOLD",
        "name": "злато (XAU/USD)",
        "currency": "$",
        "yahoo_ticker": GOLD_TICKER,
        "yahoo_fallback": GOLD_TICKER_FALLBACK,
        "twelvedata_symbol": GOLD_TWELVEDATA_SYMBOL,
        "stooq_symbol": GOLD_STOOQ_SYMBOL,
    },
]
if ENABLE_TECH100:
    INSTRUMENTS.append({
        "key": "TECH100",
        "name": "USA Tech 100 (Nasdaq-100)",
        "currency": "$",
        "yahoo_ticker": TECH100_TICKER,
        "yahoo_fallback": TECH100_TICKER_FALLBACK,
        "twelvedata_symbol": TECH100_TWELVEDATA_SYMBOL,
        "stooq_symbol": TECH100_STOOQ_SYMBOL,
    })

# Candle size used for the indicators. Shorter = faster-reacting but noisier.
# Yahoo/Twelve Data both support: 5m, 15m, 30m, 60m.
CANDLE_INTERVAL = os.environ.get("CANDLE_INTERVAL", "15m")

# --- Scan schedule -----------------------------------------------------------
# How often (minutes) the background loop re-checks the market. Checking
# much more often than the candle size above just re-reads the same
# still-forming candle - 5 min against 15m candles means you hear about a
# new signal within 5 minutes of it confirming, without hammering the data
# providers on every single check.
CHECK_INTERVAL_MINUTES = _int("CHECK_INTERVAL_MINUTES", 5)

# --- Confluence strategy -----------------------------------------------------
# Number of the 5 confluence conditions (see signals.py) that must agree
# before an alert is sent. 4 or 5 = high conviction, 3 = more alerts/noisier.
CONFLUENCE_THRESHOLD = _int("CONFLUENCE_THRESHOLD", 3)

EMA_FAST = _int("EMA_FAST", 9)
EMA_MID = _int("EMA_MID", 21)
EMA_SLOW = _int("EMA_SLOW", 50)
RSI_PERIOD = _int("RSI_PERIOD", 14)
RSI_OVERSOLD = _float("RSI_OVERSOLD", 35)
RSI_OVERBOUGHT = _float("RSI_OVERBOUGHT", 65)
MACD_FAST = _int("MACD_FAST", 12)
MACD_SLOW = _int("MACD_SLOW", 26)
MACD_SIGNAL = _int("MACD_SIGNAL", 9)
BB_PERIOD = _int("BB_PERIOD", 20)
BB_STD = _float("BB_STD", 2.0)
ATR_PERIOD = _int("ATR_PERIOD", 14)

# Only re-alert on the SAME signal type after this many hours have passed,
# even if the confluence score stays above the threshold on every check.
REALERT_COOLDOWN_HOURS = _float("REALERT_COOLDOWN_HOURS", 0.1667)  # ~10 minutes

# --- Email (Resend API) ------------------------------------------------------
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# Resend's shared sandbox sender domain is "resend.dev" (not "resend.com" -
# that one requires your own verified domain and will 403). Works without
# verifying your own domain, but Resend will only actually deliver to the
# email address tied to your Resend account until you verify a domain at
# resend.com/domains.
ALERT_FROM_EMAIL = os.environ.get("ALERT_FROM_EMAIL", "GoldSignalBot <onboarding@resend.dev>")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "yani.kolev2011@gmail.com")

# --- Web server ---------------------------------------------------------
PORT = _int("PORT", 10000)

# Send a one-off test email at startup so you know delivery works.
SEND_TEST_EMAIL_ON_START = _bool("SEND_TEST_EMAIL_ON_START", False)
