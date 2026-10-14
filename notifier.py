"""
Sends alert emails through the Resend HTTP API (https://resend.com).

Resend is used instead of Gmail SMTP because Gmail App Passwords are
unavailable on accounts under Family Link / advanced protection, and
Resend needs only a plain API key, no OAuth or app-password setup.
"""
import logging

import requests

import champion_strategy
import config

log = logging.getLogger("gold_signal_bot.notifier")

RESEND_URL = "https://api.resend.com/emails"


def _send(subject: str, html: str) -> bool:
    if not config.RESEND_API_KEY:
        log.error("RESEND_API_KEY is not set - cannot send email. See README for setup steps.")
        return False

    payload = {
        "from": config.ALERT_FROM_EMAIL,
        "to": [config.ALERT_EMAIL],
        "subject": subject,
        "html": html,
    }
    headers = {"Authorization": f"Bearer {config.RESEND_API_KEY}"}

    try:
        resp = requests.post(RESEND_URL, json=payload, headers=headers, timeout=20)
        if resp.status_code >= 300:
            log.error("Resend API error %s: %s", resp.status_code, resp.text)
            return False
        # Log Resend's own message id (24.09, by user request after a "log
        # says sent but no email arrived" report) - the API returning 2xx
        # only means Resend ACCEPTED the request, not that it was delivered
        # to the inbox (it can still bounce/land in spam/get blocked
        # afterwards). Logging the id lets you look this exact message up
        # at resend.com/emails to see its real delivery status.
        resend_id = None
        try:
            resend_id = resp.json().get("id")
        except Exception:  # noqa: BLE001
            pass
        log.info("Alert email accepted by Resend: %s (id=%s, to=%s) - check resend.com/emails for actual delivery status",
                  subject, resend_id, config.ALERT_EMAIL)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send email via Resend: %s", exc)
        return False


def send_signal_email(result, instrument: dict) -> bool:
    is_buy = result.direction == "BUY"
    emoji = "🟢" if is_buy else "🔴"
    action_bg = "#e8f5e9" if is_buy else "#fdecea"
    action_color = "#1b5e20" if is_buy else "#b71c1c"
    action_text = "ПОКУПКА (BUY)" if is_buy else "ПРОДАЖБА (SELL)"
    name = instrument["name"]
    cur = instrument.get("currency", "$")

    reasons_html = "".join(f"<li>{r}</li>" for r in result.reasons)
    sizing_note = (
        "на база подобна ситуация от последните ~1-2 дни"
        if getattr(result, "sizing_method", "atr") == "analog"
        else "на база средна волатилност (ATR)"
    )

    subject = f"{emoji} Сигнал за {action_text} - {name} @ {cur}{result.price:,.2f}"
    html = f"""
    <div style="font-family: -apple-system, Arial, sans-serif; max-width: 560px; margin: 0 auto;">
      <div style="background:{action_bg}; color:{action_color}; padding:16px 20px; border-radius:10px 10px 0 0;">
        <h2 style="margin:0; font-size:20px;">{emoji} {name}: {action_text}</h2>
        <p style="margin:4px 0 0; font-size:14px;">Увереност: {result.score}/{result.max_score} потвърждаващи индикатора</p>
      </div>
      <div style="border:1px solid #eee; border-top:none; padding:20px; border-radius:0 0 10px 10px;">
        <table style="width:100%; border-collapse:collapse; font-size:14px; margin-bottom:16px;">
          <tr><td style="padding:4px 0; color:#666;">Цена</td><td style="text-align:right; font-weight:600;">{cur}{result.price:,.2f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">RSI (14)</td><td style="text-align:right;">{result.rsi:.1f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Предложен Stop-Loss</td><td style="text-align:right;">{cur}{result.stop_loss:,.2f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Предложен Take-Profit</td><td style="text-align:right;">{cur}{result.take_profit:,.2f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Час на сигнала (UTC)</td><td style="text-align:right;">{result.timestamp}</td></tr>
        </table>
        <p style="font-size:12px; color:#888; margin:-10px 0 16px;">Stop-Loss/Take-Profit изчислени {sizing_note} - нива, не гарантирани.</p>
        <p style="font-size:14px; color:#333; margin-bottom:6px;"><strong>Защо се задейства сигналът:</strong></p>
        <ul style="font-size:13px; color:#444; margin-top:0; padding-left:18px;">{reasons_html}</ul>
        <p style="font-size:11px; color:#999; margin-top:18px; border-top:1px solid #eee; padding-top:10px;">
          Автоматичен сигнал от технически индикатори (EMA, RSI, MACD, Bollinger Bands,
          свещни фигури, аналогични ситуации от последните дни).
          Това не е финансов съвет - пазарът се движи и от новини (лихви,
          геополитика, макро данни), които тези индикатори не виждат.
          Провери сам преди да отвориш позиция.
        </p>
      </div>
    </div>
    """
    return _send(subject, html)


