"""
GoldSignalBot - by default watches USA Tech 100 (Nasdaq-100) only (gold
support is still fully in place - set ENABLE_GOLD=true in Render to bring
it back) and emails you when a confluence of technical indicators, plus an
analog-match against similar recent setups, points to a buy opportunity.

Runs as a Flask web service (for Render's free tier, which requires an
HTTP port to stay awake) with a background thread doing the actual
market checks on a timer. Pair with an external keep-alive pinger
(e.g. cron-job.org hitting "/" every ~10 min) so Render's free tier
doesn't spin the service down from inactivity.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify

import config
import data_fetch
import notifier
import signals

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gold_signal_bot")

app = Flask(__name__)

# One state block per configured instrument (config.INSTRUMENTS), keyed by
# instrument "key" (e.g. "GOLD", "TECH100").
state = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "instruments": {
        inst["key"]: {
            "name": inst["name"],
            "last_check": None,
            "last_price": None,
            "last_direction": "NONE",
            "last_score": 0,
            "last_signal_sent": {"BUY": None, "SELL": None},  # direction -> {"time": datetime, "score": int}
            "consecutive_errors": 0,
            "last_error": None,
            "last_data_source": None,  # which provider actually served the last successful check - see data_fetch.get_candles
        }
        for inst in config.INSTRUMENTS
    },
}


def _within_active_window() -> bool:
    """By user request (18.09): Monday-Friday only, config.ACTIVE_START_*
    to config.ACTIVE_END_* in config.ACTIVE_HOURS_TZ. If the timezone data
    is somehow unavailable, fail open (check anyway) rather than going
    silent because of a bug here."""
    try:
        tz = ZoneInfo(config.ACTIVE_HOURS_TZ)
    except Exception:
        log.warning("ACTIVE_HOURS_TZ (%s) invalid - skipping the active-window check.", config.ACTIVE_HOURS_TZ)
        return True
    now_local = datetime.now(tz)
    if config.ACTIVE_DAYS_ONLY and now_local.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    start = now_local.replace(hour=config.ACTIVE_START_HOUR, minute=config.ACTIVE_START_MINUTE, second=0, microsecond=0)
    end = now_local.replace(hour=config.ACTIVE_END_HOUR, minute=config.ACTIVE_END_MINUTE, second=0, microsecond=0)
    return start <= now_local <= end


def _cooldown_hours_for(score: int) -> float:
    """By user request (24.09): a strong/high-conviction signal gets a
    longer cooldown before the next alert - see the comment on
    STRONG_SIGNAL_SCORE_THRESHOLD in config.py."""
    if score >= config.STRONG_SIGNAL_SCORE_THRESHOLD:
        return config.STRONG_SIGNAL_COOLDOWN_HOURS
    return config.REALERT_COOLDOWN_HOURS


def _should_send(inst_state: dict, direction: str) -> bool:
    """Dedupe: only re-alert the same direction after a cooldown that
    scales with how strong the LAST SENT alert for that direction was
    (not the current check's score - the wait is about giving that earlier
    trade room to develop)."""
    last_sent = inst_state["last_signal_sent"].get(direction)
    if last_sent is None:
        return True
    cooldown_hours = _cooldown_hours_for(last_sent["score"])
    return datetime.now(timezone.utc) - last_sent["time"] > timedelta(hours=cooldown_hours)


def check_instrument(instrument: dict):
    key = instrument["key"]
    inst_state = state["instruments"][key]
    try:
        source_info = {}
        df = data_fetch.get_candles(instrument, interval=config.CANDLE_INTERVAL, period="60d", source_info=source_info)
        result = signals.evaluate(df)

        inst_state["last_check"] = datetime.now(timezone.utc).isoformat()
        inst_state["last_price"] = result.price
        inst_state["last_direction"] = result.direction
        inst_state["last_score"] = result.score
        inst_state["last_data_source"] = source_info.get("source")
        inst_state["consecutive_errors"] = 0
        inst_state["last_error"] = None

        log.info(
            "[%s] Check complete: price=%.2f direction=%s score=%d/%d",
            key, result.price, result.direction, result.score, result.max_score,
        )

        alertable_directions = ("BUY",) if config.ALERT_ONLY_BUY else ("BUY", "SELL")
        if result.direction in alertable_directions:
            if _should_send(inst_state, result.direction):
                sent = notifier.send_signal_email(result, instrument)
                if sent:
                    inst_state["last_signal_sent"][result.direction] = {
                        "time": datetime.now(timezone.utc), "score": result.score,
                    }
            else:
                # By user request (24.09, after a "log shows BUY but no email
                # arrived" report): make the cooldown skip visible in the
                # logs too, not just the ALERT_ONLY_BUY skip below - a
                # qualifying signal that doesn't email can otherwise look
                # identical to a bug.
                last_sent = inst_state["last_signal_sent"].get(result.direction)
                minutes_ago = (datetime.now(timezone.utc) - last_sent["time"]).total_seconds() / 60 if last_sent else None
                cooldown_minutes = _cooldown_hours_for(last_sent["score"]) * 60 if last_sent else config.REALERT_COOLDOWN_HOURS * 60
                log.info(
                    "[%s] %s setup seen (score=%d/%d) but still in cooldown (last %s alert was %.1f min ago, "
                    "scored %s/7 so cooldown is %.0f min) - not re-emailing.",
                    key, result.direction, result.score, result.max_score, result.direction,
                    minutes_ago if minutes_ago is not None else -1,
                    last_sent["score"] if last_sent else "?", cooldown_minutes,
                )
        elif result.direction == "SELL" and config.ALERT_ONLY_BUY:
            log.info("[%s] SELL setup seen (score=%d/%d) but ALERT_ONLY_BUY=true - not emailing.", key, result.score, result.max_score)

    except Exception as exc:  # noqa: BLE001
        inst_state["consecutive_errors"] += 1
        inst_state["last_error"] = str(exc)
        log.exception("[%s] Check failed", key)
        # Only email about infrastructure trouble after repeated failures,
        # so a single flaky request doesn't spam the inbox.
        if inst_state["consecutive_errors"] == 5:
            notifier.send_error_email(key, str(exc))


def check_once():
    if not _within_active_window():
        log.info(
            "Outside the active window (%02d:%02d-%02d:%02d %s, Mon-Fri only=%s) - skipping this cycle.",
            config.ACTIVE_START_HOUR, config.ACTIVE_START_MINUTE,
            config.ACTIVE_END_HOUR, config.ACTIVE_END_MINUTE,
            config.ACTIVE_HOURS_TZ, config.ACTIVE_DAYS_ONLY,
        )
        return
    for instrument in config.INSTRUMENTS:
        check_instrument(instrument)


def background_loop():
    if config.SEND_TEST_EMAIL_ON_START:
        notifier.send_test_email()
    while True:
        check_once()
        time.sleep(max(60, config.CHECK_INTERVAL_MINUTES * 60))


@app.route("/")
def health():
    """Health/keep-alive endpoint. Point your uptime pinger (cron-job.org,
    UptimeRobot, ...) at this URL every ~10 minutes."""
    return jsonify({
        "status": "ok",
        "bot": "GoldSignalBot",
        **state,
    })


def _check_all_instruments_now():
    """Same as check_once() but ignores the active window - a manual
    /check-now hit is an explicit request to test right now, weekend or
    3am included."""
    for instrument in config.INSTRUMENTS:
        check_instrument(instrument)


@app.route("/check-now")
def check_now():
    """Manually trigger an immediate check of every instrument, regardless
    of the active window (useful for testing after deploy, any time)."""
    threading.Thread(target=_check_all_instruments_now, daemon=True).start()
    return jsonify({"status": "check triggered, see /"})


def start_background_thread():
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()


start_background_thread()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT)
