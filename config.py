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

# По заявка (10.10, обновено 13.10): GC=F (COMEX gold futures) е тикерът,
# върху който шампионската стратегия (quiet_accumulation+ema_trend, виж
# champion_strategy.py) беше честно backtest-вана и валидирана (63.3% win
# rate на 123 сигнала, пълна ~880-дневна история; out-of-sample 62.9%->
# 64.9%; 4 непресичащи се тримесечия 53.8%/69.4%/63.6%/66.7% - нито едно
# под 50%) - затова е primary сега, не fallback. XAUUSD=X (спот
# форекс-стил тикер) е различен инструмент с различни часове на търговия и
# форма на свещите, затова е само резервен вариант, ако GC=F временно не
# отговори.
GOLD_TICKER = os.environ.get("GOLD_TICKER", "GC=F")
GOLD_TICKER_FALLBACK = os.environ.get("GOLD_TICKER_FALLBACK", "XAUUSD=X")
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
# По заявка (10.10, "старите са за nasdaq 100" - ботът трябваше да е за
# злато): дефолтите са обърнати обратно - златото (шампионската стратегия)
# е включено по подразбиране, Tech100 е изключен. Tech100 поддръжката
# (стария confluence engine) остава изцяло на мястото си - просто сложи
# ENABLE_TECH100=true в Render, ако го искаш пак (може и двете едновременно).
ENABLE_TECH100 = _bool("ENABLE_TECH100", False)
ENABLE_GOLD = _bool("ENABLE_GOLD", True)
TECH100_TICKER = os.environ.get("TECH100_TICKER", "NQ=F")
TECH100_TICKER_FALLBACK = os.environ.get("TECH100_TICKER_FALLBACK", "^NDX")
TECH100_TWELVEDATA_SYMBOL = os.environ.get("TECH100_TWELVEDATA_SYMBOL", "QQQ")
TECH100_STOOQ_SYMBOL = os.environ.get("TECH100_STOOQ_SYMBOL", "qqq.us")
# Nasdaq-100 index level / QQQ share price, approx. (confirmed ~41.48 from
# the user's own screenshot on 18.09 - $29,823.67 CFD vs $718.96 QQQ).
TECH100_ETF_SCALE = _float("TECH100_ETF_SCALE", 41.5)

# По заявка (10.10): златото вече НЕ минава през стария continuous
# confluence engine по-долу (INSTRUMENTS/CHECK_INTERVAL_MINUTES) - то си
# има собствен, отделен checkpoint-базиран цикъл в app.py, управляван от
# champion_strategy.py (виж CHAMPION_* по-долу). Този речник се гради
# ВИНАГИ (независимо от ENABLE_GOLD), за да го ползват champion-цикъла и
# тестовете - ENABLE_GOLD решава само дали app.py СТАРТИРА този цикъл.
GOLD_INSTRUMENT = {
    "key": "GOLD",
    "name": "злато (XAU/USD, GC=F)",
    "currency": "$",
    "yahoo_ticker": GOLD_TICKER,
    "yahoo_fallback": GOLD_TICKER_FALLBACK,
    "twelvedata_symbol": GOLD_TWELVEDATA_SYMBOL,
    "stooq_symbol": GOLD_STOOQ_SYMBOL,
    "sanity_min": _float("GOLD_SANITY_MIN", 800.0),
    "sanity_max": _float("GOLD_SANITY_MAX", 8000.0),
}

# Инструменти на стария continuous confluence engine (CHECK_INTERVAL_MINUTES
# по-долу) - вече само Tech100 (изключен по подразбиране). Златото НЕ е
# тук - виж GOLD_INSTRUMENT/CHAMPION_* по-горе/долу.
INSTRUMENTS = []
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
# still-forming candle. By user request (24.09): matched to
# REALERT_COOLDOWN_HOURS (~25 min) so the mental model is simple - check,
# email if there's a signal, check again ~25 min later, email again if it's
# still there (or a new one shows up) - instead of checking every 15 min
# but only being ALLOWED to re-email every 25.
CHECK_INTERVAL_MINUTES = _int("CHECK_INTERVAL_MINUTES", 25)

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

# --- Шампионска стратегия за злато (quiet_accumulation + ema_trend) ---------
# намерена и честно валидирана чрез разширен backtest search (GoldBacktest
# проект - gold_hour_structure_search.py/gold_quiet_accumulation_deepdive.py/
# gold_combo_oos_check.py, 47 стратегии + комбинации, тествани на ВСИЧКИ 24
# часа на денонощието): 63.3% win rate на 123 сигнала (пълна ~880-дневна
# история), out-of-sample 62.9%->64.9% (не деградира), 4 непресичащи се
# тримесечия 53.8%/69.4%/63.6%/66.7% (нито едно под 50%), random baseline
# ~47.4%. Виж champion_strategy.py за пълната логика и честните уговорки.
#
# ВАЖНО: валидирано е САМО с проверка в 09:00 Europe/Sofia (24-часов TP/SL
# хоризонт) - други часове бяха тествани отделно и се представят различно,
# затова checkpoint-ът по подразбиране сега е само този ЕДИН час на ден
# (преди - 09:00/16:30/23:00, за старата quiet_accumulation+session_momentum
# комбинация). Смени CHAMPION_CHECKPOINTS, ако искаш да добавиш други часове,
# но имай предвид, че само 09:00 е честно backtest-ван с тази комбинация.
def _parse_checkpoints(raw: str):
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            h, m = part.split(":")
            out.append((int(h), int(m)))
        except ValueError:
            continue
    return out or [(9, 0)]


