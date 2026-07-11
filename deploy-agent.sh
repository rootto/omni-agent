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

echo "🛡️ Verifying required Cloud APIs..."
gcloud services enable cloudresourcemanager.googleapis.com --project "$PROJECT"

if [ -z "$GOOGLE_CLOUD_PROJECT_NUMBER" ]; then
    read -p "Enter your Google Cloud Project Number (numeric ID): " GOOGLE_CLOUD_PROJECT_NUMBER
    if [ -f .env ]; then
        if grep -q "^GOOGLE_CLOUD_PROJECT_NUMBER=" .env; then
            sed -i "s/^GOOGLE_CLOUD_PROJECT_NUMBER=.*/GOOGLE_CLOUD_PROJECT_NUMBER=$GOOGLE_CLOUD_PROJECT_NUMBER/" .env
        else
            echo "GOOGLE_CLOUD_PROJECT_NUMBER=$GOOGLE_CLOUD_PROJECT_NUMBER" >> .env
        fi
    fi
fi

AGENT_RUNTIME_SERVICE_ACCOUNT="${AGENT_RUNTIME_SERVICE_ACCOUNT:-service-${GOOGLE_CLOUD_PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com}"
COMPUTE_SERVICE_ACCOUNT="${COMPUTE_SERVICE_ACCOUNT:-${GOOGLE_CLOUD_PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

echo "🛡️ Granting roles/aiplatform.user and roles/storage.objectAdmin to service accounts..."
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$AGENT_RUNTIME_SERVICE_ACCOUNT" --role="roles/aiplatform.user" --condition=None > /dev/null 2>&1 || true
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$COMPUTE_SERVICE_ACCOUNT" --role="roles/aiplatform.user" --condition=None > /dev/null 2>&1 || true
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$AGENT_RUNTIME_SERVICE_ACCOUNT" --role="roles/storage.objectAdmin" --condition=None > /dev/null 2>&1 || true
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$COMPUTE_SERVICE_ACCOUNT" --role="roles/storage.objectAdmin" --condition=None > /dev/null 2>&1 || true

# Pass the project explicitly to the agents-cli deployments.
agents-cli deploy --project "$PROJECT" --no-confirm-project "$@"

# Publish to the targeted Gemini Enterprise App Instance to make the agent visible in the environment.
APP_ID="${GEMINI_ENTERPRISE_APP_ID:-projects/${GOOGLE_CLOUD_PROJECT_NUMBER}/locations/global/collections/default_collection/engines/$INSTANCE}"
echo "🔗 Publishing Agent to Gemini Enterprise..."
agents-cli publish gemini-enterprise --project "$PROJECT" --gemini-enterprise-app-id "$APP_ID"
