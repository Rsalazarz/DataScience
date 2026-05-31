# Disneyland Ride Alerts — Google Cloud setup

<walkthrough-tutorial-duration duration="15"></walkthrough-tutorial-duration>

This walkthrough deploys an always-on service that emails you when **Star Wars:
Rise of the Resistance** or **Millennium Falcon: Smugglers Run** open or drop
under a 30-minute wait at Disneyland Park (California).

**On a phone:** tap the **copy/▶ button** on each command below to drop it into
the terminal — no manual copy/paste needed. Then press **Enter**.

## Before you begin

You need two things ready:

1. A **Google Cloud project with billing enabled**
   (create one at the [Cloud Console](https://console.cloud.google.com), then
   menu → Billing). You stay in the free tier (~$0).
2. A **Gmail App Password**
   ([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) —
   requires 2-Step Verification). Keep the 16-character code handy.

Click the **Cloud Shell project selector** (top of the terminal) and pick your
project so the script can auto-detect it.

## Run the setup

You should already be in the `disneyland_ride_alerts` folder (this link opened it
for you). Start the setup:

```bash
bash setup_gcp.sh
```

It will ask for:
- **Project ID** — press Enter to accept the detected one.
- **Gmail address** that sends the alerts.
- **Gmail App Password** — paste/type the 16-char code (the input stays hidden —
  that's expected).

Then type `y` to proceed. Deployment takes a few minutes — keep this tab open.

## Test it

Trigger an immediate poll:

```bash
gcloud scheduler jobs run disney-alerts-poll --location=us-central1
```

Check the logs (and look for an email if a ride is open and under 30 min):

```bash
gcloud functions logs read disney-ride-alerts --region=us-central1 --gen2 --limit=20
```

## You're done!

<walkthrough-conclusion-trophy></walkthrough-conclusion-trophy>

The service now polls every 10 minutes (8 AM–11 PM Pacific) and emails you on its
own — no phone or computer required.

**Useful later:**

```bash
# Pause in the off-season:
gcloud scheduler jobs pause disney-alerts-poll --location=us-central1
# Resume:
gcloud scheduler jobs resume disney-alerts-poll --location=us-central1
```
