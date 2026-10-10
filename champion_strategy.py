"""
Шампионската стратегия за злато - quiet_accumulation + session_momentum.

Намерена чрез 9 рунда ръчен strategy-search backtest (отделен проект,
GoldBacktest/gold_strategy_backtest.py), в който бяха честно тествани 32
различни стратегии (+ всички двойни комбинации) на GC=F, 1-часови свещи,
проверявани в 09:00/16:30/23:00 Europe/Sofia, с 1:1 ATR risk:reward.
Тази комбинация беше единствената, която:
  - би random_baseline контролата с ясен марж (72.4% срещу ~48-51%)
  - НЕ деградира при out-of-sample проверка (разделяне на по-стара/по-нова
    половина от историята) - 68.4% -> 80.0%, т.е. се подобрява, не пада

ЧЕСТНО, преди да се довериш сляпо:
  - 29 сигнала за ~2 години история не е огромна извадка - добър знак, не
    гаранция. Пазарът може да промени режим по всяко време.
  - Двете под-стратегии СЕ СЪГЛАСЯТ за посоката, за да има сигнал - никога
    форсирана LONG/SHORT догадка само за да има какво да пише в имейла.
    Ако не са съгласни (или едната мълчи), резултатът е честно "FLAT".
  - Това е технически индикатор, не финансов съвет. Новини (лихви,
    геополитика, макро данни) не се виждат в тези индикатори - провери сам.

Механизъм на двете под-стратегии (виж _quiet_accumulation_vote/
_session_momentum_vote по-долу за точната логика):
  - quiet_accumulation: пазарът е "тих" (ATR в долния квартил спрямо
    последните 100 свещи) + лек ценови дрифт за последните 5 свещи ->
    залага на продължение на този дрифт.
  - session_momentum: рязко движение (>= 1.2x ATR) за последните 3 свещи
    -> залага на продължение на моментума.
"""
from dataclasses import dataclass, field

import pandas as pd

import config

# --- Статистика от честния backtest (за прозрачност в имейла - НЕ участва
# в самото изчисление на сигнала по-долу) ------------------------------------
BACKTEST_WIN_RATE_PCT = 72.4
BACKTEST_SIGNALS = 29
BACKTEST_OOS_OLDER_PCT = 68.4
BACKTEST_OOS_NEWER_PCT = 80.0
BACKTEST_RANDOM_BASELINE_PCT = 50.0  # ~48-51% в различните хоризонти

# --- Параметри на двете под-стратегии (точно както във validirания backtest) -
QUIET_ATR_LOOKBACK = 100
QUIET_ATR_QUANTILE = 0.25
QUIET_DRIFT_LOOKBACK = 5
QUIET_DRIFT_THRESHOLD = 0.001
MOMENTUM_LOOKBACK = 3
MOMENTUM_ATR_MULT = 1.2


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = _atr(out, config.ATR_PERIOD)
    return out


def _quiet_accumulation_vote(d: pd.DataFrame, i: int):
    """Връща (посока или None, ATR прага, използван за "тих" пазар)."""
    if i < QUIET_ATR_LOOKBACK or i < QUIET_DRIFT_LOOKBACK:
        return None, None
    window = d.iloc[i - QUIET_ATR_LOOKBACK:i]
    cur = d.iloc[i]
    atr_threshold = float(window["atr"].quantile(QUIET_ATR_QUANTILE))
    if cur["atr"] > atr_threshold:
        return None, atr_threshold
    ref_close = d.iloc[i - QUIET_DRIFT_LOOKBACK]["Close"]
    drift = (cur["Close"] - ref_close) / ref_close
    if drift > QUIET_DRIFT_THRESHOLD:
        return "LONG", atr_threshold
    if drift < -QUIET_DRIFT_THRESHOLD:
        return "SHORT", atr_threshold
    return None, atr_threshold


