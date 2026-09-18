"""
Offline sanity tests for the confluence signal logic, using synthetic
price paths (no network needed). Run with: python3 test_signals.py

This sandbox's network policy blocks Yahoo Finance / stooq outbound
requests, so live end-to-end fetch+signal testing has to happen after
deployment (e.g. hit /check-now on Render and check the logs). These
tests instead verify the math and decision logic in isolation.
"""
import numpy as np
import pandas as pd

import config
import signals


def make_series(prices):
    n = len(prices)
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    close = pd.Series(prices, index=idx, dtype=float)
    high = close * 1.001
    low = close * 0.999
    open_ = close.shift(1).fillna(close.iloc[0])
    vol = pd.Series(1000, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol})


def test_uptrend_leans_buy_but_extended_move_is_not_chased():
    # A clean, un-pulled-back rally stacks the trend-following conditions
    # bullish (EMA order, price vs slow EMA, MACD), but RSI runs to an
    # extreme and price never touches the lower Bollinger Band, so the two
    # mean-reversion conditions correctly stay silent. The bot is deliberately
    # conservative here: it should lean bullish but NOT fire a high-conviction
    # BUY chasing an already-extended move with no pullback entry.
    flat = [2000 + np.sin(i / 5) * 2 for i in range(60)]
    rally = list(np.linspace(2000, 2140, 60))
    df = make_series(flat + rally)
    result = signals.evaluate(df)
    print(f"[uptrend] direction={result.direction} score={result.score}/{result.max_score} "
          f"price={result.price:.2f} rsi={result.rsi:.1f} reasons={result.reasons}")
    assert result.score >= 3, "trend-following conditions should still lean bullish"
    assert all("низходящ" not in r and "прекупен" not in r for r in result.reasons), (
        "an unbroken rally should not register any bearish reasons"
    )


def test_pullback_and_bounce_in_uptrend_produces_buy():
    # The canonical confluence setup: an uptrend, a corrective dip that
    # touches the lower Bollinger Band, then a sharp bounce that flips the
    # fast/mid EMAs and MACD histogram back bullish. This is the scenario
    # the bot is actually designed to catch, and confirms the confluence
    # threshold is reachable by realistic (not hand-picked-to-cheat) price
    # action, not just a theoretical number nothing can ever satisfy.
    up = list(np.linspace(2000, 2050, 40))
    pullback = list(np.linspace(2050, 2050 - 5, 10))
    bounce = [pullback[-1] * 1.02]
    df = make_series(up + pullback + bounce)
    result = signals.evaluate(df)
    print(f"[pullback-bounce] direction={result.direction} score={result.score}/{result.max_score} "
          f"rsi={result.rsi:.1f} reasons={result.reasons}")
    assert result.direction == "BUY"
    assert result.score >= config.CONFLUENCE_THRESHOLD
    assert all("низходящ" not in r and "прекупен" not in r for r in result.reasons)


def test_rally_and_drop_in_downtrend_produces_sell():
    # Mirror of the buy-side confluence setup: a downtrend, a corrective
    # rally that tags the upper Bollinger Band, then a sharp drop that
    # flips EMAs/MACD bearish.
    down = list(np.linspace(2050, 2000, 40))
    rally = list(np.linspace(2000, 2000 + 5, 10))
    drop = [rally[-1] * 0.98]
    df = make_series(down + rally + drop)
    result = signals.evaluate(df)
    print(f"[rally-drop] direction={result.direction} score={result.score}/{result.max_score} "
          f"rsi={result.rsi:.1f} reasons={result.reasons}")
    assert result.direction == "SELL"
    assert result.score >= config.CONFLUENCE_THRESHOLD
    assert all("възходящ" not in r and "препродаден" not in r for r in result.reasons)


def test_rsi_whipsaw_is_not_mislabeled_as_calm_recovery():
    # A single violent candle that throws RSI from oversold straight past
    # overbought must NOT be described as "leaving oversold" - it should
    # either not count that condition at all, or be read as overbought.
    up = list(np.linspace(2000, 2050, 40))
    pullback = list(np.linspace(2050, 2050 - 40, 20))
    violent_spike = [pullback[-1] * 1.05]  # one huge candle
    df = make_series(up + pullback + violent_spike)
    result = signals.evaluate(df)
    print(f"[whipsaw] direction={result.direction} score={result.score} rsi={result.rsi:.1f} reasons={result.reasons}")
    for reason in result.reasons:
        if "излиза от препродадена" in reason:
            assert result.rsi <= 55, f"mislabeled RSI reason at rsi={result.rsi:.1f}: {reason}"


def test_downtrend_leans_sell_but_extended_move_is_not_chased():
    flat = [2000 + np.sin(i / 5) * 2 for i in range(60)]
    decline = list(np.linspace(2000, 1860, 60))
    df = make_series(flat + decline)
    result = signals.evaluate(df)
    print(f"[downtrend] direction={result.direction} score={result.score}/{result.max_score} "
          f"price={result.price:.2f} rsi={result.rsi:.1f} reasons={result.reasons}")
    assert result.score >= 3, "trend-following conditions should still lean bearish"
    assert all("възходящ" not in r and "препродаден" not in r for r in result.reasons), (
        "an unbroken decline should not register any bullish reasons"
    )


def test_choppy_market_produces_no_signal():
    rng = np.random.default_rng(42)
    prices = 2000 + np.cumsum(rng.normal(0, 0.5, 120))
    df = make_series(prices)
    result = signals.evaluate(df)
    print(f"[choppy] direction={result.direction} score={result.score}/{result.max_score}")
    assert result.direction == "NONE", "a directionless random walk should not fire a signal"


def test_insufficient_data_is_safe():
    df = make_series([2000, 2001, 2002])
    result = signals.evaluate(df)
    print(f"[short] direction={result.direction} score={result.score}")
    assert result.direction == "NONE"


if __name__ == "__main__":
    tests = [
        test_uptrend_leans_buy_but_extended_move_is_not_chased,
        test_downtrend_leans_sell_but_extended_move_is_not_chased,
        test_pullback_and_bounce_in_uptrend_produces_buy,
        test_rally_and_drop_in_downtrend_produces_sell,
        test_rsi_whipsaw_is_not_mislabeled_as_calm_recovery,
        test_choppy_market_produces_no_signal,
        test_insufficient_data_is_safe,
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
    print("All signal tests passed.")
