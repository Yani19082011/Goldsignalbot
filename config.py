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


# --- Data source -----------------------------------------------------------
# Yahoo Finance ticker for spot gold in USD. "GC=F" (Comex futures) is used
# automatically as a fallback if this one returns no data.
GOLD_TICKER = os.environ.get("GOLD_TICKER", "XAUUSD=X")
GOLD_TICKER_FALLBACK = os.environ.get("GOLD_TICKER_FALLBACK", "GC=F")

# --- Scan schedule -----------------------------------------------------------
# How often (minutes) the background loop re-checks the market.
CHECK_INTERVAL_MINUTES = _int("CHECK_INTERVAL_MINUTES", 30)

# --- Confluence strategy -----------------------------------------------------
# Number of the 5 confluence conditions (see signals.py) that must agree
# before an alert is sent. 4 or 5 = high conviction, 3 = more alerts/noisier.
CONFLUENCE_THRESHOLD = _int("CONFLUENCE_THRESHOLD", 4)

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
REALERT_COOLDOWN_HOURS = _float("REALERT_COOLDOWN_HOURS", 6)

# --- Email (Resend API) ------------------------------------------------------
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# Resend's shared sandbox sender. Works without verifying your own domain,
# but Resend will only actually deliver to the email address tied to your
# Resend account until you verify a domain at resend.com/domains.
ALERT_FROM_EMAIL = os.environ.get("ALERT_FROM_EMAIL", "GoldSignalBot <onboarding@resend.com>")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "y14962461@gmail.com")

# --- Web server ---------------------------------------------------------
PORT = _int("PORT", 10000)

# Send a one-off test email at startup so you know delivery works.
SEND_TEST_EMAIL_ON_START = _bool("SEND_TEST_EMAIL_ON_START", False)
