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
#   4. stooq.com daily candles (last resort - see ALLOW_DAILY_FALLBACK below,
#      OFF by default since 24.09)
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")

# By user request (24.09 - "искам цената да се променя, преди не се
# променяше на всеки имейл"): stooq.com's free endpoint only serves DAILY
# candles, never 15-minute ones. If Yahoo Finance fails (common from
# Render's IP) and no TWELVEDATA_API_KEY is set, the bot used to silently
# fall back to that once-a-day price and keep computing "15-minute"
# indicators on it - which is why the price (and the signal) could look
# frozen for hours: the underlying data genuinely wasn't updating.
# ALLOW_DAILY_FALLBACK=false (the default) stops that: if only daily data
# is available, get_candles() raises instead of quietly using it, so the
# existing consecutive-failure error email fires and tells you to add/check
# TWELVEDATA_API_KEY - instead of you getting a signal that looks live but
# is actually hours stale. Set to true only if you'd rather have a coarse
# daily-only signal than no signal at all (not recommended for this bot's
# whole confluence/analog-match design, which assumes real 15m candles).
ALLOW_DAILY_FALLBACK = _bool("ALLOW_DAILY_FALLBACK", False)

# Per-request network timeout (seconds) for every price-data HTTP call
# (Yahoo, Twelve Data, stooq). By user request (24.09): keep one full check
# cycle fast (~20-30s max end-to-end, ideally much faster) instead of
# possibly hanging - this bounds the worst realistic case (every source
# timing out back-to-back) to roughly 4x this value.
FETCH_TIMEOUT_SECONDS = _int("FETCH_TIMEOUT_SECONDS", 8)

GOLD_TICKER = os.environ.get("GOLD_TICKER", "XAUUSD=X")
GOLD_TICKER_FALLBACK = os.environ.get("GOLD_TICKER_FALLBACK", "GC=F")
GOLD_TWELVEDATA_SYMBOL = os.environ.get("TWELVEDATA_SYMBOL", os.environ.get("GOLD_TWELVEDATA_SYMBOL", "XAU/USD"))
GOLD_STOOQ_SYMBOL = os.environ.get("GOLD_STOOQ_SYMBOL", "xauusd")

# USA Tech 100 / Nasdaq-100 (the "TECH100" CFD most brokers, incl. eToro,
# offer). Tracked via Nasdaq-100 futures (NQ=F) primarily - futures trade
# near-24h like the CFD does, unlike the ^NDX cash index which only moves
# during US market hours.
#
# BUG HISTORY (18.09, both confirmed live by the user's own Render logs):
#   Round 1: Twelve Data defaulted to "QQQ" (the Nasdaq-100 ETF). Yahoo
#     Finance fails very often from Render's cloud IP (same known issue as
#     gold), so the bot was silently falling back to Twelve Data/QQQ most of
#     the time - but QQQ's SHARE PRICE (~$718 that day) is on a totally
#     different scale than the actual Nasdaq-100 index/CFD level (~$29,800,
#     ~41.5x higher) because of QQQ's 2011 share split. The email showed a
#     real, correctly-computed signal on a meaningless price scale.
#   Round 2 (the "fix" for round 1): switched Twelve Data to the index
#     symbol "NDX" directly, and stooq's fallback to "^ndx" - but BOTH come
#     back 404 Not Found (confirmed in Render logs - neither symbol exists
#     on this Twelve Data plan or on stooq's free download endpoint), so
#     EVERY provider failed for TECH100 and it never checked successfully
#     at all after that "fix".
# FIX (this version): go back to the ETF proxies that actually return real
# data (Twelve Data "QQQ", stooq "qqq.us"), but SCALE their OHLC prices by
# TECH100_ETF_SCALE before using them - see _try_twelvedata()/the stooq
# fallback in data_fetch.py. This is mathematically safe for the confluence
# logic: EMA crossovers, RSI, MACD histogram sign, and Bollinger Band
# touches are all scale-invariant (multiplying every price by a constant
# doesn't change any of them), so the BUY/SELL decision is unaffected -
# only the displayed price/stop-loss/take-profit get put back on the real
# index scale. The scale factor is an approximation (QQQ/NDX drifts slowly
# over time due to dividends) - update TECH100_ETF_SCALE in Render
# periodically if the emailed price visibly diverges from your broker's
# TECH100 quote. The sanity_min/sanity_max check below still guards against
# it drifting far enough to matter, or against either symbol ever silently
# starting to fail again.
ENABLE_TECH100 = _bool("ENABLE_TECH100", True)
# By user request (24.09): the bot now tracks USA Tech 100 only by default.
# Gold support is still fully in place (code, tests, email formatting) - set
# ENABLE_GOLD=true in Render's Environment tab to bring it back alongside
# Tech100, no code changes needed.
ENABLE_GOLD = _bool("ENABLE_GOLD", False)
TECH100_TICKER = os.environ.get("TECH100_TICKER", "NQ=F")
TECH100_TICKER_FALLBACK = os.environ.get("TECH100_TICKER_FALLBACK", "^NDX")
TECH100_TWELVEDATA_SYMBOL = os.environ.get("TECH100_TWELVEDATA_SYMBOL", "QQQ")
TECH100_STOOQ_SYMBOL = os.environ.get("TECH100_STOOQ_SYMBOL", "qqq.us")
# Nasdaq-100 index level / QQQ share price, approx. (confirmed ~41.48 from
# the user's own screenshot on 18.09 - $29,823.67 CFD vs $718.96 QQQ).
TECH100_ETF_SCALE = _float("TECH100_ETF_SCALE", 41.5)

