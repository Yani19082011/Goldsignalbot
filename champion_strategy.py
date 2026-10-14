"""
Шампионската стратегия за злато - quiet_accumulation + ema_trend.

Намерена чрез разширено strategy-search backtest (отделен проект,
GoldBacktest/gold_hour_structure_search.py + gold_quiet_accumulation_deepdive.py
+ gold_combo_oos_check.py), в който бяха честно тествани 47 различни
стратегии (+ комбинации по 2) на GC=F, 1-часови свещи, на ВСИЧКИ 24 часа
на денонощието (не само 3 фиксирани checkpoint-а), с 1:1 ATR risk:reward.
Тази комбинация - проверявана САМО в 09:00 Europe/Sofia, с 24-часов
хоризонт за TP/SL - беше намерена като най-добрия баланс между честота
(колко често реално праща LONG/SHORT, не FLAT) и качество (win rate),
след няколко кръга допълнителни проверки:

  - Пълна налична история (~880 дни, GC=F): 123 сигнала, 63.3% win rate,
    бие random_baseline (~47.4%) с ~16 пункта.
  - Out-of-sample (по-стара/по-нова половина): 62.9% -> 64.9% - НЕ
    деградира, дори се подобрява в по-новата половина.
  - 4 НЕПРЕСИЧАЩИ СЕ тримесечия (честен regime-check, не кумулативен
    прозорец): 53.8% / 69.4% / 63.6% / 66.7% - нито едно тримесечие не
    падна под 50% (по-лошо от чиста монета).

ЧЕСТНО, преди да се довериш сляпо:
  - Валидирано е САМО на проверка в 09:00 Europe/Sofia с 24-часов TP/SL
    хоризонт - затова ботът сега проверява само в този един час на ден
    (виж CHAMPION_CHECKPOINTS в config.py), не 3 пъти както преди. Други
    часове/хоризонти бяха тествани и се представят различно (по-добре или
    по-зле) - не е същата валидация, затова не се ползват тук.
  - 123 сигнала за ~880 дни история е значително по-голяма извадка от
    предишната шампионска комбинация (quiet_accumulation+session_momentum,
    29 сигнала), но пак не е гаранция - пазарът може да промени режим по
    всяко време.
  - Двете под-стратегии СЕ СЪГЛАСЯТ за посоката, за да има сигнал - никога
    форсирана LONG/SHORT догадка само за да има какво да пише в имейла.
    Ако не са съгласни (или едната мълчи), резултатът е честно "FLAT".
  - Това е технически индикатор, не финансов съвет. Новини (лихви,
    геополитика, макро данни) не се виждат в тези индикатори - провери сам.

Механизъм на двете под-стратегии (виж _quiet_accumulation_vote/
_ema_trend_vote по-долу за точната логика):
  - quiet_accumulation: пазарът е "тих" (ATR в долния квартил спрямо
    последните 100 свещи) + лек ценови дрифт за последните 5 свещи ->
    залага на продължение на този дрифт.
  - ema_trend: трите EMA (9/21/50) и цената са подредени в ясен тренд
    (EMA9 над EMA21 И цената над EMA50 за LONG, обратното за SHORT) ->
    залага на продължение на установения тренд. Различен механизъм от
    старата session_momentum (която гледаше само рязко движение за
    последните 3 свещи) - тук се гледа по-"бавна", установена структура.
"""
from dataclasses import dataclass, field

import pandas as pd

import config

# --- Статистика от честния backtest (за прозрачност в имейла - НЕ участва
# в самото изчисление на сигнала по-долу) ------------------------------------
BACKTEST_WIN_RATE_PCT = 63.3
BACKTEST_SIGNALS = 123
BACKTEST_OOS_OLDER_PCT = 62.9
BACKTEST_OOS_NEWER_PCT = 64.9
BACKTEST_RANDOM_BASELINE_PCT = 47.4
BACKTEST_QUARTER_MIN_PCT = 53.8  # най-слабото от 4-те непресичащи се тримесечия
BACKTEST_QUARTER_MAX_PCT = 69.4  # най-силното от тях
BACKTEST_CHECKPOINT = "09:00 Europe/Sofia, 24ч хоризонт"

