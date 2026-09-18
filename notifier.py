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
        log.info("Alert email sent: %s", subject)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send email via Resend: %s", exc)
        return False


def send_signal_email(result) -> bool:
    is_buy = result.direction == "BUY"
    emoji = "🟢" if is_buy else "🔴"
    action_bg = "#e8f5e9" if is_buy else "#fdecea"
    action_color = "#1b5e20" if is_buy else "#b71c1c"
    action_text = "ПОКУПКА (BUY)" if is_buy else "ПРОДАЖБА (SELL)"

    reasons_html = "".join(f"<li>{r}</li>" for r in result.reasons)

    subject = f"{emoji} Сигнал за {action_text} на злато @ ${result.price:,.2f}"
    html = f"""
    <div style="font-family: -apple-system, Arial, sans-serif; max-width: 560px; margin: 0 auto;">
      <div style="background:{action_bg}; color:{action_color}; padding:16px 20px; border-radius:10px 10px 0 0;">
        <h2 style="margin:0; font-size:20px;">{emoji} Сигнал: {action_text}</h2>
        <p style="margin:4px 0 0; font-size:14px;">Увереност: {result.score}/{result.max_score} потвърждаващи индикатора</p>
      </div>
      <div style="border:1px solid #eee; border-top:none; padding:20px; border-radius:0 0 10px 10px;">
        <table style="width:100%; border-collapse:collapse; font-size:14px; margin-bottom:16px;">
          <tr><td style="padding:4px 0; color:#666;">Цена на златото</td><td style="text-align:right; font-weight:600;">${result.price:,.2f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">RSI (14)</td><td style="text-align:right;">{result.rsi:.1f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Предложен Stop-Loss</td><td style="text-align:right;">${result.stop_loss:,.2f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Предложен Take-Profit</td><td style="text-align:right;">${result.take_profit:,.2f}</td></tr>
          <tr><td style="padding:4px 0; color:#666;">Час на сигнала (UTC)</td><td style="text-align:right;">{result.timestamp}</td></tr>
        </table>
        <p style="font-size:14px; color:#333; margin-bottom:6px;"><strong>Защо се задейства сигналът:</strong></p>
        <ul style="font-size:13px; color:#444; margin-top:0; padding-left:18px;">{reasons_html}</ul>
        <p style="font-size:11px; color:#999; margin-top:18px; border-top:1px solid #eee; padding-top:10px;">
          Автоматичен сигнал от технически индикатори (EMA, RSI, MACD, Bollinger Bands).
          Това не е финансов съвет - пазарът на злато се движи и от новини
          (лихви, геополитика, доларов индекс), които тези индикатори не виждат.
          Провери сам преди да отвориш позиция.
        </p>
      </div>
    </div>
    """
    return _send(subject, html)


def send_test_email() -> bool:
    return _send(
        "✅ GoldSignalBot е стартиран",
        "<p>Ботът стартира успешно и ще ти изпраща имейл при всеки сигнал за покупка "
        "или продажба на злато.</p>",
    )


def send_error_email(message: str) -> bool:
    return _send(
        "⚠️ GoldSignalBot - грешка при извличане на данни",
        f"<p>Ботът не успя да изтегли цената на златото няколко пъти подред:</p><pre>{message}</pre>",
    )
