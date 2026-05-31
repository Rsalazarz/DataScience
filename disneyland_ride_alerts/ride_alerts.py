#!/usr/bin/env python3
"""Disneyland ride alert service.

Monitors individual rides at Disneyland Park (California) using the free
queue-times.com API and emails alerts when:

  1. A watched ride OPENS (transitions from closed -> open), and
  2. A watched ride's wait time drops UNDER a threshold (default 30 minutes).

Out of the box it watches:
  * Star Wars: Rise of the Resistance
  * Millennium Falcon: Smugglers Run

No third-party packages are required -- it uses only the Python standard
library (urllib + smtplib).

Configuration is via environment variables (see README.md):

  GMAIL_ADDRESS        Gmail address used to SEND the alerts (the SMTP login).
  GMAIL_APP_PASSWORD   A Google "App Password" (NOT your normal password).
  ALERT_RECIPIENT      Where to send alerts. Default: rsalazarzugasti@gmail.com
  WAIT_THRESHOLD       Wait-time threshold in minutes. Default: 30
  POLL_INTERVAL        Seconds between checks in --loop mode. Default: 300
  STATE_FILE           Where to persist alert state. Default: alert_state.json

Usage:
  python ride_alerts.py --once        # check a single time (good for cron)
  python ride_alerts.py --loop        # run continuously, polling on interval
  python ride_alerts.py --test-email  # send a test email to verify SMTP config
  python ride_alerts.py --once --dry-run   # print alerts instead of emailing
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from email.message import EmailMessage

# --- Disneyland Park (California) on queue-times.com -------------------------
PARK_ID = 16
QUEUE_TIMES_URL = f"https://queue-times.com/parks/{PARK_ID}/queue_times.json"

# Rides we monitor. Keys are the exact names returned by the API.
WATCHED_RIDES = {
    "Star Wars: Rise of the Resistance",
    "Millennium Falcon: Smugglers Run",
}

DEFAULT_RECIPIENT = "rsalazarzugasti@gmail.com"
DEFAULT_WAIT_THRESHOLD = 30          # minutes
DEFAULT_POLL_INTERVAL = 300          # seconds (5 minutes)
DEFAULT_STATE_FILE = os.path.join(os.path.dirname(__file__), "alert_state.json")

USER_AGENT = "disneyland-ride-alerts/1.0 (+personal monitoring script)"


# --- Configuration ----------------------------------------------------------
class Config:
    def __init__(self) -> None:
        self.gmail_address = os.environ.get("GMAIL_ADDRESS", "").strip()
        self.gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
        self.recipient = os.environ.get("ALERT_RECIPIENT", DEFAULT_RECIPIENT).strip()
        self.wait_threshold = int(os.environ.get("WAIT_THRESHOLD", DEFAULT_WAIT_THRESHOLD))
        self.poll_interval = int(os.environ.get("POLL_INTERVAL", DEFAULT_POLL_INTERVAL))
        self.state_file = os.environ.get("STATE_FILE", DEFAULT_STATE_FILE)
        # Allow generic SMTP override; default to Gmail.
        self.smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
        self.smtp_port = int(os.environ.get("SMTP_PORT", 465))

    def email_configured(self) -> bool:
        return bool(self.gmail_address and self.gmail_app_password and self.recipient)


# --- Data fetching ----------------------------------------------------------
def fetch_ride_status(url: str = QUEUE_TIMES_URL) -> dict[str, dict]:
    """Return {ride_name: {"is_open": bool, "wait_time": int}} for watched rides.

    queue-times groups rides into "lands"; flat "rides" may also be present.
    We scan both and keep only the rides we care about.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    rides: list[dict] = list(payload.get("rides", []))
    for land in payload.get("lands", []):
        rides.extend(land.get("rides", []))

    result: dict[str, dict] = {}
    for ride in rides:
        name = ride.get("name")
        if name in WATCHED_RIDES:
            result[name] = {
                "is_open": bool(ride.get("is_open", False)),
                "wait_time": int(ride.get("wait_time") or 0),
                "last_updated": ride.get("last_updated"),
            }
    return result


# --- State persistence ------------------------------------------------------
def today_key() -> str:
    return dt.date.today().isoformat()


def load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    # State is scoped per-day so alerts reset each morning. "for today".
    if state.get("date") != today_key():
        state = {"date": today_key(), "rides": {}}
    state.setdefault("rides", {})
    return state


def save_state(path: str, state: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, path)


def _ride_state(state: dict, name: str) -> dict:
    return state["rides"].setdefault(
        name,
        {"opened_notified": False, "low_wait_notified": False},
    )


# --- Email ------------------------------------------------------------------
def send_email(cfg: Config, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.gmail_address
    msg["To"] = cfg.recipient
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=30) as smtp:
        smtp.login(cfg.gmail_address, cfg.gmail_app_password)
        smtp.send_message(msg)


def deliver(cfg: Config, subject: str, body: str, dry_run: bool) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if dry_run or not cfg.email_configured():
        prefix = "[DRY-RUN]" if dry_run else "[EMAIL NOT CONFIGURED]"
        print(f"{stamp} {prefix} would send to {cfg.recipient}")
        print(f"  Subject: {subject}")
        for line in body.splitlines():
            print(f"  {line}")
        return
    send_email(cfg, subject, body)
    print(f"{stamp} Sent alert to {cfg.recipient}: {subject}")


