# Disneyland Ride Alerts 🎢

Emails you when specific **Disneyland Park (California)** rides open and when
their wait time drops under a threshold (default **30 minutes**).

Watched rides (out of the box):

- **Star Wars: Rise of the Resistance**
- **Millennium Falcon: Smugglers Run**

Data comes from the free [queue-times.com](https://queue-times.com) API
(Disneyland Park = park id `16`). The service uses **only the Python standard
library** — no `pip install` required.

> ☁️ **Want it always-on with real hands-free email?** Deploy it to Google Cloud
> (Cloud Functions + Scheduler + Storage) for ~$0 — see **[DEPLOY.md](DEPLOY.md)**.
> This is the most reliable option and removes the iOS email limitation.
> **Setting it up from a phone?** Follow **[SETUP_ON_PHONE.md](SETUP_ON_PHONE.md)**
> — the whole thing works in Google Cloud Shell via one script (`setup_gcp.sh`).
>
> 📱 **No computer? On an iPhone, no cloud?** iOS can't keep this Python loop
> running in the background. See **[iOS_SETUP.md](iOS_SETUP.md)** for a phone-only
> setup using Apple Shortcuts + Personal Automations.

## What you get

For each watched ride, on each day:

1. **"Ride opened"** email the first time the ride flips from closed → open.
2. **"Low wait"** email the first time the wait drops under your threshold.
   - The low-wait alert **re-arms** if the wait climbs back above the
     threshold, so you'll be told again the next time it dips.

Alerts are de-duplicated using a small `alert_state.json` file, and the state
resets each new day ("for today").

## Setup

### 1. Create a Gmail App Password (one time)

Gmail blocks normal-password SMTP logins, so you need an **App Password**:

1. Enable 2-Step Verification on your Google account.
2. Go to <https://myaccount.google.com/apppasswords>.
3. Create an app password (e.g. name it "Disneyland Alerts").
4. Copy the 16-character password.

### 2. Configure environment variables

```bash
export GMAIL_ADDRESS="youraddress@gmail.com"      # the account that SENDS alerts
export GMAIL_APP_PASSWORD="abcd efgh ijkl mnop"   # the App Password from step 1
export ALERT_RECIPIENT="rsalazarzugasti@gmail.com"  # where alerts go (default)
# Optional:
export WAIT_THRESHOLD=30          # minutes (default 30)
export POLL_INTERVAL=300          # seconds between checks in --loop (default 300)
```

A template is provided in [`alerts.env.example`](alerts.env.example):

```bash
cp alerts.env.example alerts.env
# edit alerts.env with your values, then:
set -a && source alerts.env && set +a
```

### 3. Verify email works

```bash
python3 ride_alerts.py --test-email
```

## Running it

```bash
# Single check (great for cron). Sends any due alerts and exits.
python3 ride_alerts.py --once

# Continuous loop, checking every POLL_INTERVAL seconds.
python3 ride_alerts.py --loop

# Print what WOULD be sent without emailing (no SMTP needed).
python3 ride_alerts.py --once --dry-run
```

### Run all day with cron (recommended)

To monitor "for today" only during park hours, add a crontab entry that runs
every 5 minutes between 7am and midnight:

```cron
*/5 7-23 * * * cd /path/to/disneyland_ride_alerts && \
  set -a && . ./alerts.env && set +a && \
  /usr/bin/python3 ride_alerts.py --once >> alerts.log 2>&1
```

### Or run the loop under systemd

```ini
# /etc/systemd/system/disneyland-alerts.service
[Unit]
Description=Disneyland ride alerts
After=network-online.target

[Service]
WorkingDirectory=/path/to/disneyland_ride_alerts
EnvironmentFile=/path/to/disneyland_ride_alerts/alerts.env
ExecStart=/usr/bin/python3 ride_alerts.py --loop
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Customizing which rides are watched

Edit the `WATCHED_RIDES` set near the top of `ride_alerts.py`. Names must match
the API exactly. To discover other ride names:

```bash
curl -s https://queue-times.com/parks/16/queue_times.json | python3 -m json.tool
```

(Use park id `17` for Disney California Adventure.)

## Notes

- If email isn't configured, the service still runs and **prints** the alerts
  it would have sent, so you can test without credentials.
- `alert_state.json` is created next to the script; it's safe to delete to
  reset alert tracking.
