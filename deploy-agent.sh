#!/usr/bin/env bash
# Deploy Omni-Agent to Gemini Enterprise infrastructure explicitly.

set -e

# Source environment variables if .env exists
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

PROJECT="${GOOGLE_CLOUD_PROJECT}"
INSTANCE="${GEMINI_ENTERPRISE_INSTANCE}"
ARTIFACTS_BUCKET="${GCS_BUCKET_NAME:-geapp_agents_storage}" # Default, can be overridden via args

# Check if arguments provided
while [[ $# -gt 0 ]]; do
  case $1 in
    --bucket)
      ARTIFACTS_BUCKET="$2"
      shift 2
      ;;
    *)
      # Unrecognized param, let it pass to agents-cli
      break
      ;;
  esac
done

echo "🚀 Deploying Omni-Agent to project: $PROJECT"
echo "🏢 Targeting Gemini Enterprise Instance: $INSTANCE"
echo "🪣  Using Artifacts Bucket: $ARTIFACTS_BUCKET"

# Ensure bucket exists
if ! gcloud storage ls "gs://$ARTIFACTS_BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
    echo "Bucket gs://$ARTIFACTS_BUCKET does not exist or is inaccessible. Creating..."
    gcloud storage buckets create "gs://$ARTIFACTS_BUCKET" --project "$PROJECT" || true
else
    echo "Bucket gs://$ARTIFACTS_BUCKET already exists."
fi

# We must update .env so agents-cli injects it to the deployed reasoning engine!
sed -i "s/^LOGS_BUCKET_NAME=.*/LOGS_BUCKET_NAME=$ARTIFACTS_BUCKET/" .env
sed -i "s/^GCS_BUCKET_NAME=.*/GCS_BUCKET_NAME=$ARTIFACTS_BUCKET/" .env

# Pass the project explicitly to the agents-cli deployments.
agents-cli deploy --project "$PROJECT" --no-confirm-project "$@"

# Publish to the targeted Gemini Enterprise App Instance to make the agent visible in the environment.
APP_ID="${GEMINI_ENTERPRISE_APP_ID:-projects/663586531150/locations/global/collections/default_collection/engines/$INSTANCE}"
echo "🔗 Publishing Agent to Gemini Enterprise..."
agents-cli publish gemini-enterprise --project "$PROJECT" --gemini-enterprise-app-id "$APP_ID"
