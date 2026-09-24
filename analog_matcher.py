"""
Analog / historical pattern matching (added 24.09 by user request):
"гледаш чарта и търсиш в последните 1-2 дена подобен и гледаш какво е
станало ... и ми пращаш съобщение на колко приблизително да си сложа
такe профита и stop losa да не са някакви луди" - look at the chart,
find a similar recent setup in the last day or two, see what actually
happened after it, and use that to suggest realistic (not crazy)
take-profit/stop-loss levels.

How it works, in plain terms:
  1. Take the shape of the last ANALOG_SHAPE_CANDLES closes (normalized as
     % change from the first candle in the window, so it's independent of
     the instrument's absolute price level).
  2. Slide a window of the same length back through the previous
     ANALOG_LOOKBACK_CANDLES candles, comparing each historical window's
     shape to the current one (root-mean-square distance between the two
     normalized shapes - 0 = identical shape, bigger = less similar).
  3. Take the best (smallest-distance) match. Look at what price actually
     did in the ANALOG_FOLLOW_CANDLES candles right after that historical
     window - did it go up or down, how far did it run in the winning
     direction (the "favorable" move), and how far did it dip against that
     move first (the "adverse" move)?
  4. That favorable/adverse pair is what signals.py uses to size take-profit
     and stop-loss - real historical outcomes instead of a generic
     ATR multiple, clamped to sane bounds so a single freak historical move
     can't produce an extreme suggestion (see signals.py SignalResult).

Only a "confident" match (distance <= config.ANALOG_MAX_DISTANCE) is used
by signals.py as a 7th confluence vote or for sizing - a poor/no match is
simply ignored rather than forced into a signal.

This is descriptive pattern-matching on price history, not a guarantee -
past analogs do not predict future moves, it just gives a data-grounded
starting point instead of a made-up number.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

import config


@dataclass
class AnalogResult:
    found: bool
    direction: str = "NONE"  # "BUY", "SELL", or "NONE" - which way the analog resolved
    distance: float = float("inf")  # shape-match distance, 0 = identical, smaller = better
    confident: bool = False  # distance <= config.ANALOG_MAX_DISTANCE
    favorable_move_pct: float = 0.0  # how far price ran in the winning direction, %
    adverse_move_pct: float = 0.0  # how far price dipped against it first, %
    match_index: int = -1  # row position of the matched historical window, for logging
    candles_ago: int = -1  # how many candles back the match starts


def _normalize_shape(closes: np.ndarray):
    """Z-score the window (subtract its own mean, divide by its own std).
    Scale-invariant like a simple %-from-first-close would be, but also
    shift-invariant and far more robust: comparing to the window's OWN
    mean/spread (instead of pinning everything to one reference candle)
    means a single noisy first tick can't skew the whole shape, and -
    importantly - a nearly flat/quiet window (std ~ 0, no real "shape" to
    match on) is detected and rejected here (returns None) instead of
    silently comparing as if it were meaningful, which is what let quiet,
    low-volatility stretches produce spuriously "confident" matches with
    plain %-from-first-close normalization."""
    std = closes.std()
    if std < 1e-9:
        return None
    return (closes - closes.mean()) / std


def find_analog(df: pd.DataFrame, shape_candles=None, lookback_candles=None, follow_candles=None):
    """
    Returns an AnalogResult, or None if there isn't enough history yet to
    search (early in the bot's life, or right after a data gap).
    """
    shape_candles = shape_candles or config.ANALOG_SHAPE_CANDLES
    lookback_candles = lookback_candles or config.ANALOG_LOOKBACK_CANDLES
    follow_candles = follow_candles or config.ANALOG_FOLLOW_CANDLES

    n = len(df)
    # Need: the current shape window, plus at least one full historical
    # window+follow-through pair to compare it against.
    min_rows = shape_candles + (shape_candles + follow_candles)
    if n < min_rows:
        return None

    closes = df["Close"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)

    current_shape = _normalize_shape(closes[n - shape_candles:n])
    if current_shape is None:
        # Current price action is essentially flat - there's no real shape
        # here to look for an analog of, so don't manufacture one.
        return None

    # Valid historical window start indices: [i, i+shape_candles) is the
    # candidate shape, [i+shape_candles, i+shape_candles+follow_candles) is
    # its follow-through - both must end before "now" so we're only ever
    # comparing to the past, never overlapping the current shape.
    earliest_start = max(0, n - shape_candles - lookback_candles - follow_candles)
    latest_start = n - 2 * shape_candles - follow_candles
    if latest_start < earliest_start:
        return None

    best_distance = float("inf")
    best_i = None
    for i in range(earliest_start, latest_start + 1):
        hist_shape = _normalize_shape(closes[i:i + shape_candles])
        if hist_shape is None:
            continue  # flat/quiet historical stretch - not a usable comparison
        dist = float(np.sqrt(np.mean((current_shape - hist_shape) ** 2)))
        if dist < best_distance:
            best_distance = dist
            best_i = i

    if best_i is None:
        return None

    entry_idx = best_i + shape_candles - 1
    entry_price = closes[entry_idx]
    follow_start = best_i + shape_candles
    follow_end = follow_start + follow_candles
    follow_highs = highs[follow_start:follow_end]
    follow_lows = lows[follow_start:follow_end]
    follow_close_end = closes[follow_end - 1]

    outcome_pct = (follow_close_end - entry_price) / entry_price * 100.0 if entry_price else 0.0

    if outcome_pct > 0:
        direction = "BUY"
        favorable_price = float(follow_highs.max())
        adverse_price = float(follow_lows.min())
        favorable_move_pct = max(0.0, (favorable_price - entry_price) / entry_price * 100.0)
        adverse_move_pct = max(0.0, (entry_price - adverse_price) / entry_price * 100.0)
    elif outcome_pct < 0:
        direction = "SELL"
        favorable_price = float(follow_lows.min())
        adverse_price = float(follow_highs.max())
        favorable_move_pct = max(0.0, (entry_price - favorable_price) / entry_price * 100.0)
        adverse_move_pct = max(0.0, (adverse_price - entry_price) / entry_price * 100.0)
    else:
        direction = "NONE"
        favorable_move_pct = 0.0
        adverse_move_pct = 0.0

    confident = best_distance <= config.ANALOG_MAX_DISTANCE

    return AnalogResult(
        found=True,
        direction=direction,
        distance=best_distance,
        confident=confident,
        favorable_move_pct=favorable_move_pct,
        adverse_move_pct=adverse_move_pct,
        match_index=best_i,
        candles_ago=n - best_i,
    )
