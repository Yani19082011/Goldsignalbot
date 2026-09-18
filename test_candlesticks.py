"""
Offline tests for candlesticks.py - hand-built OHLC candles matching each
pattern's textbook shape (see the module docstring for the source
definitions), no network needed. Run with: python3 test_candlesticks.py
"""
import pandas as pd

import candlesticks


def _df(rows, freq="15min"):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq=freq)
    df = pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"])
    return df


def _declining_run(n, start, end):
    """n candles net declining from start to end - small-bodied, used as
    the "prior downtrend" lead-in before a reversal pattern."""
    step = (end - start) / n
    rows = []
    price = start
    for _ in range(n):
        o = price
        c = price + step
        h = max(o, c) + abs(step) * 0.2
        l = min(o, c) - abs(step) * 0.2
        rows.append([o, h, l, c])
        price = c
    return rows


def _rising_run(n, start, end):
    return _declining_run(n, start, end)  # same helper, direction via start/end


def test_hammer_after_downtrend_is_bullish():
    lead_in = _declining_run(15, 2050, 2000)
    # Hammer: small body near the top, long lower shadow, tiny/no upper shadow
    hammer = [1999, 2001.5, 1985, 2001]  # body=2, lower_shadow=14 (7x body), upper_shadow=0.5
    df = _df(lead_in + [hammer])
    bullish, bearish = candlesticks.detect_patterns(df)
    print(f"[hammer] bullish={bullish} bearish={bearish}")
    assert any("Hammer" in p for p in bullish), f"expected Hammer in {bullish}"


def test_shooting_star_after_uptrend_is_bearish():
    lead_in = _rising_run(15, 2000, 2050)
    # Inverted-hammer shape after an uptrend = Shooting Star
    star = [2050, 2065, 2049.7, 2050.5]  # body=0.5, upper_shadow=14.5, lower_shadow=0.3 (<= body)
    df = _df(lead_in + [star])
    bullish, bearish = candlesticks.detect_patterns(df)
    print(f"[shooting-star] bullish={bullish} bearish={bearish}")
    assert any("Shooting Star" in p for p in bearish), f"expected Shooting Star in {bearish}"


def test_bullish_engulfing_after_downtrend():
    lead_in = _declining_run(15, 2050, 2000)
    prev = [2000, 2001, 1994, 1995]  # small-ish bearish candle
    cur = [1994, 2006, 1993, 2005]   # bullish candle, body fully engulfs prev's open/close
    df = _df(lead_in + [prev, cur])
    bullish, bearish = candlesticks.detect_patterns(df)
    print(f"[bullish-engulfing] bullish={bullish}")
    assert any("Bullish Engulfing" in p for p in bullish), f"expected Bullish Engulfing in {bullish}"


def test_morning_star_after_downtrend():
    lead_in = _declining_run(15, 2100, 2000)
    c2 = [2000, 2002, 1965, 1970]   # long bearish candle
    c1 = [1968, 1972, 1963, 1967]   # small-bodied middle candle
    c0 = [1969, 2005, 1968, 2000]   # long bullish candle closing above c2's midpoint (1985)
    df = _df(lead_in + [c2, c1, c0])
    bullish, bearish = candlesticks.detect_patterns(df)
    print(f"[morning-star] bullish={bullish}")
    assert any("Morning Star" in p for p in bullish), f"expected Morning Star in {bullish}"


def test_three_white_soldiers_after_downtrend():
    lead_in = _declining_run(15, 2080, 2000)
    c2 = [2000, 2022, 1998, 2020]
    c1 = [2010, 2042, 2008, 2040]
    c0 = [2030, 2062, 2028, 2060]
    df = _df(lead_in + [c2, c1, c0])
    bullish, bearish = candlesticks.detect_patterns(df)
    print(f"[three-white-soldiers] bullish={bullish}")
    assert any("Three White Soldiers" in p for p in bullish), f"expected Three White Soldiers in {bullish}"


def test_no_pattern_on_flat_indecisive_candles():
    idx = pd.date_range("2026-01-01", periods=20, freq="15min")
    rows = [[2000, 2000.5, 1999.5, 2000.2] for _ in range(20)]
    df = _df(rows)
    bullish, bearish = candlesticks.detect_patterns(df)
    print(f"[flat] bullish={bullish} bearish={bearish}")
    assert bullish == [] and bearish == [], "flat/indecisive candles should not match any pattern"


def test_insufficient_data_returns_empty():
    df = _df([[2000, 2001, 1999, 2000.5]] * 5)
    bullish, bearish = candlesticks.detect_patterns(df)
    assert bullish == [] and bearish == []
    print("[insufficient-data] correctly returned no patterns")


if __name__ == "__main__":
    tests = [
        test_hammer_after_downtrend_is_bullish,
        test_shooting_star_after_uptrend_is_bearish,
        test_bullish_engulfing_after_downtrend,
        test_morning_star_after_downtrend,
        test_three_white_soldiers_after_downtrend,
        test_no_pattern_on_flat_indecisive_candles,
        test_insufficient_data_returns_empty,
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
    print("All candlestick pattern tests passed.")
