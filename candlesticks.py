"""
Candlestick pattern recognition (OHLC-only, no volume needed) - adds a 6th
confluence condition to signals.py alongside EMA/RSI/MACD/Bollinger.

Researched 18.09 against StockCharts ChartSchool's Candlestick Pattern
Dictionary (https://chartschool.stockcharts.com/table-of-contents/chart-
analysis/candlestick-charts/candlestick-pattern-dictionary) - the canonical
qualitative definitions for each pattern below come from there. That source
(like most classic candlestick literature) describes shapes in relative
terms only ("long body", "small body", "long shadow") with no numeric
ratios attached. The thresholds below are the common industry convention
used by most algorithmic implementations (TradingView's built-in scripts,
Bulkowski's Encyclopedia of Candlestick Charts):
  - "long body"   >= 60% of the candle's high-low range
  - "small body"  <= 30% of the candle's high-low range
  - "doji" body   <= 8% of the candle's high-low range
  - "long shadow" >= 2x the body length

Gap-based patterns (Piercing Line, Dark Cloud Cover, Morning/Evening Star
classically expect a gap between candle bodies) are adapted WITHOUT
requiring a literal price gap: gold and index futures/CFDs trade
near-continuously, so gaps between consecutive 15m candles essentially
never occur the way they do on a stock's daily chart between sessions.
The shape/closing-position rules are kept; only the gap requirement is
dropped, otherwise these patterns would almost never fire on this data.

Trend context matters: most of these are REVERSAL patterns, only
meaningful after a preceding move in the opposite direction (a hammer
after a rally isn't bullish, it's just noise). Approximated with a simple
lookback: was price net higher/lower LOOKBACK_TREND candles before the
pattern started than right before the pattern started.
"""
import pandas as pd

LOOKBACK_TREND = 5  # candles back to check the preceding move's direction
LONG_BODY_RATIO = 0.6
SMALL_BODY_RATIO = 0.3
DOJI_BODY_RATIO = 0.08
LONG_SHADOW_MULT = 2.0

MIN_ROWS = LOOKBACK_TREND + 8  # comfortable buffer for 3-candle patterns


def _metrics(row) -> dict:
    o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    return {
        "open": o, "high": h, "low": l, "close": c,
        "range": rng, "body": body, "body_ratio": body / rng,
        "upper_shadow": h - max(o, c), "lower_shadow": min(o, c) - l,
        "bullish": c > o, "bearish": c < o,
    }


def detect_patterns(df: pd.DataFrame):
    """Returns (bullish_patterns, bearish_patterns) - lists of Bulgarian
    pattern names detected using the most recent 1-3 candles. Returns
    ([], []) if there isn't enough data yet."""
    if len(df) < MIN_ROWS:
        return [], []

    c0 = _metrics(df.iloc[-1])  # current candle
    c1 = _metrics(df.iloc[-2])  # one before
    c2 = _metrics(df.iloc[-3])  # two before
    closes = df["Close"]

    def _trend_before(n_candles_ago: int, direction: str) -> bool:
        end_idx = -1 - n_candles_ago
        start_idx = end_idx - LOOKBACK_TREND
        if abs(start_idx) > len(closes):
            return False
        start, end = closes.iloc[start_idx], closes.iloc[end_idx]
        return start > end if direction == "down" else start < end

    bullish, bearish = [], []

    # --- Single-candle patterns (on c0) ---
    hammer_shape = (
        c0["lower_shadow"] >= LONG_SHADOW_MULT * max(c0["body"], 1e-9)
        and c0["upper_shadow"] <= c0["body"] + 1e-9
        and c0["body_ratio"] <= SMALL_BODY_RATIO + 0.1
    )
    inverted_shape = (
        c0["upper_shadow"] >= LONG_SHADOW_MULT * max(c0["body"], 1e-9)
        and c0["lower_shadow"] <= c0["body"] + 1e-9
        and c0["body_ratio"] <= SMALL_BODY_RATIO + 0.1
    )
    is_doji = c0["body_ratio"] <= DOJI_BODY_RATIO

    if hammer_shape and _trend_before(1, "down"):
        bullish.append("Hammer (чук)")
    if inverted_shape and _trend_before(1, "down"):
        bullish.append("Inverted Hammer")
    if hammer_shape and _trend_before(1, "up"):
        bearish.append("Hanging Man")
    if inverted_shape and _trend_before(1, "up"):
        bearish.append("Shooting Star")
    if is_doji and c0["lower_shadow"] > 2 * c0["upper_shadow"] and _trend_before(1, "down"):
        bullish.append("Dragonfly Doji")
    if is_doji and c0["upper_shadow"] > 2 * c0["lower_shadow"] and _trend_before(1, "up"):
        bearish.append("Gravestone Doji")

    # --- Two-candle patterns (c1 then c0) ---
    if (c1["bearish"] and c0["bullish"]
            and c0["open"] <= c1["close"] and c0["close"] >= c1["open"]
            and _trend_before(1, "down")):
        bullish.append("Bullish Engulfing")
    if (c1["bullish"] and c0["bearish"]
            and c0["open"] >= c1["close"] and c0["close"] <= c1["open"]
            and _trend_before(1, "up")):
        bearish.append("Bearish Engulfing")

    prev_mid = (c1["open"] + c1["close"]) / 2
    if (c1["bearish"] and c1["body_ratio"] >= LONG_BODY_RATIO
            and c0["bullish"] and prev_mid < c0["close"] < c1["open"]
            and _trend_before(1, "down")):
        bullish.append("Piercing Line")
    if (c1["bullish"] and c1["body_ratio"] >= LONG_BODY_RATIO
            and c0["bearish"] and c1["open"] < c0["close"] < prev_mid
            and _trend_before(1, "up")):
        bearish.append("Dark Cloud Cover")

    # --- Three-candle patterns (c2, c1, c0) ---
    c2_mid = (c2["open"] + c2["close"]) / 2
    if (c2["bearish"] and c2["body_ratio"] >= LONG_BODY_RATIO
            and c1["body_ratio"] <= SMALL_BODY_RATIO
            and c0["bullish"] and c0["close"] > c2_mid
            and _trend_before(2, "down")):
        bullish.append("Morning Star")
    if (c2["bullish"] and c2["body_ratio"] >= LONG_BODY_RATIO
            and c1["body_ratio"] <= SMALL_BODY_RATIO
            and c0["bearish"] and c0["close"] < c2_mid
            and _trend_before(2, "up")):
        bearish.append("Evening Star")

    if (c2["bullish"] and c1["bullish"] and c0["bullish"]
            and min(c2["body_ratio"], c1["body_ratio"], c0["body_ratio"]) >= LONG_BODY_RATIO
            and c2["open"] <= c1["open"] <= c2["close"]
            and c1["open"] <= c0["open"] <= c1["close"]
            and c1["close"] > c2["close"] and c0["close"] > c1["close"]):
        bullish.append("Three White Soldiers")
    if (c2["bearish"] and c1["bearish"] and c0["bearish"]
            and min(c2["body_ratio"], c1["body_ratio"], c0["body_ratio"]) >= LONG_BODY_RATIO
            and c2["close"] <= c1["open"] <= c2["open"]
            and c1["close"] <= c0["open"] <= c1["open"]
            and c1["close"] < c2["close"] and c0["close"] < c1["close"]):
        bearish.append("Three Black Crows")

    return bullish, bearish
