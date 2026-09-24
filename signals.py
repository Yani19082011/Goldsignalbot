"""
Confluence-based buy/sell signal logic.

Seven independent conditions are checked per instrument's candles. Each one
votes BUY, SELL, or neutral. When enough of them agree (CONFLUENCE_THRESHOLD
out of 7), that's a signal:
  1) EMA9 vs EMA21 trend/crossover
  2) Price vs EMA50 (broader trend filter)
  3) RSI(14) leaving oversold/overbought
  4) MACD histogram momentum/crossover
  5) Bollinger Band support/resistance test
  6) Candlestick pattern (hammer, engulfing, morning/evening star, ... -
     see candlesticks.py; added 18.09 by user request)
  7) Analog match: is the current price shape similar to a setup from the
     last day or two, and which way did that historical analog resolve? -
     see analog_matcher.py; added 24.09 by user request. This condition
     also drives take_profit/stop_loss sizing below (real historical
     outcome instead of a generic ATR multiple) when it's a confident
     match in the same direction as the overall signal.

This is a rule-based technical indicator tool, not financial advice.
Markets move on macro/news events (Fed decisions, geopolitics, USD
strength) that no technical indicator sees coming - use this as one
input among several, not a substitute for your own judgement.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import analog_matcher
import candlesticks
import config

MAX_SCORE = 7


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

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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
    # Analog-match context (see analog_matcher.py) - used below to size
    # stop_loss/take_profit off a real historical outcome instead of a
    # generic ATR multiple, when it's a confident match agreeing with
    # `direction`. Distance/candles_ago are kept for logging/debugging.
    analog_direction: str = "NONE"
    analog_confident: bool = False
    analog_favorable_pct: float = 0.0
    analog_adverse_pct: float = 0.0
    analog_distance: float = float("inf")
    analog_candles_ago: int = -1

    @property
    def _uses_analog_sizing(self) -> bool:
        return (
            self.direction in ("BUY", "SELL")
            and self.analog_confident
            and self.analog_direction == self.direction
            and self.analog_adverse_pct > 0
            and self.analog_favorable_pct > 0
        )

    @property
    def sizing_method(self) -> str:
        """For transparency in the email - which method produced the
        stop_loss/take_profit below."""
        return "analog" if self._uses_analog_sizing else "atr"

    @property
    def stop_loss(self):
        if self.direction not in ("BUY", "SELL"):
            return None
        if self._uses_analog_sizing:
            # How far the historical analog dipped against the move before
            # it played out - clamped to a sane ATR range so one freak past
            # move can't suggest an extreme stop.
            raw_dist = self.price * (self.analog_adverse_pct / 100.0)
            dist = _clamp(raw_dist, 0.5 * self.atr, 4 * self.atr)
        else:
            dist = 2 * self.atr
        return round(self.price - dist, 2) if self.direction == "BUY" else round(self.price + dist, 2)

    @property
    def take_profit(self):
        if self.direction not in ("BUY", "SELL"):
            return None
        if self._uses_analog_sizing:
            # How far the historical analog ran in the winning direction -
            # same sane clamp applied.
            raw_dist = self.price * (self.analog_favorable_pct / 100.0)
            dist = _clamp(raw_dist, 1.5 * self.atr, 8 * self.atr)
        else:
            dist = 4 * self.atr  # ~1:2 risk:reward vs. the ATR-based stop
        return round(self.price + dist, 2) if self.direction == "BUY" else round(self.price - dist, 2)


def evaluate(df: pd.DataFrame) -> SignalResult:
    """
    Looks at the most recent candles and scores 6 confluence conditions
    for BUY and for SELL. Returns whichever direction (if any) crosses the
    configured threshold. Ties or a below-threshold score return direction
    "NONE".
    """
    d = add_indicators(df).dropna(
        subset=["ema_fast", "ema_mid", "ema_slow", "rsi", "macd_hist", "bb_lower", "atr"]
    )
    if len(d) < 3:
        return SignalResult(direction="NONE", score=0, max_score=MAX_SCORE, reasons=["not enough data yet"])

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

    # 6) Candlestick pattern (hammer, engulfing, morning/evening star, ...) -
    # see candlesticks.py. Multiple patterns matching at once still count as
    # ONE condition here (same weight as the other 5), not one point per
    # pattern, so a busy candle doesn't dominate the score.
    bullish_patterns, bearish_patterns = candlesticks.detect_patterns(d)
    if bullish_patterns:
        buy_reasons.append("Свещна фигура: " + ", ".join(bullish_patterns))
    if bearish_patterns:
        sell_reasons.append("Свещна фигура: " + ", ".join(bearish_patterns))

    # 7) Analog match: does the current price shape resemble a setup from
    # the last day or two, and how did that one resolve? Only a confident
    # (close-enough) match votes - a weak/no match simply doesn't count
    # either way, same as any other inconclusive condition above.
    analog = analog_matcher.find_analog(d)
    if analog and analog.confident:
        if analog.direction == "BUY":
            buy_reasons.append(
                "Аналогична ситуация преди ~%d свещи: тогава е последвало покачване (макс. ~%.2f%%)"
                % (analog.candles_ago, analog.favorable_move_pct)
            )
        elif analog.direction == "SELL":
            sell_reasons.append(
                "Аналогична ситуация преди ~%d свещи: тогава е последвал спад (макс. ~%.2f%%)"
                % (analog.candles_ago, analog.favorable_move_pct)
            )

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

    if analog and analog.found:
        analog_kwargs = dict(
            analog_direction=analog.direction,
            analog_confident=analog.confident,
            analog_favorable_pct=analog.favorable_move_pct,
            analog_adverse_pct=analog.adverse_move_pct,
            analog_distance=analog.distance,
            analog_candles_ago=analog.candles_ago,
        )
    else:
        analog_kwargs = {}

    return SignalResult(
        direction=direction,
        score=score,
        max_score=MAX_SCORE,
        reasons=reasons,
        price=float(cur["Close"]),
        atr=float(cur["atr"]),
        rsi=float(cur["rsi"]),
        timestamp=d.index[-1],
        **analog_kwargs,
    )