def _session_momentum_vote(d: pd.DataFrame, i: int):
    """Връща (посока или None, самото движение в цена за последните 3 свещи)."""
    if i < MOMENTUM_LOOKBACK:
        return None, None
    cur, ref = d.iloc[i], d.iloc[i - MOMENTUM_LOOKBACK]
    move = float(cur["Close"] - ref["Close"])
    if abs(move) < MOMENTUM_ATR_MULT * cur["atr"]:
        return None, move
    return ("LONG" if move > 0 else "SHORT"), move


@dataclass
class ChampionResult:
    direction: str  # "LONG", "SHORT", или "FLAT"
    price: float = 0.0
    atr: float = 0.0
    timestamp: object = None
    quiet_vote: str = None
    momentum_vote: str = None
    reasons: list = field(default_factory=list)
    enough_data: bool = True

    @property
    def stop_loss(self):
        if self.direction not in ("LONG", "SHORT"):
            return None
        dist = config.CHAMPION_STOP_ATR_MULT * self.atr
        return round(self.price - dist, 2) if self.direction == "LONG" else round(self.price + dist, 2)

    @property
    def take_profit(self):
        if self.direction not in ("LONG", "SHORT"):
            return None
        dist = config.CHAMPION_TP_ATR_MULT * self.atr
        return round(self.price + dist, 2) if self.direction == "LONG" else round(self.price - dist, 2)


def evaluate(df: pd.DataFrame) -> ChampionResult:
    """Гледа последната (най-новата) свещ в df и връща LONG/SHORT/FLAT.
    Причинно коректно - гледа само история до и включително последния ред,
    никога напред (без lookahead), както и във validирания backtest."""
    d = add_indicators(df).dropna(subset=["atr"])
    min_needed = QUIET_ATR_LOOKBACK + QUIET_DRIFT_LOOKBACK + 1
    if len(d) < min_needed:
        return ChampionResult(
            direction="FLAT",
            enough_data=False,
            reasons=[f"Няма достатъчно история още ({len(d)}/{min_needed} свещи) - изчакай следваща проверка."],
        )

    i = len(d) - 1
    cur = d.iloc[i]
    quiet_vote, atr_threshold = _quiet_accumulation_vote(d, i)
    momentum_vote, move = _session_momentum_vote(d, i)

    reasons = []
    if quiet_vote:
        reasons.append(
            f"quiet_accumulation: тих период (ATR {cur['atr']:.2f} <= прага {atr_threshold:.2f} от последните "
            f"{QUIET_ATR_LOOKBACK} свещи) + лек дрифт -> {quiet_vote}"
        )
    elif atr_threshold is not None:
        reasons.append(
            f"quiet_accumulation: няма мнение (ATR {cur['atr']:.2f}, прага {atr_threshold:.2f} - "
            "пазарът не е достатъчно тих, или няма ясен дрифт)"
        )
    else:
        reasons.append("quiet_accumulation: няма мнение (недостатъчно история за прага)")

    if momentum_vote:
        reasons.append(
            f"session_momentum: рязко движение {move:+.2f} за последните {MOMENTUM_LOOKBACK} свещи "
            f"(>= {MOMENTUM_ATR_MULT}x ATR) -> {momentum_vote}"
        )
    else:
        move_txt = f"{move:+.2f}" if move is not None else "?"
        reasons.append(f"session_momentum: няма мнение (движение {move_txt} - не е достатъчно рязко)")

    if quiet_vote and momentum_vote and quiet_vote == momentum_vote:
        direction = quiet_vote
        reasons.insert(0, f"И двете под-стратегии са съгласни за {direction} - сигнал.")
    else:
        direction = "FLAT"
        reasons.insert(0, "Под-стратегиите не са съгласни (или едната мълчи) - честно FLAT, без форсирана догадка.")

    return ChampionResult(
        direction=direction,
        price=float(cur["Close"]),
        atr=float(cur["atr"]),
        timestamp=d.index[-1],
        quiet_vote=quiet_vote,
        momentum_vote=momentum_vote,
        reasons=reasons,
    )