CHAMPION_CHECKPOINTS = _parse_checkpoints(os.environ.get("CHAMPION_CHECKPOINTS", "09:00"))
CHAMPION_CHECKPOINTS_TZ = os.environ.get("CHAMPION_CHECKPOINTS_TZ", ACTIVE_HOURS_TZ)

# 1:1 ATR risk:reward (Рунд 2 от backtest-а, по твое желание - "не искам tp
# да е нещо грандиозно"). Промяната тук НЕ преизчислява backtest резултата
# по-горе - те са валидирани точно с 1.5/1.5, не променяй без нов backtest.
CHAMPION_STOP_ATR_MULT = _float("CHAMPION_STOP_ATR_MULT", 1.5)
CHAMPION_TP_ATR_MULT = _float("CHAMPION_TP_ATR_MULT", 1.5)

# Шампионската стратегия е валидирана точно на 1-часови (GC=F) свещи - не
# сменяй това, без да пуснеш нов backtest на новия timeframe. Периодът на
# изтегляне (60 дни) дава ~1000+ часови свещи - достатъчно за 100-свещния
# ATR прозорец на quiet_accumulation плюс буфер история.
GOLD_CANDLE_INTERVAL = os.environ.get("GOLD_CANDLE_INTERVAL", "60m")
GOLD_FETCH_PERIOD = os.environ.get("GOLD_FETCH_PERIOD", "60d")

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

# By user request (25.09 - "искам наистина да гледа графиката от предните
# 1-2 дена и по-реалистични take profit-и"): take-profit/stop-loss should
# reflect what ACTUALLY happened after a similar recent setup far more
# often than they currently do. ANALOG_MAX_DISTANCE above is deliberately
# strict (see its comment) because it also gates the 7th CONFLUENCE VOTE -
# a shaky match shouldn't be able to help trigger a BUY/SELL email on its
# own. But that strictness was tested (simulated on realistic-noise 15m
# data) to only produce a "confident" match on ~9% of checks - so 91% of
# the time, TP/SL was silently falling back to the generic ATR multiple
# below instead of the real-history-based sizing this feature exists for.
# This separate, looser threshold is used ONLY for sizing, never for the
# confluence vote - a so-so shape match is still useful for "roughly how
# far did this kind of move tend to run", even when it's not solid enough
# to trust as a signal on its own. Loosening only this (not
# ANALOG_MAX_DISTANCE) means TP/SL now reflects a real recent analog on
# roughly a quarter of checks instead of ~1 in 11, without changing which
# checks actually trigger a BUY/SELL email.
ANALOG_SIZING_MAX_DISTANCE = _float("ANALOG_SIZING_MAX_DISTANCE", 0.35)

# By user request (18.09): only email BUY signals, never SELL - later
# reverted (24.09): with BUY-only, the inbox always looks the same
# regardless of what the market actually does, which is hard to tell apart
# from the bot being stuck/broken. Now both directions are emailed by
# default, so what you see genuinely reflects the confluence logic - BUY,
# SELL, or quiet, whichever the market actually shows. SELL signals are
# always computed and logged/visible on "/" either way. Set
# ALERT_ONLY_BUY=true in Render if you want to go back to BUY-only.
ALERT_ONLY_BUY = _bool("ALERT_ONLY_BUY", False)

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

# By user request (24.09): "ако потенциал за short/long e по-висок, да
# даде повече време да хитне take profit-a" - a high-conviction signal
# (score >= STRONG_SIGNAL_SCORE_THRESHOLD out of 7) usually implies a
# further take-profit target (see the analog-based sizing in signals.py -
# a stronger/more confident analog match tends to carry a bigger realized
# move), so re-alerting on it again after only the normal ~25 min is often
# premature - the trade hasn't had time to develop toward that target yet.
# After sending a signal whose score met this threshold, the NEXT alert for
# that same direction waits STRONG_SIGNAL_COOLDOWN_HOURS (~35 min) instead
# of the usual REALERT_COOLDOWN_HOURS. A normal-strength signal (below the
# threshold) keeps the usual cooldown - see _should_send() in app.py.
STRONG_SIGNAL_SCORE_THRESHOLD = _int("STRONG_SIGNAL_SCORE_THRESHOLD", 5)
STRONG_SIGNAL_COOLDOWN_HOURS = _float("STRONG_SIGNAL_COOLDOWN_HOURS", 35 / 60)  # ~35 minutes

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
