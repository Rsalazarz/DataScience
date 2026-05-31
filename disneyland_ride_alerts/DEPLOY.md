# Deploy to Google Cloud (always-on, real email, ~$0)

This runs the ride alerts 24/7 in Google Cloud so your phone/computer don't have
to be involved at all, and delivers **real email** (no iOS "tap to send" limit).

```
Cloud Scheduler --(HTTPS)--> Cloud Function (gen2) --(SMTP)--> your inbox
                                    |
                                    +-- de-dup state in a Cloud Storage bucket
```

**Cost:** within the Google Cloud always-free tier at this volume
(Cloud Functions 2M calls/mo, Cloud Scheduler 3 jobs, tiny Storage). A
**billing account must still be enabled** on the project even though you won't be
charged.

---

## Prerequisites

- A Google Cloud project (`gcloud projects create my-disney-alerts` or use an
  existing one) with **billing enabled**.
- The [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed and
  authenticated: `gcloud auth login`.
- A Gmail **App Password** (https://myaccount.google.com/apppasswords) — same one
  the CLI uses.

Set some shell variables to reuse below:

```bash
export PROJECT_ID="my-disney-alerts"
export REGION="us-central1"
export BUCKET="${PROJECT_ID}-disney-alert-state"
export GMAIL_ADDRESS="youraddress@gmail.com"
export ALERT_RECIPIENT="rsalazarzugasti@gmail.com"

gcloud config set project "$PROJECT_ID"
```

---

## 1. Enable the APIs

```bash
gcloud services enable \
  cloudfunctions.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com
```

## 2. Create the state bucket

```bash
gcloud storage buckets create "gs://${BUCKET}" --location="$REGION"
```

## 3. Store the Gmail App Password in Secret Manager

```bash
printf '%s' 'your-16-char-app-password' | \
  gcloud secrets create gmail-app-password --data-file=-
```

## 4. Deploy the Cloud Function

Run from inside the `disneyland_ride_alerts/` directory (so `main.py`,
`ride_alerts.py`, and `requirements.txt` are all uploaded together):

```bash
gcloud functions deploy disney-ride-alerts \
  --gen2 --runtime=python311 --region="$REGION" \
  --source=. --entry-point=check_rides \
  --trigger-http --no-allow-unauthenticated \
  --set-env-vars="STATE_BUCKET=${BUCKET},GMAIL_ADDRESS=${GMAIL_ADDRESS},ALERT_RECIPIENT=${ALERT_RECIPIENT},WAIT_THRESHOLD=30" \
  --set-secrets="GMAIL_APP_PASSWORD=gmail-app-password:latest"
```

`--no-allow-unauthenticated` keeps the endpoint private; only the Scheduler
service account (next step) will be allowed to call it.

## 5. Give the function access to the bucket

The function runs as the project's default compute service account. Let it
read/write the state object:

```bash
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/storage.objectAdmin"
```

(Secret access is granted automatically by `--set-secrets`.)

## 6. Schedule it during park hours

Create a service account the Scheduler uses to invoke the function, allow it to
call the function, then create the job. Polls **every 10 minutes, 8 AM–11 PM
Pacific**:

```bash
gcloud iam service-accounts create disney-scheduler \
  --display-name="Disney alerts scheduler"
SCHED_SA="disney-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"

FUNC_URL=$(gcloud functions describe disney-ride-alerts \
  --region="$REGION" --gen2 --format='value(serviceConfig.uri)')

# Allow the scheduler SA to invoke the underlying Cloud Run service.
gcloud run services add-iam-policy-binding disney-ride-alerts \
  --region="$REGION" \
  --member="serviceAccount:${SCHED_SA}" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http disney-alerts-poll \
  --location="$REGION" \
  --schedule="*/10 8-22 * * *" \
  --time-zone="America/Los_Angeles" \
  --uri="$FUNC_URL" \
  --http-method=GET \
  --oidc-service-account-email="$SCHED_SA" \
  --oidc-token-audience="$FUNC_URL"
```

> `*/10 8-22` fires at :00,:10,… through 10:50 PM. Adjust the hours/interval to
> taste — more frequent = faster alerts, still free at this scale.

## 7. Test it

```bash
# Force an immediate run:
gcloud scheduler jobs run disney-alerts-poll --location="$REGION"

# Watch logs:
gcloud functions logs read disney-ride-alerts --region="$REGION" --gen2 --limit=20
```

When a watched ride is open and under 30 minutes, you'll get the email.

---

## Updating / tuning

- **Change threshold or recipient:** re-run the `gcloud functions deploy` command
  with new `--set-env-vars` (no code change needed).
- **Change schedule:** `gcloud scheduler jobs update http disney-alerts-poll --schedule="..." --location="$REGION"`.
- **Pause for the off-season:** `gcloud scheduler jobs pause disney-alerts-poll --location="$REGION"`.

## Tear down (stop all charges/activity)

```bash
gcloud scheduler jobs delete disney-alerts-poll --location="$REGION" --quiet
gcloud functions delete disney-ride-alerts --region="$REGION" --gen2 --quiet
gcloud storage rm --recursive "gs://${BUCKET}"
gcloud secrets delete gmail-app-password --quiet
```

## Alternatives within Google Cloud

- **SendGrid (via GCP Marketplace)** instead of Gmail SMTP — useful if you'd
  rather not use an App Password; swap `send_email` for a SendGrid API call.
- **Pub/Sub trigger** instead of HTTP — if you prefer Scheduler → Pub/Sub →
  Function. HTTP is simpler, so that's what's wired up here.
