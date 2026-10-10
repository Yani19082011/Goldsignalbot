"""
Offline sanity tests for the champion gold strategy (champion_strategy.py:
quiet_accumulation + session_momentum), using synthetic price paths (no
network needed). Run with: python3 test_champion_strategy.py

Mirrors the style of test_signals.py - these check the live-bot porting of
the logic is faithful to the validated backtest (same math, same no-
lookahead discipline), not the backtest's real-market results themselves
(those were produced separately in GoldBacktest/gold_strategy_backtest.py).

Price paths use a FIXED high/low "band" around Close (not realistic
candle shapes) so True Range - and therefore ATR - stays exactly at a
known floor (2x the band) as long as each bar's Close delta stays within
the band. That makes it possible to deliberately engineer a quiet-ATR
regime with a controlled drift/momentum on top, instead of hoping random
noise happens to produce one.
"""
import numpy as np
import pandas as pd

import champion_strategy
import config

_BAND = 2.0  # вижте бележката по-горе - фиксирана high/low "лента" около Close


def make_series(closes, start="2026-01-01", freq="h", band=_BAND):
    n = len(closes)
    idx = pd.date_range(start, periods=n, freq=freq)
    close = pd.Series(closes, index=idx, dtype=float)
    high = close + band
    low = close - band
    open_ = close.shift(1).fillna(close.iloc[0])
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close})


def _quiet_base(n_quiet: int = 130, seed: int = 0):
    # Шум много по-малък от band-а -> True Range стои на пода (2*band) за
    # цялата "тиха" база, т.е. ATR се установява точно на този под.
    rng = np.random.default_rng(seed)
    return list(2000 + np.cumsum(rng.normal(0, 0.1, n_quiet)))


def _quiet_then_grind(step_per_bar: float, n_quiet: int = 130, seed: int = 0):
    """n_quiet тихи свещи, последвани от QUIET_DRIFT_LOOKBACK (=5) свещи,
    всяка стъпваща с `step_per_bar` - в рамките на band-а, затова ATR си
    остава на пода (не се "раздува"), докато кумулативният дрифт за
    последните 5 свещи И движението за последните 3 свещи растат заедно
    (реалистично за грайндащ, но тих пазар). И двете под-стратегии трябва
    да се задействат в същата посока тук."""
    quiet = _quiet_base(n_quiet, seed)
    base = quiet[-1]
    drift_bars = [base + step_per_bar * (k + 1) for k in range(champion_strategy.QUIET_DRIFT_LOOKBACK)]
    return quiet + drift_bars


def test_quiet_market_plus_momentum_jump_agree_on_long():
    closes = _quiet_then_grind(step_per_bar=2.0)
    df = make_series(closes)
    result = champion_strategy.evaluate(df)
    print(f"[long-agree] direction={result.direction} quiet={result.quiet_vote} momentum={result.momentum_vote} "
          f"price={result.price:.2f} atr={result.atr:.3f} reasons={result.reasons}")
    assert result.quiet_vote == "LONG", "expected quiet_accumulation to vote LONG in a quiet-then-grind-up setup"
    assert result.momentum_vote == "LONG", "expected session_momentum to vote LONG after the grind-up"
    assert result.direction == "LONG"
    assert result.stop_loss < result.price < result.take_profit


def test_quiet_market_plus_momentum_jump_agree_on_short():
    closes = _quiet_then_grind(step_per_bar=-2.0)
    df = make_series(closes)
    result = champion_strategy.evaluate(df)
    print(f"[short-agree] direction={result.direction} quiet={result.quiet_vote} momentum={result.momentum_vote} "
          f"price={result.price:.2f} atr={result.atr:.3f}")
    assert result.quiet_vote == "SHORT"
    assert result.momentum_vote == "SHORT"
    assert result.direction == "SHORT"
    assert result.take_profit < result.price < result.stop_loss


