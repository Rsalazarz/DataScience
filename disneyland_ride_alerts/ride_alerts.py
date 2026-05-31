#!/usr/bin/env python3
"""Disneyland / California Adventure ride alert service.

Monitors specific rides at the Disneyland Resort (California) using the free
queue-times.com API and emails alerts when:

  1. A watched ride OPENS (transitions from closed -> open), and
  2. A watched ride's wait time drops UNDER one of the configured thresholds
     (default: 20 minutes, then 10 minutes -- you get a heads-up at 20 and a
     more urgent ping if it falls under 10).

Rides span BOTH parks (Disneyland Park id 16 and Disney California Adventure
id 17), so both are polled.

No third-party packages are required for the CLI -- it uses only the Python
standard library (urllib + smtplib). The Cloud Function (main.py) reuses the
shared logic here.

Configuration via environment variables:

  GMAIL_ADDRESS        Gmail address used to SEND the alerts (the SMTP login).
  GMAIL_APP_PASSWORD   A Google "App Password" (NOT your normal password).
  ALERT_RECIPIENT      Where to send alerts. Default: rsalazarzugasti@gmail.com
  WAIT_THRESHOLDS      Comma-separated minutes, e.g. "20,10". Default: "20,10".
  POLL_INTERVAL        Seconds between checks in --loop mode. Default: 60.
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

# --- Parks at the Disneyland Resort (California) on queue-times.com ----------
PARKS = {
    16: "Disneyland Park",
    17: "Disney California Adventure",
}
QUEUE_TIMES_URL = "https://queue-times.com/parks/{park_id}/queue_times.json"

# Rides we monitor. These must match the API's "name" field EXACTLY.
WATCHED_RIDES = {
    # Disneyland Park
    "Star Wars: Rise of the Resistance",
    "Millennium Falcon: Smugglers Run",
    "Star Tours - The Adventures Continue",
    "Big Thunder Mountain Railroad",
    '"it\'s a small world"',
    # Disney California Adventure
    "Grizzly River Run",
    "Radiator Springs Racers",
    "Monsters, Inc. Mike & Sulley to the Rescue!",
}

DEFAULT_RECIPIENT = "rsalazarzugasti@gmail.com"
DEFAULT_WAIT_THRESHOLDS = [20, 10]   # minutes
DEFAULT_POLL_INTERVAL = 60           # seconds
DEFAULT_STATE_FILE = os.path.join(os.path.dirname(__file__), "alert_state.json")

USER_AGENT = "disneyland-ride-alerts/2.0 (+personal monitoring script)"


# --- Configuration ----------------------------------------------------------
class Config:
    def __init__(self) -> None:
        self.gmail_address = os.environ.get("GMAIL_ADDRESS", "").strip()
        self.gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
        self.recipient = os.environ.get("ALERT_RECIPIENT", DEFAULT_RECIPIENT).strip()
        self.wait_thresholds = self._parse_thresholds()
        self.poll_interval = int(os.environ.get("POLL_INTERVAL", DEFAULT_POLL_INTERVAL))
        self.state_file = os.environ.get("STATE_FILE", DEFAULT_STATE_FILE)
        self.smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
        self.smtp_port = int(os.environ.get("SMTP_PORT", 465))

    @staticmethod
    def _parse_thresholds() -> list[int]:
        raw = os.environ.get("WAIT_THRESHOLDS", "").strip()
        if raw:
            vals = {int(x) for x in raw.replace(" ", "").split(",") if x}
            return sorted(vals, reverse=True)
        if os.environ.get("WAIT_THRESHOLD"):  # legacy single-value support
            return [int(os.environ["WAIT_THRESHOLD"])]
        return list(DEFAULT_WAIT_THRESHOLDS)

    def email_configured(self) -> bool:
        return bool(self.gmail_address and self.gmail_app_password and self.recipient)


# --- Data fetching ----------------------------------------------------------
def _fetch_park(park_id: int) -> list[dict]:
    url = QUEUE_TIMES_URL.format(park_id=park_id)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rides = list(payload.get("rides", []))
    for land in payload.get("lands", []):
        rides.extend(land.get("rides", []))
    return rides


def fetch_ride_status() -> dict[str, dict]:
    """Return {ride_name: {is_open, wait_time, park, park_id, last_updated}}.

    Polls every park in PARKS and keeps only the rides in WATCHED_RIDES.
    """
    result: dict[str, dict] = {}
    for park_id, park_name in PARKS.items():
        for ride in _fetch_park(park_id):
            name = ride.get("name")
            if name in WATCHED_RIDES:
                result[name] = {
                    "is_open": bool(ride.get("is_open", False)),
                    "wait_time": int(ride.get("wait_time") or 0),
                    "park": park_name,
                    "park_id": park_id,
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
    if state.get("date") != today_key():
        state = {"date": today_key(), "rides": {}}
    state.setdefault("rides", {})
    return state


def save_state(path: str, state: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, path)


# --- Email formatting -------------------------------------------------------
def _alert_message(kind: str, name: str, info: dict, stamp: str,
                   tier: int | None = None) -> dict:
    """Build a {subject, body, html} message for an OPEN or LOW-wait alert."""
    park = info["park"]
    wait = info["wait_time"]
    url = f"https://queue-times.com/parks/{info['park_id']}"

    if kind == "open":
        emoji, headline, color = "🎢", "Now Open", "#1a9e4b"
        subject = f"🎢 OPEN: {name} ({wait} min)"
        note = "This ride just opened."
    else:
        emoji = "⏱️"
        headline = f"Short Wait: {wait} min"
        color = "#d23b3b" if (tier or 0) <= 10 else "#e67e22"
        subject = f"⏱️ {name} — {wait} min wait (under {tier})"
        note = f"Dropped under your {tier}-minute alert."

    plain = (
        f"{name}\n{park}\n\n"
        f"{headline}\n"
        f"Current wait: {wait} minutes\n"
        f"{note}\n"
        f"As of {stamp}\n\n"
        f"Live wait times: {url}"
    )

    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;'
        'border:1px solid #eee;border-radius:10px;overflow:hidden">'
        f'<div style="background:{color};color:#fff;padding:14px 18px;'
        f'font-size:18px;font-weight:bold">{emoji} {headline}</div>'
        '<div style="padding:18px">'
        f'<div style="font-size:20px;font-weight:bold;margin-bottom:2px">{name}</div>'
        f'<div style="color:#666;margin-bottom:14px">{park}</div>'
        f'<div style="font-size:40px;font-weight:bold;color:{color};line-height:1">'
        f'{wait}<span style="font-size:18px;color:#666;font-weight:normal"> min wait</span></div>'
        f'<div style="margin:12px 0;color:#444">{note}</div>'
        f'<div style="color:#999;font-size:12px;margin-bottom:16px">As of {stamp}</div>'
        f'<a href="{url}" style="display:inline-block;background:{color};color:#fff;'
        'text-decoration:none;padding:10px 16px;border-radius:6px;font-weight:bold">'
        'See live wait times &rarr;</a>'
        '</div></div>'
    )
    return {"subject": subject, "body": plain, "html": html}


# --- Alert engine (shared by CLI and the Cloud Function) --------------------
def evaluate_alerts(statuses: dict[str, dict], ride_states: dict[str, dict],
                    thresholds: list[int], stamp: str) -> list[dict]:
    """Return the {subject, body, html} messages due now, mutating ride_states.

    Tiered behaviour: with thresholds [20, 10] you get an alert the first time a
    ride dips under 20, and a second (more urgent) one if it then dips under 10.
    A tier re-arms only after the wait climbs back above the highest threshold
    (or the ride closes), so small fluctuations don't spam you.
    """
    thresholds = sorted(thresholds, reverse=True)
    messages: list[dict] = []

    for name in sorted(WATCHED_RIDES):
        info = statuses.get(name)
        if info is None:
            continue

        rs = ride_states.setdefault(name, {})
        rs.setdefault("opened_notified", False)
        rs.setdefault("last_tier", None)

        if not info["is_open"]:
            rs["last_tier"] = None        # re-arm for the next opening
            continue

        just_opened = False
        if not rs["opened_notified"]:
            messages.append(_alert_message("open", name, info, stamp))
            rs["opened_notified"] = True
            just_opened = True

        wait = info["wait_time"]
        crossed = [t for t in thresholds if wait < t]
        current_tier = min(crossed) if crossed else None
        last = rs["last_tier"]

        if current_tier is None:
            rs["last_tier"] = None        # above all thresholds -> re-arm
        elif last is None or current_tier < last:
            # Entered a more urgent tier. The "just opened" email already shows
            # the (short) wait, so don't double-send in that single case.
            if not just_opened:
                messages.append(_alert_message("low", name, info, stamp, tier=current_tier))
            rs["last_tier"] = current_tier
        # else: still in the same/less-urgent tier -> no new alert

    return messages


# --- Email sending ----------------------------------------------------------
def send_email(cfg: Config, subject: str, body: str, html: str | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.gmail_address
    msg["To"] = cfg.recipient
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=30) as smtp:
        smtp.login(cfg.gmail_address, cfg.gmail_app_password)
        smtp.send_message(msg)


def deliver(cfg: Config, msg: dict, dry_run: bool) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if dry_run or not cfg.email_configured():
        prefix = "[DRY-RUN]" if dry_run else "[EMAIL NOT CONFIGURED]"
        print(f"{stamp} {prefix} would send to {cfg.recipient}")
        print(f"  Subject: {msg['subject']}")
        for line in msg["body"].splitlines():
            print(f"  {line}")
        return
    send_email(cfg, msg["subject"], msg["body"], msg.get("html"))
    print(f"{stamp} Sent alert to {cfg.recipient}: {msg['subject']}")


# --- Core check -------------------------------------------------------------
def check_once(cfg: Config, dry_run: bool = False) -> None:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        statuses = fetch_ride_status()
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        print(f"{stamp} ERROR fetching ride status: {exc}", file=sys.stderr)
        return

    state = load_state(cfg.state_file)

    for name in sorted(WATCHED_RIDES):
        info = statuses.get(name)
        if info is None:
            print(f"{stamp} {name}: not found in API response")
            continue
        status_word = f"OPEN ({info['wait_time']} min)" if info["is_open"] else "closed"
        print(f"{stamp} [{info['park']}] {name}: {status_word}")

    messages = evaluate_alerts(statuses, state["rides"], cfg.wait_thresholds, stamp)
    for msg in messages:
        deliver(cfg, msg, dry_run=dry_run)

    save_state(cfg.state_file, state)


def run_loop(cfg: Config, dry_run: bool = False) -> None:
    print(
        f"Starting Disneyland ride alert loop. Parks={list(PARKS.values())}, "
        f"thresholds={cfg.wait_thresholds} min, interval={cfg.poll_interval}s.\n"
        f"Watching {len(WATCHED_RIDES)} rides. Recipient: {cfg.recipient}\n"
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
                f"Watching {len(WATCHED_RIDES)} rides across "
                f"{', '.join(PARKS.values())}.\n"
                f"Alert thresholds: {cfg.wait_thresholds} minutes.\n"
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
    check_once(cfg, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
