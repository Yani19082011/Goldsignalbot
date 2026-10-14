"""
Offline sanity tests for the champion gold strategy (champion_strategy.py:
quiet_accumulation + ema_trend), using synthetic price paths (no network
needed). Run with: python3 test_champion_strategy.py

Mirrors the style of test_signals.py - these check the live-bot porting of
the logic is faithful to the validated backtest (same math, same no-
lookahead discipline), not the backtest's real-market results themselves
(those were produced separately in GoldBacktest/gold_hour_structure_search.py
+ gold_quiet_accumulation_deepdive.py + gold_combo_oos_check.py).

Price paths use a FIXED high/low "band" around Close (not realistic
candle shapes) so True Range - and therefore ATR - stays exactly at a
known floor (2x the band) as long as each bar's Close delta stays within
the band. That makes it possible to deliberately engineer a quiet-ATR
regime with a controlled drift on top, instead of hoping random noise
happens to produce one.

Unlike the old session_momentum (which only looked at the last 3 bars),
ema_trend needs an ESTABLISHED trend (EMA9/EMA21/EMA50 aligned) - so the
"agree" scenarios below use a long, steady drift (150 bars), not just a
short tail jump.
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


def _quiet_then_established_trend(step_per_bar: float, n_quiet: int = 130, n_trend: int = 150, seed: int = 0):
    """n_quiet тихи свещи, последвани от n_trend свещи с постоянна стъпка
    `step_per_bar` (в рамките на band-а, затова ATR си остава на пода, не
    се "раздува"). Достатъчно дълго (150 бара), за да може EMA9/EMA21/EMA50
    реално да се подредят в посоката на тренда (ema_trend не гледа само
    последните няколко бара, за разлика от старата session_momentum) - и
    докато кумулативният 5-барен дрифт в края остава извън "мъртвата зона"
    на quiet_accumulation. И двете под-стратегии трябва да се задействат в
    същата посока тук."""
    quiet = _quiet_base(n_quiet, seed)
    base = quiet[-1]
    trend = [base + step_per_bar * (k + 1) for k in range(n_trend)]
    return quiet + trend


def test_quiet_market_plus_established_trend_agree_on_long():
    closes = _quiet_then_established_trend(step_per_bar=0.5)
    df = make_series(closes)
    result = champion_strategy.evaluate(df)
    print(f"[long-agree] direction={result.direction} quiet={result.quiet_vote} trend={result.trend_vote} "
          f"price={result.price:.2f} atr={result.atr:.3f} reasons={result.reasons}")
    assert result.quiet_vote == "LONG", "expected quiet_accumulation to vote LONG in a quiet-then-grind-up setup"
    assert result.trend_vote == "LONG", "expected ema_trend to vote LONG after the established grind-up"
    assert result.direction == "LONG"
    assert result.stop_loss < result.price < result.take_profit


def test_quiet_market_plus_established_trend_agree_on_short():
    closes = _quiet_then_established_trend(step_per_bar=-0.5)
    df = make_series(closes)
    result = champion_strategy.evaluate(df)
    print(f"[short-agree] direction={result.direction} quiet={result.quiet_vote} trend={result.trend_vote} "
          f"price={result.price:.2f} atr={result.atr:.3f}")
    assert result.quiet_vote == "SHORT"
    assert result.trend_vote == "SHORT"
    assert result.direction == "SHORT"
    assert result.take_profit < result.price < result.stop_loss


def test_one_substrategy_abstaining_is_flat_not_forced():
    # Установен LONG тренд (достатъчно дълъг, за да подреди EMA9>EMA21 и
    # цена>EMA50), но последните 5 свещи са напълно плоски - нетният
    # 5-барен дрифт пада в "мъртвата зона" на quiet_accumulation (None),
    # докато ema_trend все още гледа установената по-дълга структура и
    # остава LONG. Резултатът НЕ бива да бъде форсиран към LONG само защото
    # едната под-стратегия има мнение.
    closes = _quiet_then_established_trend(step_per_bar=0.5, n_trend=145)
    flat_tail = [closes[-1]] * 5
    df = make_series(closes + flat_tail)
    result = champion_strategy.evaluate(df)
    print(f"[abstain] direction={result.direction} quiet={result.quiet_vote} trend={result.trend_vote}")
    assert result.quiet_vote is None, "expected quiet_accumulation to abstain (drift inside the dead zone)"
    assert result.trend_vote == "LONG", "expected ema_trend to still reflect the established uptrend"
    assert result.direction == "FLAT", "one side abstaining must not force the other side's direction"
    assert result.stop_loss is None and result.take_profit is None


def test_noisy_market_with_no_setup_is_flat():
    rng = np.random.default_rng(2)
    closes = 2000 + np.cumsum(rng.normal(0, 0.8, 150))  # нормална волатилност, без установен тих период/тренд
    df = make_series(list(closes))
    result = champion_strategy.evaluate(df)
    print(f"[noisy] direction={result.direction} quiet={result.quiet_vote} trend={result.trend_vote}")
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
    closes = _quiet_then_established_trend(step_per_bar=0.5)
    df_a = make_series(closes)
    df_b = make_series(closes + [9999.0, -9999.0, 5.0])  # бъдещи редове, различни стойности
    df_b_truncated = df_b.iloc[:len(df_a)]
    result_a = champion_strategy.evaluate(df_a)
    result_b = champion_strategy.evaluate(df_b_truncated)
    assert result_a.direction == result_b.direction == "LONG"
    assert result_a.price == result_b.price
    print("[no-lookahead] truncated-vs-full history up to the same row gives the identical result - OK")


def test_stop_loss_take_profit_use_configured_atr_multiples():
    closes = _quiet_then_established_trend(step_per_bar=0.5)
    df = make_series(closes)
    result = champion_strategy.evaluate(df)
    expected_stop = round(result.price - config.CHAMPION_STOP_ATR_MULT * result.atr, 2)
    expected_tp = round(result.price + config.CHAMPION_TP_ATR_MULT * result.atr, 2)
    print(f"[sizing] stop={result.stop_loss} expected={expected_stop} tp={result.take_profit} expected={expected_tp}")
    assert result.stop_loss == expected_stop
    assert result.take_profit == expected_tp


def test_tp_sl_distance_matches_price_difference():
    closes = _quiet_then_established_trend(step_per_bar=0.5)
    df = make_series(closes)
    result = champion_strategy.evaluate(df)
    assert result.tp_sl_distance == round(abs(result.take_profit - result.price), 2)
    assert result.tp_sl_distance_pct == round(result.tp_sl_distance / result.price * 100, 2)
    print(f"[tp-sl-distance] distance={result.tp_sl_distance} pct={result.tp_sl_distance_pct}%")


if __name__ == "__main__":
    tests = [
        test_quiet_market_plus_established_trend_agree_on_long,
        test_quiet_market_plus_established_trend_agree_on_short,
        test_one_substrategy_abstaining_is_flat_not_forced,
        test_noisy_market_with_no_setup_is_flat,
        test_insufficient_data_is_safe_and_honest,
        test_no_lookahead_last_row_decides_everything,
        test_stop_loss_take_profit_use_configured_atr_multiples,
        test_tp_sl_distance_matches_price_difference,
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
