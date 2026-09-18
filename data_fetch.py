"""
Fetches recent gold price candles.

Primary source: Yahoo Finance (via yfinance), ticker "XAUUSD=X" (spot gold
in USD), falling back to "GC=F" (Comex gold futures) if the first returns
nothing. Both are free and need no API key.

Secondary fallback: stooq.com's free CSV endpoint, in case Yahoo is
unreachable (rate limiting, temporary outage, etc.) - also free, no key.

Note: outbound network calls only work once this bot is actually running
somewhere with normal internet access (e.g. deployed on Render). Some
sandboxed dev environments restrict outbound web access entirely.
"""
import io
import logging

import pandas as pd
import requests
import yfinance as yf

import config

log = logging.getLogger("gold_signal_bot.data")


def _fetch_yfinance(ticker: str, period: str, interval: str) -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    # yfinance sometimes returns MultiIndex columns for a single ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


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


def get_candles(interval: str = "60m", period: str = "60d") -> pd.DataFrame:
    """
    Returns an OHLCV DataFrame indexed by timestamp, newest last.
    Tries the configured ticker, then the fallback ticker, then stooq
    (daily only). Raises RuntimeError if every source fails.
    """
    for ticker in (config.GOLD_TICKER, config.GOLD_TICKER_FALLBACK):
        try:
            df = _fetch_yfinance(ticker, period=period, interval=interval)
            if not df.empty:
                log.info("Fetched %d candles from Yahoo Finance (%s, %s)", len(df), ticker, interval)
                return df
        except Exception as exc:  # noqa: BLE001
            log.warning("Yahoo Finance fetch failed for %s: %s", ticker, exc)

    # Last resort: daily candles from stooq (coarser, but keeps the bot alive)
    try:
        df = _fetch_stooq()
        if not df.empty:
            log.info("Fetched %d daily candles from stooq.com (fallback)", len(df))
            return df
    except Exception as exc:  # noqa: BLE001
        log.warning("stooq.com fallback fetch failed: %s", exc)

    raise RuntimeError("Could not fetch gold price data from any source")


def get_daily_candles(period: str = "1y") -> pd.DataFrame:
    return get_candles(interval="1d", period=period)
