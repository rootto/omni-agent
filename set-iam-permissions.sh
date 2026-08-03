#!/usr/bin/env bash
# Helper script for an out-of-band Project IAM Admin to grant required deployment IAM permissions.
# Usage: bash ./set-iam-permissions.sh [--project <project-id>] [--project-number <project-number>]

set -e

# Source environment variables if .env exists
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

PROJECT="${GOOGLE_CLOUD_PROJECT}"
PROJECT_NUMBER="${PROJECT_NUMBER:-$GOOGLE_CLOUD_PROJECT_NUMBER}"

while [[ $# -gt 0 ]]; do
  case $1 in
    --project)
      PROJECT="$2"
      shift 2
      ;;
    --project-number)
      PROJECT_NUMBER="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [ -z "$PROJECT" ]; then
  read -p "Enter Google Cloud Project ID: " PROJECT
fi

if [ -z "$PROJECT_NUMBER" ]; then
  read -p "Enter Google Cloud Project Number (numeric ID): " PROJECT_NUMBER
fi

AGENT_RUNTIME_SERVICE_ACCOUNT="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
CLOUDBUILD_SERVICE_ACCOUNT="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

echo "🛡️ Granting deployment IAM roles on project: $PROJECT"
echo "  • Runtime Service Account: $AGENT_RUNTIME_SERVICE_ACCOUNT"
echo "  • Cloud Build Service Account: $CLOUDBUILD_SERVICE_ACCOUNT"
echo ""

grant_role() {
  local member="$1"
  local role="$2"
  echo "  -> Granting $role to $member..."
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$member" \
    --role="$role" \
    --condition=None >/dev/null
}

echo "1) Vertex AI Reasoning Engine Runtime SA ($AGENT_RUNTIME_SERVICE_ACCOUNT):"
grant_role "$AGENT_RUNTIME_SERVICE_ACCOUNT" "roles/aiplatform.user"
grant_role "$AGENT_RUNTIME_SERVICE_ACCOUNT" "roles/storage.objectAdmin"
grant_role "$AGENT_RUNTIME_SERVICE_ACCOUNT" "roles/iam.serviceAccountTokenCreator"

echo ""
echo "2) Cloud Build Service Account ($CLOUDBUILD_SERVICE_ACCOUNT):"
grant_role "$CLOUDBUILD_SERVICE_ACCOUNT" "roles/storage.objectAdmin"
grant_role "$CLOUDBUILD_SERVICE_ACCOUNT" "roles/logging.logWriter"

echo ""
echo "3) Cross-Service Account Signing Binding:"
echo "  -> Allowing $AGENT_RUNTIME_SERVICE_ACCOUNT to sign blobs using ${PROJECT_NUMBER}-compute@developer.gserviceaccount.com..."
gcloud iam service-accounts add-iam-policy-binding "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --member="serviceAccount:$AGENT_RUNTIME_SERVICE_ACCOUNT" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --project="$PROJECT" \
    --condition=None >/dev/null 2>&1 || true

echo ""
echo "✅ All required deployment IAM permissions granted successfully!"