def test_one_substrategy_abstaining_is_flat_not_forced():
    # Последните 2 "свободни" свещи от 5-барния прозорец се качват точно
    # толкова, че да приберат нетния 5-барен дрифт обратно в "мъртвата
    # зона" (под QUIET_DRIFT_THRESHOLD) - quiet_accumulation се въздържа
    # (None) - докато последните 3 свещи все пак падат достатъчно рязко, за
    # да задействат session_momentum (SHORT). Резултатът НЕ бива да бъде
    # форсиран към SHORT само защото едната под-стратегия има мнение.
    quiet = _quiet_base(seed=1)
    base = quiet[-1]
    rise, fall = 3.0, 2.0
    tail = [base + rise, base + rise, base + rise - fall, base + rise - 2 * fall, base + rise - 3 * fall]
    df = make_series(quiet + tail)
    result = champion_strategy.evaluate(df)
    print(f"[abstain] direction={result.direction} quiet={result.quiet_vote} momentum={result.momentum_vote}")
    assert result.quiet_vote is None, "expected quiet_accumulation to abstain (drift inside the dead zone)"
    assert result.momentum_vote == "SHORT", "expected session_momentum to still fire on the sharp last-3-bar drop"
    assert result.direction == "FLAT", "one side abstaining must not force the other side's direction"
    assert result.stop_loss is None and result.take_profit is None


def test_noisy_market_with_no_setup_is_flat():
    rng = np.random.default_rng(2)
    closes = 2000 + np.cumsum(rng.normal(0, 0.8, 150))  # нормална волатилност, без ясен тих период/скок
    df = make_series(list(closes))
    result = champion_strategy.evaluate(df)
    print(f"[noisy] direction={result.direction} quiet={result.quiet_vote} momentum={result.momentum_vote}")
    assert result.direction == "FLAT"


def test_insufficient_data_is_safe_and_honest():
    df = make_series([2000.0 + i * 0.1 for i in range(10)])
    result = champion_strategy.evaluate(df)
    print(f"[short-history] direction={result.direction} enough_data={result.enough_data}")
    assert result.direction == "FLAT"
    assert result.enough_data is False
    assert result.stop_loss is None and result.take_profit is None


def test_no_lookahead_last_row_decides_everything():
    # Две идентични истории до позиция i, различни само СЛЕД i - резултатът
    # на evaluate() (който винаги гледа последния ред) трябва да зависи
    # единствено от данните ДО и ВКЛЮЧИТЕЛНО последния ред, никога от бъдещето.
    closes = _quiet_then_grind(step_per_bar=2.0)
    df_a = make_series(closes)
    df_b = make_series(closes + [9999.0, -9999.0, 5.0])  # бъдещи редове, различни стойности
    df_b_truncated = df_b.iloc[:len(df_a)]
    result_a = champion_strategy.evaluate(df_a)
    result_b = champion_strategy.evaluate(df_b_truncated)
    assert result_a.direction == result_b.direction == "LONG"
    assert result_a.price == result_b.price
    print("[no-lookahead] truncated-vs-full history up to the same row gives the identical result - OK")


def test_stop_loss_take_profit_use_configured_atr_multiples():
    closes = _quiet_then_grind(step_per_bar=2.0)
    df = make_series(closes)
    result = champion_strategy.evaluate(df)
    expected_stop = round(result.price - config.CHAMPION_STOP_ATR_MULT * result.atr, 2)
    expected_tp = round(result.price + config.CHAMPION_TP_ATR_MULT * result.atr, 2)
    print(f"[sizing] stop={result.stop_loss} expected={expected_stop} tp={result.take_profit} expected={expected_tp}")
    assert result.stop_loss == expected_stop
    assert result.take_profit == expected_tp


if __name__ == "__main__":
    tests = [
        test_quiet_market_plus_momentum_jump_agree_on_long,
        test_quiet_market_plus_momentum_jump_agree_on_short,
        test_one_substrategy_abstaining_is_flat_not_forced,
        test_noisy_market_with_no_setup_is_flat,
        test_insufficient_data_is_safe_and_honest,
        test_no_lookahead_last_row_decides_everything,
        test_stop_loss_take_profit_use_configured_atr_multiples,
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
    print("All champion strategy tests passed.")