def send_champion_signal_email(result, instrument_name: str = None) -> bool:
    """Имейл при LONG/SHORT сигнал от шампионската стратегия (виж
    champion_strategy.py) - изпраща се на всеки checkpoint, когато
    quiet_accumulation и ema_trend се съгласят."""
    instrument_name = instrument_name or config.GOLD_INSTRUMENT["name"]
    is_long = result.direction == "LONG"
    emoji = "🟢" if is_long else "🔴"
    action_bg = "#e8f5e9" if is_long else "#fdecea"
    action_color = "#1b5e20" if is_long else "#b71c1c"
    action_text = "LONG (покупка)" if is_long else "SHORT (продажба)"

    reasons_html = "".join(f"<li>{r}</li>" for r in result.reasons)
    dist, dist_pct = result.tp_sl_distance, result.tp_sl_distance_pct

    subject = f"{emoji} Шампионска стратегия: {action_text} - {instrument_name} @ ${result.price:,.2f}"
    html = f"""
    <div style="font-family: -apple-system, Arial, sans-serif; max-width: 560px; margin: 0 auto;">
      <div style="background:{action_bg}; color:{action_color}; padding:16px 20px; border-radius:10px 10px 0 0;">
        <h2 style="margin:0; font-size:20px;">{emoji} {instrument_name}: {action_text}</h2>
        <p style="margin:4px 0 0; font-size:14px;">quiet_accumulation + ema_trend са съгласни</p>
      </div>
      <div style="border:1px solid #eee; border-top:none; padding:20px; border-radius:0 0 10px 10px;">
        <table style="width:100%; border-collapse:collapse; font-size:14px; margin-bottom:16px;">
          <tr><td style="padding:4px 0; color:#666;">Цена (вход)</td><td style="text-align:right; font-weight:600;">${result.price:,.2f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Stop-Loss</td><td style="text-align:right;">${result.stop_loss:,.2f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Take-Profit</td><td style="text-align:right;">${result.take_profit:,.2f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Разстояние TP/SL (прибл.)</td><td style="text-align:right;">±${dist:,.2f} (~{dist_pct:.2f}%)</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Час на свещта</td><td style="text-align:right;">{result.timestamp}</td></tr>
        </table>
        <p style="font-size:14px; color:#333; margin-bottom:6px;"><strong>Защо:</strong></p>
        <ul style="font-size:13px; color:#444; margin-top:0; padding-left:18px;">{reasons_html}</ul>
        <p style="font-size:11px; color:#999; margin-top:18px; border-top:1px solid #eee; padding-top:10px;">
          Честен backtest (GC=F, 1h свещи, проверка в {champion_strategy.BACKTEST_CHECKPOINT}, 1:1 ATR R:R, ~880 дни история):
          {champion_strategy.BACKTEST_WIN_RATE_PCT}% win rate на {champion_strategy.BACKTEST_SIGNALS} сигнала,
          out-of-sample {champion_strategy.BACKTEST_OOS_OLDER_PCT}% -> {champion_strategy.BACKTEST_OOS_NEWER_PCT}%,
          4 отделни тримесечия {champion_strategy.BACKTEST_QUARTER_MIN_PCT}%-{champion_strategy.BACKTEST_QUARTER_MAX_PCT}%
          (random baseline ~{champion_strategy.BACKTEST_RANDOM_BASELINE_PCT}%). Това НЕ е гаранция за бъдещето -
          пазарът може да промени режим по всяко време, а новини (лихви, геополитика, макро данни) не се виждат
          в тези индикатори. Провери сам преди да отвориш позиция - не е финансов съвет.
        </p>
      </div>
    </div>
    """
    return _send(subject, html)


