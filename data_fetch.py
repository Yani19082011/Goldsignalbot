"""
Fetches recent gold price candles from several providers, so the bot keeps
working if any single one is down, rate-limited, or blocks requests coming
from a cloud host's shared IP (a real risk once we're polling every few
minutes instead of every 30 - see CHECK_INTERVAL_MINUTES in config.py).

Order of providers, each one free and needing no paid plan:
  1. Yahoo Finance, primary ticker (spot gold XAUUSD=X)
  2. Yahoo Finance, fallback ticker (GC=F, Comex futures)
  3. Twelve Data (only used if TWELVEDATA_API_KEY is set)
  4. stooq.com daily candles (last resort, coarser)
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


def _fetch_twelvedata(interval: str, outputsize: int = 500) -> pd.DataFrame:
    if not config.TWELVEDATA_API_KEY:
        return pd.DataFrame()
    td_interval = _TWELVEDATA_INTERVAL.get(interval, "15min")
    params = {
        "symbol": config.TWELVEDATA_SYMBOL,
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


def _fetch_stooq(symbol: str = "xauusd") -> pd.DataFrame:
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    if df.empty or "Close" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    return df[["Open", "High", "Low", "Close", "Volume"]] if "Volume" in df.columns else df.assign(Volume=0)


def get_candles(interval: str = None, period: str = "60d") -> pd.DataFrame:
    """
    Returns an OHLCV DataFrame indexed by timestamp, newest last.
    Tries every configured provider in order and returns the first one that
    succeeds. Raises RuntimeError only if every source fails.
    """
    interval = interval or config.CANDLE_INTERVAL

    for ticker in (config.GOLD_TICKER, config.GOLD_TICKER_FALLBACK):
        try:
            df = _fetch_yfinance(ticker, period=period, interval=interval)
            if not df.empty:
                log.info("Fetched %d candles from Yahoo Finance (%s, %s)", len(df), ticker, interval)
                return df
        except Exception as exc:  # noqa: BLE001
            log.warning("Yahoo Finance fetch failed for %s: %s", ticker, exc)

    if config.TWELVEDATA_API_KEY:
        try:
            df = _fetch_twelvedata(interval)
            if not df.empty:
                log.info("Fetched %d candles from Twelve Data (%s, %s)", len(df), config.TWELVEDATA_SYMBOL, interval)
                return df
        except Exception as exc:  # noqa: BLE001
            log.warning("Twelve Data fetch failed: %s", exc)

    # Last resort: daily candles from stooq (coarser, but keeps the bot alive)
    try:
        df = _fetch_stooq()
        if not df.empty:
            log.info("Fetched %d daily candles from stooq.com (fallback)", len(df))
            return df
    except Exception as exc:  # noqa: BLE001
        log.warning("stooq.com fallback fetch failed: %s", exc)

    raise RuntimeError("Could not fetch gold price data from any source (Yahoo Finance, Twelve Data, stooq.com)")


def get_daily_candles(period: str = "1y") -> pd.DataFrame:
    return get_candles(interval="60m", period=period)
