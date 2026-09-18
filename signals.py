"""
Confluence-based buy/sell signal logic for gold.

Five independent technical conditions are checked on hourly candles.
Each one votes BUY, SELL, or neutral. When enough of them agree
(CONFLUENCE_THRESHOLD out of 5), that's a signal.

This is a rule-based technical indicator tool, not financial advice.
Gold can move on macro/news events (Fed decisions, geopolitics, USD
strength) that no technical indicator sees coming - use this as one
input among several, not a substitute for your own judgement.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config


# ---------------------------------------------------------------------------
# Indicator math (no external TA library needed - keeps deployment light)
# ---------------------------------------------------------------------------

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def macd(series: pd.Series, fast: int, slow: int, signal: int):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger_bands(series: pd.Series, period: int, num_std: float):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Adds every indicator column the confluence logic needs, in place-safe copy."""
    out = df.copy()
    close = out["Close"]

    out["ema_fast"] = ema(close, config.EMA_FAST)
    out["ema_mid"] = ema(close, config.EMA_MID)
    out["ema_slow"] = ema(close, config.EMA_SLOW)

    out["rsi"] = rsi(close, config.RSI_PERIOD)

    macd_line, signal_line, hist = macd(close, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist

    upper, mid, lower = bollinger_bands(close, config.BB_PERIOD, config.BB_STD)
    out["bb_upper"] = upper
    out["bb_mid"] = mid
    out["bb_lower"] = lower

    out["atr"] = atr(out, config.ATR_PERIOD)

    return out


# ---------------------------------------------------------------------------
# Confluence scoring
# ---------------------------------------------------------------------------

@dataclass
class SignalResult:
    direction: str  # "BUY", "SELL", or "NONE"
    score: int
    max_score: int
    reasons: list = field(default_factory=list)
    price: float = 0.0
    atr: float = 0.0
    rsi: float = 0.0
    timestamp: object = None

    @property
    def stop_loss(self):
        if self.direction == "BUY":
            return round(self.price - 2 * self.atr, 2)
        if self.direction == "SELL":
            return round(self.price + 2 * self.atr, 2)
        return None

    @property
    def take_profit(self):
        if self.direction == "BUY":
            return round(self.price + 4 * self.atr, 2)  # ~1:2 risk:reward
        if self.direction == "SELL":
            return round(self.price - 4 * self.atr, 2)
        return None


def evaluate(df: pd.DataFrame) -> SignalResult:
    """
    Looks at the most recent two fully-formed candles and scores 5
    confluence conditions for BUY and for SELL. Returns whichever
    direction (if any) crosses the configured threshold. Ties or a
    below-threshold score return direction "NONE".
    """
    d = add_indicators(df).dropna(
        subset=["ema_fast", "ema_mid", "ema_slow", "rsi", "macd_hist", "bb_lower", "atr"]
    )
    if len(d) < 3:
        return SignalResult(direction="NONE", score=0, max_score=5, reasons=["not enough data yet"])

    cur = d.iloc[-1]
    prev = d.iloc[-2]

    buy_reasons, sell_reasons = [], []

    # 1) EMA trend / crossover
    bull_cross = prev["ema_fast"] <= prev["ema_mid"] and cur["ema_fast"] > cur["ema_mid"]
    bear_cross = prev["ema_fast"] >= prev["ema_mid"] and cur["ema_fast"] < cur["ema_mid"]
    if bull_cross or cur["ema_fast"] > cur["ema_mid"]:
        buy_reasons.append("EMA%d над EMA%d (възходящ тренд)" % (config.EMA_FAST, config.EMA_MID))
    if bear_cross or cur["ema_fast"] < cur["ema_mid"]:
        sell_reasons.append("EMA%d под EMA%d (низходящ тренд)" % (config.EMA_FAST, config.EMA_MID))

    # 2) Price vs slow EMA (bigger-picture trend filter)
    if cur["Close"] > cur["ema_slow"]:
        buy_reasons.append("Цената е над EMA%d (общ тренд нагоре)" % config.EMA_SLOW)
    if cur["Close"] < cur["ema_slow"]:
        sell_reasons.append("Цената е под EMA%d (общ тренд надолу)" % config.EMA_SLOW)

    # 3) RSI recovering from oversold / falling from overbought.
    # Looks at whether RSI dipped into oversold within the last few candles
    # and has since stabilized back toward neutral - not just a strict
    # single-candle crossing. The neutral-zone cap matters: without it, a
    # violent candle that whipsaws RSI from <35 straight past 65 would get
    # mislabeled as a calm "leaving oversold" buy signal, when it's actually
    # now overbought and arguably bearish.
    neutral_mid = (config.RSI_OVERSOLD + config.RSI_OVERBOUGHT) / 2
    recent_rsi = d["rsi"].iloc[-3:] if len(d) >= 3 else d["rsi"].iloc[-1:]
    if cur["rsi"] < config.RSI_OVERSOLD:
        buy_reasons.append("RSI препродаден (%.1f) - възможен отскок" % cur["rsi"])
    elif recent_rsi.min() < config.RSI_OVERSOLD <= cur["rsi"] <= neutral_mid:
        buy_reasons.append("RSI излиза от препродадена зона (%.1f)" % cur["rsi"])
    if cur["rsi"] > config.RSI_OVERBOUGHT:
        sell_reasons.append("RSI прекупен (%.1f) - възможен спад" % cur["rsi"])
    elif recent_rsi.max() > config.RSI_OVERBOUGHT >= cur["rsi"] >= neutral_mid:
        sell_reasons.append("RSI излиза от прекупена зона (%.1f)" % cur["rsi"])

    # 4) MACD histogram momentum / crossover
    macd_bull_cross = prev["macd_hist"] <= 0 < cur["macd_hist"]
    macd_bear_cross = prev["macd_hist"] >= 0 > cur["macd_hist"]
    if macd_bull_cross or cur["macd_hist"] > 0:
        buy_reasons.append("MACD хистограма положителна (бичи моментум)")
    if macd_bear_cross or cur["macd_hist"] < 0:
        sell_reasons.append("MACD хистограма отрицателна (мечи моментум)")

    # 5) Bollinger Band support/resistance test. Looks at the last 3 candles,
    # not just the current one: a real support/resistance "test" is the dip
    # or spike that touches the band, and the confirming bounce candle -
    # the one that actually flips the EMA/MACD conditions - typically lands
    # 1-2 candles after that touch, not on it.
    recent = d.iloc[-3:] if len(d) >= 3 else d.iloc[-1:]
    if (recent["Low"] <= recent["bb_lower"] * 1.002).any():
        buy_reasons.append("Цената тества долната Bollinger лента (подкрепа)")
    if (recent["High"] >= recent["bb_upper"] * 0.998).any():
        sell_reasons.append("Цената тества горната Bollinger лента (съпротива)")

    buy_score = len(buy_reasons)
    sell_score = len(sell_reasons)

    if buy_score >= config.CONFLUENCE_THRESHOLD and buy_score > sell_score:
        direction, score, reasons = "BUY", buy_score, buy_reasons
    elif sell_score >= config.CONFLUENCE_THRESHOLD and sell_score > buy_score:
        direction, score, reasons = "SELL", sell_score, sell_reasons
    else:
        direction, score, reasons = "NONE", max(buy_score, sell_score), (
            buy_reasons if buy_score >= sell_score else sell_reasons
        )

    return SignalResult(
        direction=direction,
        score=score,
        max_score=5,
        reasons=reasons,
        price=float(cur["Close"]),
        atr=float(cur["atr"]),
        rsi=float(cur["rsi"]),
        timestamp=d.index[-1],
    )
