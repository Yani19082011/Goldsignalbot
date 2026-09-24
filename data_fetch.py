"""
Fetches recent price candles for a configured instrument (gold, Tech100/
Nasdaq-100, ...) from several providers, so the bot keeps working if any
single one is down, rate-limited, or blocks requests coming from a cloud
host's shared IP (a real risk once we're polling every few minutes instead
of every 30 - see CHECK_INTERVAL_MINUTES in config.py).

Order of providers, each one free and needing no paid plan:
  1. Yahoo Finance, primary ticker
  2. Yahoo Finance, fallback ticker
  3. Twelve Data (only used if TWELVEDATA_API_KEY is set)
  4. stooq.com daily candles (last resort, coarser)

Each instrument (see config.INSTRUMENTS) carries its own ticker/symbol for
every provider, since a single symbol rarely exists identically across all
of them (e.g. gold is "XAUUSD=X" on Yahoo but "XAU/USD" on Twelve Data; the
Nasdaq-100 is tracked via futures "NQ=F" on Yahoo but the ETF "QQQ" on
Twelve Data, which doesn't cover futures on its free plan).
"""
import io
import logging

import pandas as pd
import requests
import yfinance as yf

import config

log = logging.getLogger("gold_signal_bot.data")

# Maps our internal interval strings (yfinance's convention) to each other
# provider's own convention.
_TWELVEDATA_INTERVAL = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "60m": "1h",
}


def _fetch_yfinance(ticker: str, period: str, interval: str) -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False,
                      timeout=config.FETCH_TIMEOUT_SECONDS)
    if df is None or df.empty:
        return pd.DataFrame()
    # yfinance sometimes returns MultiIndex columns for a single ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


def _fetch_twelvedata(symbol: str, interval: str, outputsize: int = 500) -> pd.DataFrame:
    if not config.TWELVEDATA_API_KEY or not symbol:
        return pd.DataFrame()
    td_interval = _TWELVEDATA_INTERVAL.get(interval, "15min")
    params = {
        "symbol": symbol,
        "interval": td_interval,
        "outputsize": outputsize,
        "apikey": config.TWELVEDATA_API_KEY,
        "format": "JSON",
        "order": "ASC",
    }
    resp = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=config.FETCH_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "error" or "values" not in data:
        raise RuntimeError(f"Twelve Data error: {data.get('message', data)}")

    rows = data["values"]
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(float) if "volume" in df.columns else 0.0
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _fetch_stooq(symbol: str) -> pd.DataFrame:
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    resp = requests.get(url, timeout=config.FETCH_TIMEOUT_SECONDS)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    if df.empty or "Close" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    return df[["Open", "High", "Low", "Close", "Volume"]] if "Volume" in df.columns else df.assign(Volume=0)


def _scale_ohlc(df: pd.DataFrame, scale: float) -> pd.DataFrame:
    """Multiplies every OHLC price by a constant factor - used when a
    provider only has an ETF/proxy for an instrument (e.g. QQQ standing in
    for the Nasdaq-100 index/CFD - see TECH100_ETF_SCALE in config.py).
    Safe for the confluence logic: EMA crossovers, RSI, MACD histogram
    sign, and Bollinger Band touches are all scale-invariant, so this only
    changes the displayed price/stop-loss/take-profit, never the BUY/SELL
    decision itself."""
    if scale == 1.0 or df.empty:
        return df
    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        out[col] = out[col] * scale
    return out


def _passes_sanity_check(df: pd.DataFrame, instrument: dict, source: str) -> bool:
    """Loose plausibility check on the latest close price - catches the
    whole class of bug where a provider returns real, well-formed data for
    the WRONG thing (wrong symbol, an ETF instead of the index it tracks,
    a currency mismatch, a stale/decimal-shifted reading, ...) and the bot
    would otherwise happily compute a confluence signal and email it,
    looking completely legitimate while being off by an order of magnitude.
    Confirmed real case (18.09): Twelve Data's "QQQ" (the Nasdaq-100 ETF,
    ~$718) got used as a stand-in for the Nasdaq-100 index/CFD (~$29,800) -
    see the long comment in config.py. sanity_min/sanity_max are a generous
    range, not a precision check - the goal is only to reject "obviously
    the wrong instrument," not to validate the exact price.
    """
    sanity_min = instrument.get("sanity_min")
    sanity_max = instrument.get("sanity_max")
    if sanity_min is None or sanity_max is None or df.empty:
        return True
    last_close = float(df["Close"].iloc[-1])
    if sanity_min <= last_close <= sanity_max:
        return True
    log.warning(
        "%s: %s returned a price (%.2f) outside the expected range [%.2f, %.2f] for this "
        "instrument - rejecting this reading as wrong-instrument/wrong-scale, trying the next source.",
        instrument["key"], source, last_close, sanity_min, sanity_max,
    )
    return False


