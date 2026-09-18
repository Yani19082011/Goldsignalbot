"""
GoldSignalBot - watches gold (XAU/USD) and USA Tech 100 (Nasdaq-100) and
emails you when a confluence of technical indicators points to a buy or
sell opportunity on either one.

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
            "last_signal_sent": {"BUY": None, "SELL": None},  # direction -> datetime
            "consecutive_errors": 0,
            "last_error": None,
        }
        for inst in config.INSTRUMENTS
    },
}


def _should_send(inst_state: dict, direction: str) -> bool:
    """Dedupe: only re-alert the same direction after the cooldown window."""
    last_sent = inst_state["last_signal_sent"].get(direction)
    if last_sent is None:
        return True
    return datetime.now(timezone.utc) - last_sent > timedelta(hours=config.REALERT_COOLDOWN_HOURS)


def check_instrument(instrument: dict):
    key = instrument["key"]
    inst_state = state["instruments"][key]
    try:
        df = data_fetch.get_candles(instrument, interval=config.CANDLE_INTERVAL, period="60d")
        result = signals.evaluate(df)

        inst_state["last_check"] = datetime.now(timezone.utc).isoformat()
        inst_state["last_price"] = result.price
        inst_state["last_direction"] = result.direction
        inst_state["last_score"] = result.score
        inst_state["consecutive_errors"] = 0
        inst_state["last_error"] = None

        log.info(
            "[%s] Check complete: price=%.2f direction=%s score=%d/%d",
            key, result.price, result.direction, result.score, result.max_score,
        )

        if result.direction in ("BUY", "SELL") and _should_send(inst_state, result.direction):
            sent = notifier.send_signal_email(result, instrument)
            if sent:
                inst_state["last_signal_sent"][result.direction] = datetime.now(timezone.utc)

    except Exception as exc:  # noqa: BLE001
        inst_state["consecutive_errors"] += 1
        inst_state["last_error"] = str(exc)
        log.exception("[%s] Check failed", key)
        # Only email about infrastructure trouble after repeated failures,
        # so a single flaky request doesn't spam the inbox.
        if inst_state["consecutive_errors"] == 5:
            notifier.send_error_email(key, str(exc))


def check_once():
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


@app.route("/check-now")
def check_now():
    """Manually trigger an immediate check of every instrument (useful for
    testing after deploy)."""
    threading.Thread(target=check_once, daemon=True).start()
    return jsonify({"status": "check triggered, see /"})


def start_background_thread():
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()


start_background_thread()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT)
