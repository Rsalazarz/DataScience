#!/usr/bin/env bash
#
# One-shot Google Cloud setup for the Disneyland ride alerts.
# Designed to run in Google Cloud Shell (works from a phone browser).
#
# It enables APIs, creates the state bucket, stores your Gmail App Password
# in Secret Manager, deploys the Cloud Function, wires up IAM, and creates the
# Cloud Scheduler job. Safe to re-run (idempotent).
#
# Usage (from inside the disneyland_ride_alerts/ directory):
#   bash setup_gcp.sh
#
# You can pre-set any of these as environment variables to skip the prompts:
#   PROJECT_ID, REGION, GMAIL_ADDRESS, ALERT_RECIPIENT, WAIT_THRESHOLD,
#   SCHEDULE, TIMEZONE
#
set -euo pipefail

# --- Configuration (env var overrides, otherwise prompted/defaulted) --------
REGION="${REGION:-us-central1}"
ALERT_RECIPIENT="${ALERT_RECIPIENT:-rsalazarzugasti@gmail.com}"
WAIT_THRESHOLD="${WAIT_THRESHOLD:-30}"
SCHEDULE="${SCHEDULE:-*/10 8-22 * * *}"
TIMEZONE="${TIMEZONE:-America/Los_Angeles}"

prompt() {  # prompt VAR "message" -> loops until a non-empty value is given
  local _var="$1" _msg="$2" _val
  while [[ -z "${!_var:-}" ]]; do
    read -r -p "$_msg" _val
    printf -v "$_var" '%s' "$_val"
    [[ -z "${!_var:-}" ]] && echo "  (this is required)"
  done
}

echo "=== Disneyland ride alerts: Google Cloud setup ==="

# --- Auth check (first, so non-interactive runs fail fast and clearly) -------
# Cloud Shell sometimes starts without an active account selected, which makes
# later steps fail with confusing errors (e.g. a false "billing not enabled").
if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | grep -q .; then
  echo "[!] No active gcloud account in this session."
  echo "    First try:  gcloud config set account YOUR_EMAIL@gmail.com"
  echo "    If that fails:  gcloud auth login   (or click 'Authorize' in Cloud Shell)"
  echo "    Then re-run this script."
  exit 1
fi

# Default the project to whatever Cloud Shell / gcloud already has selected,
# so on a phone you usually don't have to type it.
if [[ -z "${PROJECT_ID:-}" ]]; then
  _cur="$(gcloud config get-value project 2>/dev/null || true)"
  if [[ -n "$_cur" && "$_cur" != "(unset)" ]]; then
    PROJECT_ID="$_cur"
    echo "Using already-selected project: $PROJECT_ID"
  fi
fi
prompt PROJECT_ID    "Google Cloud project ID: "
prompt GMAIL_ADDRESS "Gmail address that SENDS alerts (e.g. you@gmail.com): "

# Does the secret already exist? If so, the App Password is optional (you can
# keep the stored one). If not, it's required.
if gcloud secrets describe gmail-app-password --project="$PROJECT_ID" >/dev/null 2>&1; then
  SECRET_EXISTS=true
else
  SECRET_EXISTS=false
fi

# App Password is read silently and never echoed or stored on disk.
if [[ -z "${GMAIL_APP_PASSWORD:-}" ]]; then
  if $SECRET_EXISTS; then
    if [[ "${ASSUME_YES:-}" == "1" ]]; then
      echo "Keeping the stored Gmail App Password."
    else
      read -r -s -p "Gmail App Password (press Enter to keep the stored one): " GMAIL_APP_PASSWORD
      echo
    fi
  elif [[ "${ASSUME_YES:-}" == "1" ]]; then
    echo "[!] No stored secret and no GMAIL_APP_PASSWORD provided -- cannot run" >&2
    echo "    non-interactively. Re-run with GMAIL_APP_PASSWORD='your-app-password'." >&2
    exit 1
  else
    while [[ -z "${GMAIL_APP_PASSWORD:-}" ]]; do
      read -r -s -p "Gmail App Password (input hidden): " GMAIL_APP_PASSWORD
      echo
      [[ -z "${GMAIL_APP_PASSWORD:-}" ]] && echo "  (this is required)"
    done
  fi
fi

BUCKET="${BUCKET:-${PROJECT_ID}-disney-alert-state}"

echo
echo "Project:    $PROJECT_ID"
echo "Region:     $REGION"
echo "Bucket:     $BUCKET"
echo "Sender:     $GMAIL_ADDRESS"
echo "Recipient:  $ALERT_RECIPIENT"
echo "Threshold:  ${WAIT_THRESHOLD} min"
echo "Schedule:   '$SCHEDULE' ($TIMEZONE)"
echo
if [[ "${ASSUME_YES:-}" == "1" ]]; then
  echo "Proceeding (ASSUME_YES=1)."
