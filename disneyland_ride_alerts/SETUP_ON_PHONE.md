# Set up Google Cloud entirely from your iPhone 📱☁️

Yes — you can do all of this on your phone, no computer needed. The key tool is
**Google Cloud Shell**: a free, browser-based terminal that already has `gcloud`
installed and signed in. You'll clone this repo into it and run one script.

Budget ~20 minutes the first time. After this, the alerts run 24/7 on their own.

---

## Step 1 — Create a Google Cloud project with billing

In **Safari** (or the **Google Cloud** app from the App Store):

1. Go to <https://console.cloud.google.com> and sign in with your Google account.
2. Top bar → project picker → **New Project** → name it (e.g. `disney-alerts`) →
   **Create**. Note the **Project ID** (looks like `disney-alerts-123456`).
3. Open the menu (☰) → **Billing** → link a billing account (add a card).
   - This is required even though you'll stay in the **free tier (~$0)**.
   - New Google Cloud accounts also get a free trial credit.

> Tip: the mobile web console works best in "Desktop site" mode. In Safari tap
> **aA** (left of the address bar) → **Request Desktop Website**.

## Step 2 — Create a Gmail App Password

The service logs into Gmail to send mail, which needs an **App Password** (not
your normal password):

1. In Safari go to <https://myaccount.google.com/apppasswords>.
   - You must have **2-Step Verification** turned on first
     (<https://myaccount.google.com/signinoptions/twosv>).
2. Create an app password named e.g. `Disney Alerts`.
3. Copy the 16-character code somewhere you can paste it in Step 4
   (you'll paste it once into a hidden prompt).

## Step 3 — Open Cloud Shell and get the code

1. Go to <https://shell.cloud.google.com> (or in the Google Cloud app, tap the
   **>_** terminal icon, top right). Authorize it if asked.
2. Wait for the terminal prompt, then clone this repo and enter the folder:

   ```bash
   git clone https://github.com/Rsalazarz/DataScience.git
   cd DataScience/disneyland_ride_alerts
   ```

   - If the repo is **private**, Cloud Shell will ask for a username and a
     **GitHub personal access token** (create one at
     <https://github.com/settings/tokens> with `repo` scope) as the password.

## Step 4 — Run the setup script

```bash
bash setup_gcp.sh
```

It will ask you for:
- **Project ID** — from Step 1.
- **Gmail address** that sends the alerts.
- **Gmail App Password** — paste the code from Step 2 (input is hidden — that's
  normal, just paste and press return).

Then confirm with `y`. The script enables APIs, creates the bucket and secret,
deploys the function, sets permissions, and schedules polling **every 10 minutes
from 8 AM–11 PM Pacific**. It takes a few minutes — leave the tab open.

> Everything has sensible defaults. To change the recipient, threshold, or
> schedule, set them first, e.g.:
> ```bash
> WAIT_THRESHOLD=20 SCHEDULE="*/5 8-23 * * *" bash setup_gcp.sh
> ```

## Step 5 — Test it

```bash
gcloud scheduler jobs run disney-alerts-poll --location=us-central1
gcloud functions logs read disney-ride-alerts --region=us-central1 --gen2 --limit=20
```

When a watched ride is open and under 30 minutes, the email lands in
`rsalazarzugasti@gmail.com`. (If both rides are closed/long right now, the log
will just show their status and `alerts_sent=0` — that's correct.)

---

## Managing it later (all from Cloud Shell on your phone)

```bash
# Pause during the off-season (stops polling, keeps everything):
gcloud scheduler jobs pause disney-alerts-poll --location=us-central1
# Resume:
gcloud scheduler jobs resume disney-alerts-poll --location=us-central1

# Change threshold/recipient/schedule: edit values and re-run the script.
cd ~/DataScience/disneyland_ride_alerts && git pull && WAIT_THRESHOLD=25 bash setup_gcp.sh
```

To remove everything, see the **Tear down** section in [DEPLOY.md](DEPLOY.md).

---

## Things that trip people up

- **"Billing is not enabled"** — finish Step 1.3, then re-run the script.
- **App Password rejected** — make sure 2-Step Verification is on and you pasted
  the 16-char *app* password, not your account password.
- **Cloud Shell session timed out / disconnected** — it's ephemeral but your
  `git clone` persists in its home dir; just reopen and `cd` back in. Anything
  already deployed keeps running regardless of the shell.
- **Typing on a phone is fiddly** — paste commands from this file rather than
  typing them; Cloud Shell supports paste.