def get_candles(instrument: dict = None, interval: str = None, period: str = "60d", source_info: dict = None) -> pd.DataFrame:
    """
    Returns an OHLCV DataFrame indexed by timestamp, newest last, for the
    given instrument (a dict from config.INSTRUMENTS - defaults to the
    first configured instrument, gold, if omitted for backward compat).
    Tries every configured provider and returns the first one that
    succeeds. Raises RuntimeError only if every source fails.

    Order: if a Twelve Data key is configured, it's tried FIRST, ahead of
    Yahoo Finance. In practice Yahoo Finance blocks/rate-limits requests
    from cloud hosts (Render, AWS, etc.) very consistently - trying it
    first would mean two guaranteed-failing requests (and two stack traces
    in the logs) on every single check. Without a Twelve Data key, Yahoo
    is tried first since it's the only free-without-a-key option.

    If `source_info` (a dict) is passed, it's filled in with which source
    actually served this call (source_info["source"]) - by user request
    (24.09), so app.py can surface it on the "/" health endpoint and you
    can see at a glance whether checks are running on live intraday data
    or (if you've opted into ALLOW_DAILY_FALLBACK) stale daily data.
    """
    instrument = instrument or config.INSTRUMENTS[0]
    interval = interval or config.CANDLE_INTERVAL

    def _try_yahoo():
        for ticker in (instrument["yahoo_ticker"], instrument["yahoo_fallback"]):
            if not ticker:
                continue
            try:
                df = _fetch_yfinance(ticker, period=period, interval=interval)
                if not df.empty and _passes_sanity_check(df, instrument, f"Yahoo Finance ({ticker})"):
                    log.info("Fetched %d candles from Yahoo Finance (%s, %s, %s)", len(df), instrument["key"], ticker, interval)
                    return df, f"Yahoo Finance ({ticker})"
            except Exception as exc:  # noqa: BLE001
                log.warning("Yahoo Finance fetch failed for %s (%s): %s", instrument["key"], ticker, exc)
        return pd.DataFrame(), None

    def _try_twelvedata():
        symbol = instrument.get("twelvedata_symbol")
        if not config.TWELVEDATA_API_KEY or not symbol:
            return pd.DataFrame(), None
        try:
            df = _fetch_twelvedata(symbol, interval)
            df = _scale_ohlc(df, instrument.get("twelvedata_scale", 1.0))
            if not df.empty and _passes_sanity_check(df, instrument, f"Twelve Data ({symbol})"):
                log.info("Fetched %d candles from Twelve Data (%s, %s, %s)", len(df), instrument["key"], symbol, interval)
                return df, f"Twelve Data ({symbol})"
        except Exception as exc:  # noqa: BLE001
            log.warning("Twelve Data fetch failed for %s: %s", instrument["key"], exc)
        return pd.DataFrame(), None

    providers = [_try_twelvedata, _try_yahoo] if config.TWELVEDATA_API_KEY else [_try_yahoo, _try_twelvedata]
    for provider in providers:
        df, source = provider()
        if not df.empty:
            if source_info is not None:
                source_info["source"] = source
            return df

    # Last resort: daily candles from stooq. OFF by default (see
    # ALLOW_DAILY_FALLBACK in config.py, 24.09) - stooq's free endpoint is
    # daily-only, and silently using once-a-day data to drive a "15-minute"
    # confluence/analog-match signal is exactly what made the price (and
    # the signal) look frozen for hours in the past. Better to fail loudly
    # here (-> triggers the existing error email after repeated failures)
    # than to keep emailing stale-looking signals.
    symbol = instrument.get("stooq_symbol")
    if symbol and config.ALLOW_DAILY_FALLBACK:
        try:
            df = _fetch_stooq(symbol)
            df = _scale_ohlc(df, instrument.get("stooq_scale", 1.0))
            if not df.empty and _passes_sanity_check(df, instrument, f"stooq.com ({symbol})"):
                log.warning(
                    "%s: falling back to stooq.com's DAILY candles (ALLOW_DAILY_FALLBACK=true) - "
                    "the price/signal will only update once a day until Yahoo/Twelve Data recover.",
                    instrument["key"],
                )
                if source_info is not None:
                    source_info["source"] = f"stooq.com daily ({symbol})"
                return df
        except Exception as exc:  # noqa: BLE001
            log.warning("stooq.com fallback fetch failed for %s: %s", instrument["key"], exc)
    elif symbol:
        log.warning(
            "%s: Yahoo Finance and Twelve Data both failed/unavailable, and ALLOW_DAILY_FALLBACK=false "
            "so stooq.com's daily-only data was NOT used (would give a stale, wrong-granularity signal). "
            "Add/check TWELVEDATA_API_KEY to fix this properly.",
            instrument["key"],
        )

    sources_tried = "Yahoo Finance, Twelve Data"
    if config.ALLOW_DAILY_FALLBACK:
        sources_tried += ", stooq.com (daily)"
    raise RuntimeError(f"Could not fetch {instrument['key']} intraday price data from any source ({sources_tried})")


def get_daily_candles(instrument: dict = None, period: str = "1y") -> pd.DataFrame:
    return get_candles(instrument, interval="60m", period=period)
