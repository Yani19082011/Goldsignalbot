"""
GoldSignalBot - watches gold (XAU/USD) and emails you when a confluence
of technical indicators points to a buy or sell opportunity.

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

state = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "last_check": None,
    "last_price": None,
    "last_direction": "NONE",
    "last_score": 0,
    "last_signal_sent": {"BUY": None, "SELL": None},  # direction -> datetime
    "consecutive_errors": 0,
    "last_error": None,
}


def _should_send(direction: str) -> bool:
    """Dedupe: only re-alert the same direction after the cooldown window."""
    last_sent = state["last_signal_sent"].get(direction)
    if last_sent is None:
        return True
    return datetime.now(timezone.utc) - last_sent > timedelta(hours=config.REALERT_COOLDOWN_HOURS)


def check_once():
    try:
        df = data_fetch.get_candles(interval="60m", period="60d")
        result = signals.evaluate(df)

        state["last_check"] = datetime.now(timezone.utc).isoformat()
        state["last_price"] = result.price
        state["last_direction"] = result.direction
        state["last_score"] = result.score
        state["consecutive_errors"] = 0
        state["last_error"] = None

        log.info(
            "Check complete: price=%.2f direction=%s score=%d/%d",
            result.price, result.direction, result.score, result.max_score,
        )

        if result.direction in ("BUY", "SELL") and _should_send(result.direction):
            sent = notifier.send_signal_email(result)
            if sent:
                state["last_signal_sent"][result.direction] = datetime.now(timezone.utc)

    except Exception as exc:  # noqa: BLE001
        state["consecutive_errors"] += 1
        state["last_error"] = str(exc)
        log.exception("Check failed")
        # Only email about infrastructure trouble after repeated failures,
        # so a single flaky request doesn't spam the inbox.
        if state["consecutive_errors"] == 5:
            notifier.send_error_email(str(exc))


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
    """Manually trigger an immediate check (useful for testing after deploy)."""
    threading.Thread(target=check_once, daemon=True).start()
    return jsonify({"status": "check triggered, see /"})


def start_background_thread():
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()


start_background_thread()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT)
