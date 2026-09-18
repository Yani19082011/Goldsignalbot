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
# during US market hours.
#
# BUG FOUND 18.09 (confirmed by the user's own screenshots): the Twelve Data
# symbol here used to default to "QQQ" (the Nasdaq-100 ETF). Yahoo Finance
# fails very often from Render's cloud IP (the same known issue as gold),
# so the bot was silently falling back to Twelve Data/QQQ most of the time -
# but QQQ's SHARE PRICE (~$718 that day) is on a totally different scale
# than the actual Nasdaq-100 index/CFD level (~$29,800 that day, ~41x
# higher) because of QQQ's 2011 share split. The email showed a real,
# correctly-computed signal, just on the wrong price scale entirely - stop
# loss/take profit numbers were meaningless next to the real chart. Fixed
# two ways: (1) Twelve Data now requests the actual index ("NDX") instead
# of the ETF, so it's the right scale when it works; (2) get_candles() below
# now sanity-checks the fetched price against each instrument's expected
# range and rejects/skips a provider whose reading is wildly out of scale,
# instead of ever emailing a number that doesn't match the real market -
# see "sanity_min"/"sanity_max" below and _passes_sanity_check() in
# data_fetch.py. This second part guards against this whole class of bug
# (wrong symbol, wrong currency, decimal error, ...) for both instruments,
# not just this one incident.
ENABLE_TECH100 = _bool("ENABLE_TECH100", True)
TECH100_TICKER = os.environ.get("TECH100_TICKER", "NQ=F")
TECH100_TICKER_FALLBACK = os.environ.get("TECH100_TICKER_FALLBACK", "^NDX")
TECH100_TWELVEDATA_SYMBOL = os.environ.get("TECH100_TWELVEDATA_SYMBOL", "NDX")
TECH100_STOOQ_SYMBOL = os.environ.get("TECH100_STOOQ_SYMBOL", "^ndx")

# Every instrument the background loop checks each cycle. Set ENABLE_TECH100
# to false in Render's Environment tab to go back to gold-only.
# sanity_min/sanity_max are a loose plausibility range (generous on purpose -
# not a precision check, just "is this even remotely the right instrument
# and scale") - see _passes_sanity_check() in data_fetch.py.
INSTRUMENTS = [
    {
        "key": "GOLD",
        "name": "злато (XAU/USD)",
        "currency": "$",
        "yahoo_ticker": GOLD_TICKER,
        "yahoo_fallback": GOLD_TICKER_FALLBACK,
        "twelvedata_symbol": GOLD_TWELVEDATA_SYMBOL,
        "stooq_symbol": GOLD_STOOQ_SYMBOL,
        "sanity_min": _float("GOLD_SANITY_MIN", 800.0),
        "sanity_max": _float("GOLD_SANITY_MAX", 8000.0),
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
        "sanity_min": _float("TECH100_SANITY_MIN", 8000.0),
        "sanity_max": _float("TECH100_SANITY_MAX", 60000.0),
    })

# Candle size used for the indicators. Shorter = faster-reacting but noisier.
# Yahoo/Twelve Data both support: 5m, 15m, 30m, 60m.
CANDLE_INTERVAL = os.environ.get("CANDLE_INTERVAL", "15m")

# --- Scan schedule -----------------------------------------------------------
# How often (minutes) the background loop re-checks the market. Checking
# much more often than the candle size above just re-reads the same
# still-forming candle.
CHECK_INTERVAL_MINUTES = _int("CHECK_INTERVAL_MINUTES", 15)

# --- Active window -----------------------------------------------------------
# By user request (18.09): only check/alert Monday-Friday, 07:30-23:00
# (Europe/Sofia) - no weekend or late-night emails. Outside this window the
# background loop does nothing (no provider calls, no emails) and just
# waits for the next tick. Set ACTIVE_DAYS_ONLY=false or widen the hours
# below (e.g. back to 00:00-23:59) to go back to round-the-clock checking -
# gold/forex markets do trade nearly 24/5, this window is purely about when
# YOU want to be alerted, not when the market is open.
ACTIVE_DAYS_ONLY = _bool("ACTIVE_DAYS_ONLY", True)  # Monday-Friday only
ACTIVE_HOURS_TZ = os.environ.get("ACTIVE_HOURS_TZ", "Europe/Sofia")
ACTIVE_START_HOUR = _int("ACTIVE_START_HOUR", 7)
ACTIVE_START_MINUTE = _int("ACTIVE_START_MINUTE", 30)
ACTIVE_END_HOUR = _int("ACTIVE_END_HOUR", 23)
ACTIVE_END_MINUTE = _int("ACTIVE_END_MINUTE", 0)

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
