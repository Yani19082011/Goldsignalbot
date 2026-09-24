"""
Offline tests for analog_matcher.py - hand-built OHLC candles with a known
repeated shape and a known follow-through outcome, no network needed.
Run with: python3 test_analog_matcher.py
"""
import pandas as pd

import analog_matcher


def _df(closes):
    """Simple OHLC frame with no intrabar spread (High=Low=Close) - fine
    here since these tests only care about the Close-based shape match and
    hand-picked High/Low extremes are set explicitly where needed via the
    Close values themselves."""
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="15min")
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close})


def test_scale_invariant_exact_match_sizes_tp_sl_from_real_outcome():
    # A historical occurrence of a shape (base=100), what happened right
    # after it (a known dip then a known rally), padding, then the exact
    # same shape recurring right now but at a totally different price level
    # (base=500) - the match should still be found (normalization makes it
    # scale-invariant) and the sizing should reflect the real historical
    # dip (adverse) and rally (favorable), not the padding.
    filler_before = [99, 99.5, 100]
    pattern = [100, 101, 100.5, 102, 101.5, 103]  # the shape being repeated
    follow = [102, 101, 103, 105, 107, 106, 109, 108]  # low=101, high=109, ends up (BUY)
    filler_mid = [200] * 10
    current_shape = [500, 505, 502.5, 510, 507.5, 515]  # same % shape as `pattern`, 5x the price

    closes = filler_before + pattern + follow + filler_mid + current_shape
    df = _df(closes)

    result = analog_matcher.find_analog(df, shape_candles=6, lookback_candles=30, follow_candles=8)
    print(f"[exact-match] found={result.found} direction={result.direction} distance={result.distance:.4f} "
          f"confident={result.confident} favorable={result.favorable_move_pct:.3f}% adverse={result.adverse_move_pct:.3f}% "
          f"candles_ago={result.candles_ago}")

    assert result.found
    assert result.distance < 1e-6, f"identical normalized shapes should have ~0 distance, got {result.distance}"
    assert result.confident
    assert result.direction == "BUY"
    # entry = 103 (last close of the matched pattern), high=109, low=101
    assert abs(result.favorable_move_pct - (109 - 103) / 103 * 100) < 1e-6
    assert abs(result.adverse_move_pct - (103 - 101) / 103 * 100) < 1e-6
    assert result.candles_ago == len(closes) - len(filler_before)


def test_completely_flat_history_yields_no_match_at_all():
    # A flat/quiet history has no real "shape" in any window to compare
    # against - rather than pretend a match was found (which is how plain
    # %-from-first-close normalization could spuriously call a flat, barely
    # moving stretch a "confident" match for anything), this should come
    # back empty-handed.
    history = [100.0] * 40
    sharp_move = [100, 90, 80, 90, 100, 110]
    df = _df(history + sharp_move)

    result = analog_matcher.find_analog(df, shape_candles=6, lookback_candles=40, follow_candles=8)
    print(f"[flat-history] result={result}")

    assert result is None, "a perfectly flat history has nothing usable to match against"


def test_dissimilar_but_variable_history_gives_no_confident_match():
    # History has real movement (a gentle, steady climb), but nothing in it
    # resembles the current sharp zigzag - the best match found should be
    # far enough away (low shape-correlation) to NOT be trusted.
    history = list(range(100, 140))  # smooth, steady 1-per-candle climb
    zigzag = [120, 110, 130, 105, 135, 100]  # current shape: sharp, choppy
    df = _df(history + zigzag)

    result = analog_matcher.find_analog(df, shape_candles=6, lookback_candles=40, follow_candles=8)
    print(f"[dissimilar] found={result.found} distance={result.distance:.2f} confident={result.confident}")

    assert result.found
    assert not result.confident, "a smooth climb should not confidently match a sharp zigzag"


def test_not_enough_data_returns_none():
    df = _df([100.0, 101.0, 102.0, 101.5, 100.5])
    result = analog_matcher.find_analog(df, shape_candles=6, lookback_candles=30, follow_candles=8)
    print(f"[insufficient-data] result={result}")
    assert result is None


def test_downtrend_analog_resolves_sell():
    filler_before = [200, 200.5, 200]
    pattern = [200, 198, 199, 196, 197, 194]  # jagged decline shape
    follow = [193, 195, 191, 190, 188, 189, 185, 186]  # high=195, low=185, ends down (SELL)
    filler_mid = [300] * 10
    current_shape = [50, 49.5, 49.75, 49, 49.25, 48.5]  # same % shape as `pattern`, different scale

    closes = filler_before + pattern + follow + filler_mid + current_shape
    df = _df(closes)

    result = analog_matcher.find_analog(df, shape_candles=6, lookback_candles=30, follow_candles=8)
    print(f"[downtrend-analog] direction={result.direction} distance={result.distance:.4f} "
          f"favorable={result.favorable_move_pct:.3f}% adverse={result.adverse_move_pct:.3f}%")

    assert result.found
    assert result.confident
    assert result.direction == "SELL"
    # entry = 194 (last close of the matched pattern), low=185 (favorable for a SELL), high=195 (adverse)
    assert abs(result.favorable_move_pct - (194 - 185) / 194 * 100) < 1e-6
    assert abs(result.adverse_move_pct - (195 - 194) / 194 * 100) < 1e-6


if __name__ == "__main__":
    tests = [
        test_scale_invariant_exact_match_sizes_tp_sl_from_real_outcome,
        test_completely_flat_history_yields_no_match_at_all,
        test_dissimilar_but_variable_history_gives_no_confident_match,
        test_not_enough_data_returns_none,
        test_downtrend_analog_resolves_sell,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}\n")
        except AssertionError as e:
            failures += 1
            print(f"FAIL: {t.__name__}: {e}\n")
    if failures:
        raise SystemExit(f"{failures} test(s) failed")
    print("All analog matcher tests passed.")