else
  read -r -p "Proceed? [y/N] " ok
  [[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "Aborted."; exit 1; }
fi

gcloud config set project "$PROJECT_ID" >/dev/null

# --- 0. Billing sanity check (warn only) ------------------------------------
if gcloud billing projects describe "$PROJECT_ID" \
      --format='value(billingEnabled)' 2>/dev/null | grep -qi true; then
  echo "[ok] Billing is enabled."
else
  echo "[!] Billing does NOT appear to be enabled on $PROJECT_ID."
  echo "    Enable it at https://console.cloud.google.com/billing then re-run."
  if [[ "${ASSUME_YES:-}" == "1" ]]; then
    echo "    (continuing anyway; ASSUME_YES=1)"
  else
    read -r -p "Continue anyway? [y/N] " c
    [[ "$c" == "y" || "$c" == "Y" ]] || exit 1
  fi
fi

# --- 1. Enable APIs ---------------------------------------------------------
echo "[1/7] Enabling APIs (this can take a minute)..."
gcloud services enable \
  cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com secretmanager.googleapis.com storage.googleapis.com

# --- 2. State bucket --------------------------------------------------------
echo "[2/7] Ensuring state bucket gs://$BUCKET ..."
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION"
else
  echo "      bucket already exists."
fi

# --- 3. Secret (Gmail App Password) -----------------------------------------
echo "[3/7] Storing Gmail App Password in Secret Manager..."
if [[ -z "${GMAIL_APP_PASSWORD:-}" ]]; then
  echo "      no new password entered; keeping the stored secret."
elif $SECRET_EXISTS; then
  printf '%s' "$GMAIL_APP_PASSWORD" | \
    gcloud secrets versions add gmail-app-password --data-file=-
else
  printf '%s' "$GMAIL_APP_PASSWORD" | \
    gcloud secrets create gmail-app-password --data-file=-
fi
unset GMAIL_APP_PASSWORD

# --- 4. IAM: let the function's service account read the secret -------------
# This MUST happen before deploy, or the new revision can't start.
echo "[4/7] Granting the function access to the secret..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding gmail-app-password \
  --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null

# --- 5. Deploy the function -------------------------------------------------
# Skip the (slow) deploy if the function is already live, unless FORCE_DEPLOY=1.
if [[ "${FORCE_DEPLOY:-}" != "1" ]] && \
   gcloud functions describe disney-ride-alerts --region="$REGION" --gen2 \
     --format='value(state)' 2>/dev/null | grep -q ACTIVE; then
  echo "[5/7] Function already ACTIVE; skipping redeploy (set FORCE_DEPLOY=1 to force)."
else
  echo "[5/7] Deploying Cloud Function (takes 1-3 minutes)..."
  gcloud functions deploy disney-ride-alerts \
    --gen2 --runtime=python311 --region="$REGION" \
    --source=. --entry-point=check_rides \
    --trigger-http --no-allow-unauthenticated \
    --set-env-vars="STATE_BUCKET=${BUCKET},GMAIL_ADDRESS=${GMAIL_ADDRESS},ALERT_RECIPIENT=${ALERT_RECIPIENT},WAIT_THRESHOLD=${WAIT_THRESHOLD}" \
    --set-secrets="GMAIL_APP_PASSWORD=gmail-app-password:latest"
fi

# --- 6. IAM: bucket access for the function (used at runtime) ---------------
echo "[6/7] Granting the function access to the state bucket..."
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/storage.objectAdmin" >/dev/null

# --- 7. Scheduler job -------------------------------------------------------
echo "[7/7] Creating the Cloud Scheduler poll job..."
SCHED_SA="disney-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$SCHED_SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create disney-scheduler \
    --display-name="Disney alerts scheduler"
fi

# A freshly created service account takes a few seconds to propagate through
# IAM; wait until it is resolvable before granting it roles.
echo "      waiting for the scheduler service account to be ready..."
for _ in $(seq 1 18); do
  gcloud iam service-accounts describe "$SCHED_SA" >/dev/null 2>&1 && break
  sleep 5
done

FUNC_URL=$(gcloud functions describe disney-ride-alerts \
  --region="$REGION" --gen2 --format='value(serviceConfig.uri)')

# Allow the scheduler SA to invoke the function, retrying while IAM catches up.
for attempt in $(seq 1 18); do
  if gcloud run services add-iam-policy-binding disney-ride-alerts \
       --region="$REGION" \
       --member="serviceAccount:${SCHED_SA}" \
       --role="roles/run.invoker" >/dev/null 2>&1; then
    break
  fi
  echo "      service account still propagating; retrying ($attempt/18)..."
  sleep 5
done

if gcloud scheduler jobs describe disney-alerts-poll --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http disney-alerts-poll --location="$REGION" \
    --schedule="$SCHEDULE" --time-zone="$TIMEZONE" \
    --uri="$FUNC_URL" --http-method=GET \
    --oidc-service-account-email="$SCHED_SA" --oidc-token-audience="$FUNC_URL"
else
  gcloud scheduler jobs create http disney-alerts-poll --location="$REGION" \
    --schedule="$SCHEDULE" --time-zone="$TIMEZONE" \
    --uri="$FUNC_URL" --http-method=GET \
    --oidc-service-account-email="$SCHED_SA" --oidc-token-audience="$FUNC_URL"
fi

echo
echo "=== Done! ==="
echo "Function URL: $FUNC_URL"
echo
echo "Test it now with:"
echo "  gcloud scheduler jobs run disney-alerts-poll --location=$REGION"
echo "  gcloud functions logs read disney-ride-alerts --region=$REGION --gen2 --limit=20"