def send_champion_flat_email(result, instrument_name: str = None) -> bool:
    """Имейл при FLAT (под-стратегиите не са съгласни, или едната мълчи) -
    изпраща се ВИНАГИ на всеки checkpoint, за да има честен отговор на
    всяка проверка, без форсирана LONG/SHORT догадка само за да има какво
    да пише в имейла."""
    instrument_name = instrument_name or config.GOLD_INSTRUMENT["name"]
    reasons_html = "".join(f"<li>{r}</li>" for r in result.reasons)
    price_part = f" @ ${result.price:,.2f}" if result.price else ""
    subject = f"⚪ Шампионска стратегия: FLAT (без сигнал) - {instrument_name}{price_part}"
    html = f"""
    <div style="font-family: -apple-system, Arial, sans-serif; max-width: 560px; margin: 0 auto;">
      <div style="background:#f0f0f0; color:#555; padding:16px 20px; border-radius:10px 10px 0 0;">
        <h2 style="margin:0; font-size:20px;">⚪ {instrument_name}: FLAT</h2>
        <p style="margin:4px 0 0; font-size:14px;">Честно - няма ясен edge точно сега.</p>
      </div>
      <div style="border:1px solid #eee; border-top:none; padding:20px; border-radius:0 0 10px 10px;">
        <ul style="font-size:13px; color:#444; margin-top:0; padding-left:18px;">{reasons_html}</ul>
        <p style="font-size:11px; color:#999; margin-top:18px; border-top:1px solid #eee; padding-top:10px;">
          По-добре честно FLAT, отколкото форсирана LONG/SHORT догадка само за да има какво да пише в имейла -
          шампионската стратегия (quiet_accumulation+ema_trend) е валидирана само за моментите, в които
          двете под-стратегии реално се съгласят ({champion_strategy.BACKTEST_SIGNALS} сигнала в честния
          backtest, проверка в {champion_strategy.BACKTEST_CHECKPOINT}). Следващата проверка е на следващия checkpoint.
        </p>
      </div>
    </div>
    """
    return _send(subject, html)


def send_test_email() -> bool:
    import config
    names = [inst["name"] for inst in config.INSTRUMENTS]
    if config.ENABLE_GOLD:
        checkpoints = ", ".join(f"{h:02d}:{m:02d}" for h, m in config.CHAMPION_CHECKPOINTS)
        names.insert(0, f"{config.GOLD_INSTRUMENT['name']} (шампионска стратегия, checkpoint-и в {checkpoints} {config.CHAMPION_CHECKPOINTS_TZ})")
    names_str = ", ".join(names) or "(няма конфигурирани инструменти)"
    # By user request (02.10): текстът още казваше "само при покупка", въпреки
    # че от 24.09 ALERT_ONLY_BUY=false по подразбиране и SELL също се праща -
    # подвеждащо след всеки рестарт. Вече отразява реалната конфигурация.
    signal_kind = "сигнал за покупка" if config.ALERT_ONLY_BUY else "сигнал за покупка или продажба"
    return _send(
        "✅ GoldSignalBot е стартиран",
        f"<p>Ботът стартира успешно и ще ти изпраща имейл при {signal_kind} "
        f"на следените инструменти: {names_str}.</p>",
    )


def send_error_email(instrument_key: str, message: str) -> bool:
    return _send(
        f"⚠️ GoldSignalBot - грешка при извличане на данни ({instrument_key})",
        f"<p>Ботът не успя да изтегли цената за {instrument_key} няколко пъти подред:</p><pre>{message}</pre>",
    )
