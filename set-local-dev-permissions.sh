#!/usr/bin/env bash
# Helper script to grant local development IAM permissions for local testing and playground.
# Usage: bash ./set-local-dev-permissions.sh [--project <project-id>] [--project-number <project-number>]

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

COMPUTE_SERVICE_ACCOUNT="${COMPUTE_SERVICE_ACCOUNT:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

echo "🛡️ Granting local development IAM roles on project: $PROJECT"
echo "  • Compute Service Account: $COMPUTE_SERVICE_ACCOUNT"
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

echo "1) Compute Service Account ($COMPUTE_SERVICE_ACCOUNT):"
grant_role "$COMPUTE_SERVICE_ACCOUNT" "roles/aiplatform.user"
grant_role "$COMPUTE_SERVICE_ACCOUNT" "roles/storage.objectAdmin"
grant_role "$COMPUTE_SERVICE_ACCOUNT" "roles/iam.serviceAccountTokenCreator"

echo ""
echo "✅ All required local development IAM permissions granted successfully!"
