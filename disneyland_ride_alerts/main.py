"""Google Cloud Function entry point for the Disneyland ride alerts.

This wraps the shared alert logic in ``ride_alerts.py`` for a serverless,
always-on deployment:

  Cloud Scheduler --(HTTP)--> this function --(SMTP)--> your inbox
                                   |
                                   +-- state in Cloud Storage (de-dup)

Because Cloud Functions are stateless, the per-day alert state lives in a
Cloud Storage object instead of a local file.

Environment variables (set at deploy time):
  GMAIL_ADDRESS        Gmail address that sends the alerts (SMTP login).
  GMAIL_APP_PASSWORD   Google App Password (inject via Secret Manager).
  ALERT_RECIPIENT      Where alerts go. Default: rsalazarzugasti@gmail.com
  WAIT_THRESHOLD       Minutes. Default: 30
  STATE_BUCKET         Cloud Storage bucket name for state (required).
  STATE_BLOB           Object name for state. Default: alert_state.json

Deploy with --entry-point=check_rides (see DEPLOY.md).
"""

from __future__ import annotations

import datetime as dt
import json
import os

import functions_framework
from google.cloud import storage

import ride_alerts as ra

_storage_client: storage.Client | None = None


def _bucket():
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client()
    bucket_name = os.environ["STATE_BUCKET"]
    return _storage_client.bucket(bucket_name)


def _state_blob():
    return _bucket().blob(os.environ.get("STATE_BLOB", "alert_state.json"))


def load_state() -> dict:
    """Load per-day alert state from Cloud Storage (resets each new day)."""
    blob = _state_blob()
    state: dict = {}
    if blob.exists():
        try:
            state = json.loads(blob.download_as_text())
        except (ValueError, json.JSONDecodeError):
            state = {}
    if state.get("date") != ra.today_key():
        state = {"date": ra.today_key(), "rides": {}}
    state.setdefault("rides", {})
    return state


def save_state(state: dict) -> None:
    _state_blob().upload_from_string(
        json.dumps(state, indent=2), content_type="application/json"
    )


@functions_framework.http
def check_rides(request):
    """HTTP entry point invoked by Cloud Scheduler."""
    cfg = ra.Config()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    try:
        statuses = ra.fetch_ride_status()
    except Exception as exc:  # network/parse errors -> 500 so Scheduler retries
        print(f"{stamp} ERROR fetching ride status: {exc}")
        return (f"fetch error: {exc}", 500)

    state = load_state()
    messages = ra.evaluate_alerts(statuses, state["rides"], cfg.wait_threshold, stamp)

    sent = 0
    for msg in messages:
        if cfg.email_configured():
            ra.send_email(cfg, msg["subject"], msg["body"])
            sent += 1
            print(f"{stamp} sent: {msg['subject']}")
        else:
            print(f"{stamp} [EMAIL NOT CONFIGURED] would send: {msg['subject']}")

    save_state(state)

    summary = {
        name: (
            f"OPEN {info['wait_time']}m" if info["is_open"] else "closed"
        )
        for name, info in statuses.items()
    }
    print(f"{stamp} checked {summary}; alerts sent={sent}")
    return (json.dumps({"checked": summary, "alerts_sent": sent}), 200)
