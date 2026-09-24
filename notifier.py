"""
Sends alert emails through the Resend HTTP API (https://resend.com).

Resend is used instead of Gmail SMTP because Gmail App Passwords are
unavailable on accounts under Family Link / advanced protection, and
Resend needs only a plain API key, no OAuth or app-password setup.
"""
import logging

import requests

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


def send_test_email() -> bool:
    import config
    names = ", ".join(inst["name"] for inst in config.INSTRUMENTS) or "(няма конфигурирани инструменти)"
    return _send(
        "✅ GoldSignalBot е стартиран",
        f"<p>Ботът стартира успешно и ще ти изпраща имейл при сигнал за покупка "
        f"на следените инструменти: {names}.</p>",
    )


def send_error_email(instrument_key: str, message: str) -> bool:
    return _send(
        f"⚠️ GoldSignalBot - грешка при извличане на данни ({instrument_key})",
        f"<p>Ботът не успя да изтегли цената за {instrument_key} няколко пъти подред:</p><pre>{message}</pre>",
    )