# --- Core check -------------------------------------------------------------
def check_once(cfg: Config, dry_run: bool = False) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        statuses = fetch_ride_status()
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        print(f"{stamp} ERROR fetching ride status: {exc}", file=sys.stderr)
        return

    state = load_state(cfg.state_file)

    # Log current status for each ride (CLI visibility).
    for name in sorted(WATCHED_RIDES):
        info = statuses.get(name)
        if info is None:
            print(f"{stamp} {name}: not found in API response")
            continue
        status_word = f"OPEN ({info['wait_time']} min)" if info["is_open"] else "closed"
        print(f"{stamp} {name}: {status_word}")

    # Decide which alerts are due (shared logic), then deliver them.
    messages = evaluate_alerts(statuses, state["rides"], cfg.wait_threshold, stamp)
    for msg in messages:
        deliver(cfg, msg["subject"], msg["body"], dry_run=dry_run)

    save_state(cfg.state_file, state)


def evaluate_alerts(
    statuses: dict[str, dict],
    ride_states: dict[str, dict],
    threshold: int,
    stamp: str,
) -> list[dict]:
    """Pure-ish alert engine shared by the CLI and the Cloud Function.

    Given the current ride statuses and the persisted per-ride alert state,
    return the list of {"subject", "body"} messages that should be sent now,
    mutating ``ride_states`` in place to record what was alerted.
    """
    messages: list[dict] = []
    for name in sorted(WATCHED_RIDES):
        info = statuses.get(name)
        if info is None:
            continue

        rs = ride_states.setdefault(
            name, {"opened_notified": False, "low_wait_notified": False}
        )
        is_open = info["is_open"]
        wait = info["wait_time"]

        # 1) Ride opened -> notify once per day.
        if is_open and not rs["opened_notified"]:
            messages.append(
                {
                    "subject": f"🎢 OPEN: {name} at Disneyland",
                    "body": (
                        f"{name} is now OPEN at Disneyland Park (California).\n\n"
                        f"Current wait time: {wait} minutes.\n"
                        f"Checked at: {stamp}\n\n"
                        f"Live wait times: https://queue-times.com/parks/{PARK_ID}"
                    ),
                }
            )
            rs["opened_notified"] = True

        # 2) Wait time under threshold -> notify once per "low-wait episode".
        if is_open and wait < threshold:
            if not rs["low_wait_notified"]:
                messages.append(
                    {
                        "subject": f"⏱️ LOW WAIT: {name} is {wait} min (< {threshold})",
                        "body": (
                            f"{name} at Disneyland Park (California) has a short wait!\n\n"
                            f"Current wait time: {wait} minutes "
                            f"(under your {threshold}-minute threshold).\n"
                            f"Checked at: {stamp}\n\n"
                            f"Go now: https://queue-times.com/parks/{PARK_ID}"
                        ),
                    }
                )
                rs["low_wait_notified"] = True
        else:
            # Wait rose back above threshold (or ride closed); re-arm the alert
            # so the next dip below the threshold notifies again.
            rs["low_wait_notified"] = False

    return messages


def run_loop(cfg: Config, dry_run: bool = False) -> None:
    print(
        f"Starting Disneyland ride alert loop. Park=Disneyland (id {PARK_ID}), "
        f"threshold={cfg.wait_threshold} min, interval={cfg.poll_interval}s.\n"
        f"Watching: {', '.join(sorted(WATCHED_RIDES))}\n"
        f"Recipient: {cfg.recipient}\n"
        f"Email configured: {cfg.email_configured()} (dry_run={dry_run})"
    )
    while True:
        check_once(cfg, dry_run=dry_run)
        time.sleep(cfg.poll_interval)


def send_test_email(cfg: Config) -> int:
    if not cfg.email_configured():
        print(
            "Email is NOT configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
            "(and optionally ALERT_RECIPIENT).",
            file=sys.stderr,
        )
        return 1
    try:
        send_email(
            cfg,
            subject="✅ Disneyland ride alerts: test email",
            body=(
                "This is a test from your Disneyland ride alert service.\n"
                f"Watching: {', '.join(sorted(WATCHED_RIDES))}\n"
                f"Wait-time threshold: {cfg.wait_threshold} minutes.\n"
                "If you received this, email alerts are working."
            ),
        )
    except (smtplib.SMTPException, OSError) as exc:
        print(f"Failed to send test email: {exc}", file=sys.stderr)
        return 1
    print(f"Test email sent to {cfg.recipient}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run a single check and exit (good for cron).")
    mode.add_argument("--loop", action="store_true", help="Run continuously, polling on POLL_INTERVAL.")
    mode.add_argument("--test-email", action="store_true", help="Send a test email and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print alerts instead of sending email.")
    args = parser.parse_args(argv)

    cfg = Config()

    if args.test_email:
        return send_test_email(cfg)
    if args.loop:
        run_loop(cfg, dry_run=args.dry_run)
        return 0
    # Default to a single check.
    check_once(cfg, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