# Every instrument the background loop checks each cycle. Set ENABLE_TECH100
# to false in Render's Environment tab to go back to gold-only.
# sanity_min/sanity_max are a loose plausibility range (generous on purpose -
# not a precision check, just "is this even remotely the right instrument
# and scale") - see _passes_sanity_check() in data_fetch.py.
INSTRUMENTS = []
if ENABLE_GOLD:
    INSTRUMENTS.append({
        "key": "GOLD",
        "name": "злато (XAU/USD)",
        "currency": "$",
        "yahoo_ticker": GOLD_TICKER,
        "yahoo_fallback": GOLD_TICKER_FALLBACK,
        "twelvedata_symbol": GOLD_TWELVEDATA_SYMBOL,
        "stooq_symbol": GOLD_STOOQ_SYMBOL,
        "sanity_min": _float("GOLD_SANITY_MIN", 800.0),
        "sanity_max": _float("GOLD_SANITY_MAX", 8000.0),
    })
if ENABLE_TECH100:
    INSTRUMENTS.append({
        "key": "TECH100",
        "name": "USA Tech 100 (Nasdaq-100)",
        "currency": "$",
        "yahoo_ticker": TECH100_TICKER,
        "yahoo_fallback": TECH100_TICKER_FALLBACK,
        "twelvedata_symbol": TECH100_TWELVEDATA_SYMBOL,
        "twelvedata_scale": TECH100_ETF_SCALE,  # QQQ -> index scale; Yahoo needs no scaling
        "stooq_symbol": TECH100_STOOQ_SYMBOL,
        "stooq_scale": TECH100_ETF_SCALE,  # qqq.us -> index scale
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
# Number of the 7 confluence conditions (see signals.py, candlesticks.py,
# analog_matcher.py) that must agree before an alert is sent. Higher =
# fewer, higher-conviction alerts; lower = more alerts/noisier.
CONFLUENCE_THRESHOLD = _int("CONFLUENCE_THRESHOLD", 3)

# --- Analog / historical pattern matching (see analog_matcher.py) ----------
# By user request (24.09): "look at the last 1-2 days for a similar setup,
# see what happened, and use that for realistic (not crazy) TP/SL levels."
# ANALOG_SHAPE_CANDLES: length of the recent "shape" being matched (10 x
# 15m = ~2.5h of price action).
# ANALOG_LOOKBACK_CANDLES: how far back to search for a similar shape (192 x
# 15m = ~2 days of near-continuous trading).
# ANALOG_FOLLOW_CANDLES: how many candles after a historical match to look
# at to see what actually happened (8 x 15m = ~2h of follow-through).
# ANALOG_MAX_DISTANCE: max allowed shape-match distance for a match to be
# trusted at all - a poor match is ignored rather than forced into a signal
# or used for sizing. Shapes are compared z-score-normalized (each window
# rescaled to its own mean=0/std=1 before comparing), so this distance is in
# standard-deviation units, not price or %: 0 = identical shape, larger =
# less alike. Deliberately strict (checked empirically against pure random
# walks searched over ANALOG_LOOKBACK_CANDLES candidates: <1% of the time
# does random noise alone produce a match this close) - the point of this
# feature is a trustworthy, real-outcome-based TP/SL, so a "maybe" match is
# skipped rather than risking a wrong one; it just won't vote/size every
# check, only when something genuinely similar shows up.
ANALOG_SHAPE_CANDLES = _int("ANALOG_SHAPE_CANDLES", 10)
ANALOG_LOOKBACK_CANDLES = _int("ANALOG_LOOKBACK_CANDLES", 192)
ANALOG_FOLLOW_CANDLES = _int("ANALOG_FOLLOW_CANDLES", 8)
ANALOG_MAX_DISTANCE = _float("ANALOG_MAX_DISTANCE", 0.18)

# By user request (18.09): only email BUY signals, never SELL. SELL signals
# are still computed and logged/visible on "/" (useful context - "the bot
# went quiet" is very different from "the bot sees a SELL setup but isn't
# telling you"), just never emailed. Set ALERT_ONLY_BUY=false to go back to
# getting both directions.
ALERT_ONLY_BUY = _bool("ALERT_ONLY_BUY", True)

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
# By user request (24.09, raised from ~10 min): with the active window
# (07:30-23:00 Mon-Fri = ~930 min/day) and a 15-min check interval, a
# 10-minute cooldown could in the worst case (one direction holding all
# day) approach ~93 emails/day - uncomfortably close to Resend's free-tier
# cap of 100 emails/day. 25 minutes brings that worst case down to ~37/day,
# a much safer margin.
REALERT_COOLDOWN_HOURS = _float("REALERT_COOLDOWN_HOURS", 25 / 60)  # ~25 minutes

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
