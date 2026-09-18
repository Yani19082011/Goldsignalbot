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
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False)
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
    resp = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=20)
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
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    if df.empty or "Close" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    return df[["Open", "High", "Low", "Close", "Volume"]] if "Volume" in df.columns else df.assign(Volume=0)


def get_candles(instrument: dict = None, interval: str = None, period: str = "60d") -> pd.DataFrame:
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
    """
    instrument = instrument or config.INSTRUMENTS[0]
    interval = interval or config.CANDLE_INTERVAL

    def _try_yahoo():
        for ticker in (instrument["yahoo_ticker"], instrument["yahoo_fallback"]):
            if not ticker:
                continue
            try:
                df = _fetch_yfinance(ticker, period=period, interval=interval)
                if not df.empty:
                    log.info("Fetched %d candles from Yahoo Finance (%s, %s, %s)", len(df), instrument["key"], ticker, interval)
                    return df
            except Exception as exc:  # noqa: BLE001
                log.warning("Yahoo Finance fetch failed for %s (%s): %s", instrument["key"], ticker, exc)
        return pd.DataFrame()

    def _try_twelvedata():
        symbol = instrument.get("twelvedata_symbol")
        if not config.TWELVEDATA_API_KEY or not symbol:
            return pd.DataFrame()
        try:
            df = _fetch_twelvedata(symbol, interval)
            if not df.empty:
                log.info("Fetched %d candles from Twelve Data (%s, %s, %s)", len(df), instrument["key"], symbol, interval)
                return df
        except Exception as exc:  # noqa: BLE001
            log.warning("Twelve Data fetch failed for %s: %s", instrument["key"], exc)
        return pd.DataFrame()

    providers = [_try_twelvedata, _try_yahoo] if config.TWELVEDATA_API_KEY else [_try_yahoo, _try_twelvedata]
    for provider in providers:
        df = provider()
        if not df.empty:
            return df

    # Last resort: daily candles from stooq (coarser, but keeps the bot alive)
    symbol = instrument.get("stooq_symbol")
    if symbol:
        try:
            df = _fetch_stooq(symbol)
            if not df.empty:
                log.info("Fetched %d daily candles from stooq.com (%s, fallback)", len(df), instrument["key"])
                return df
        except Exception as exc:  # noqa: BLE001
            log.warning("stooq.com fallback fetch failed for %s: %s", instrument["key"], exc)

    raise RuntimeError(
        f"Could not fetch {instrument['key']} price data from any source (Yahoo Finance, Twelve Data, stooq.com)"
    )


def get_daily_candles(instrument: dict = None, period: str = "1y") -> pd.DataFrame:
    return get_candles(instrument, interval="60m", period=period)