# --- Параметри на двете под-стратегии (точно както във validirания backtest) -
QUIET_ATR_LOOKBACK = 100
QUIET_ATR_QUANTILE = 0.25
QUIET_DRIFT_LOOKBACK = 5
QUIET_DRIFT_THRESHOLD = 0.001
EMA_FAST_PERIOD = 9
EMA_MID_PERIOD = 21
EMA_SLOW_PERIOD = 50


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = _atr(out, config.ATR_PERIOD)
    out["ema9"] = _ema(out["Close"], EMA_FAST_PERIOD)
    out["ema21"] = _ema(out["Close"], EMA_MID_PERIOD)
    out["ema50"] = _ema(out["Close"], EMA_SLOW_PERIOD)
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


def _ema_trend_vote(d: pd.DataFrame, i: int):
    """Връща (посока или None, речник с текущите EMA/цена стойности) -
    EMA9 над EMA21 И цената над EMA50 -> LONG; обратното -> SHORT; иначе
    без мнение. Причинно коректно - гледа само реда i, който вече отразява
    само минала история (EMA е плъзгаща средна назад, без lookahead)."""
    cur = d.iloc[i]
    info = {"ema9": float(cur["ema9"]), "ema21": float(cur["ema21"]),
            "ema50": float(cur["ema50"]), "close": float(cur["Close"])}
    if cur["ema9"] > cur["ema21"] and cur["Close"] > cur["ema50"]:
        return "LONG", info
    if cur["ema9"] < cur["ema21"] and cur["Close"] < cur["ema50"]:
        return "SHORT", info
    return None, info


@dataclass
class ChampionResult:
    direction: str  # "LONG", "SHORT", или "FLAT"
    price: float = 0.0
    atr: float = 0.0
    timestamp: object = None
    quiet_vote: str = None
    trend_vote: str = None
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

    @property
    def tp_sl_distance(self):
        """Приблизителното разстояние (в цената, не %) от входа до TP/SL -
        и двете са на еднакво разстояние (1:1 ATR risk:reward), затова е
        една стойност. None, ако няма LONG/SHORT сигнал."""
        if self.direction not in ("LONG", "SHORT"):
            return None
        return round(config.CHAMPION_TP_ATR_MULT * self.atr, 2)

    @property
    def tp_sl_distance_pct(self):
        if self.direction not in ("LONG", "SHORT") or not self.price:
            return None
        return round(self.tp_sl_distance / self.price * 100, 2)


def evaluate(df: pd.DataFrame) -> ChampionResult:
    """Гледа последната (най-новата) свещ в df и връща LONG/SHORT/FLAT.
    Причинно коректно - гледа само история до и включително последния ред,
    никога напред (без lookahead), както и във validirания backtest."""
    d = add_indicators(df).dropna(subset=["atr", "ema50"])
    min_needed = max(QUIET_ATR_LOOKBACK + QUIET_DRIFT_LOOKBACK, EMA_SLOW_PERIOD) + 1
    if len(d) < min_needed:
        return ChampionResult(
            direction="FLAT",
            enough_data=False,
            reasons=[f"Няма достатъчно история още ({len(d)}/{min_needed} свещи) - изчакай следваща проверка."],
        )

    i = len(d) - 1
    cur = d.iloc[i]
    quiet_vote, atr_threshold = _quiet_accumulation_vote(d, i)
    trend_vote, ema_info = _ema_trend_vote(d, i)

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

    if trend_vote:
        reasons.append(
            f"ema_trend: EMA9 {ema_info['ema9']:.2f} {'>' if trend_vote == 'LONG' else '<'} "
            f"EMA21 {ema_info['ema21']:.2f} И цена {ema_info['close']:.2f} "
            f"{'>' if trend_vote == 'LONG' else '<'} EMA50 {ema_info['ema50']:.2f} -> {trend_vote}"
        )
    else:
        reasons.append(
            f"ema_trend: няма мнение (EMA9 {ema_info['ema9']:.2f}, EMA21 {ema_info['ema21']:.2f}, "
            f"EMA50 {ema_info['ema50']:.2f}, цена {ema_info['close']:.2f} - няма ясно подреден тренд)"
        )

    if quiet_vote and trend_vote and quiet_vote == trend_vote:
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
        trend_vote=trend_vote,
        reasons=reasons,
    )
