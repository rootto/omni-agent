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
PROJECT_NUMBER="${PROJECT_NUMBER:-$GOOGLE_CLOUD_PROJECT_NUMBER}"
GEMINI_APP_LOCATION="${GEMINI_APP_LOCATION:-global}"
AGENT_REGION="${AGENT_REGION:-us-central1}"
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

echo "🚀 Deploying Omni-Agent to project: $PROJECT (region: $AGENT_REGION)"
echo "🏢 Targeting Gemini Enterprise Instance: $INSTANCE"
echo "🪣  Using Artifacts Bucket: $ARTIFACTS_BUCKET"

# Ensure bucket exists
if ! gcloud storage ls "gs://$ARTIFACTS_BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
    echo "Bucket gs://$ARTIFACTS_BUCKET does not exist or is inaccessible. Creating..."
    gcloud storage buckets create "gs://$ARTIFACTS_BUCKET" --project "$PROJECT" || true
else
    echo "Bucket gs://$ARTIFACTS_BUCKET already exists."
fi

# Ensure ffmpeg & ffprobe static Linux binaries exist in the target GCS bucket for runtime video merging
echo "📦 Verifying static ffmpeg & ffprobe binaries in gs://$ARTIFACTS_BUCKET/bin/..."
if ! gcloud storage ls "gs://$ARTIFACTS_BUCKET/bin/ffmpeg" --project "$PROJECT" >/dev/null 2>&1 || ! gcloud storage ls "gs://$ARTIFACTS_BUCKET/bin/ffprobe" --project "$PROJECT" >/dev/null 2>&1; then
    echo "Static ffmpeg/ffprobe binaries missing in gs://$ARTIFACTS_BUCKET/bin/. Downloading and uploading..."
    TMP_BIN_DIR=$(mktemp -d)
    if curl -sL "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" -o "$TMP_BIN_DIR/ffmpeg.tar.xz" 2>/dev/null && [ -s "$TMP_BIN_DIR/ffmpeg.tar.xz" ]; then
        tar -xJf "$TMP_BIN_DIR/ffmpeg.tar.xz" -C "$TMP_BIN_DIR" --strip-components=1 2>/dev/null || true
    fi
    if [ ! -f "$TMP_BIN_DIR/ffmpeg" ] || [ ! -f "$TMP_BIN_DIR/ffprobe" ]; then
        echo "Downloading individual static binaries..."
        curl -sL "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0/ffmpeg-linux-x64" -o "$TMP_BIN_DIR/ffmpeg" 2>/dev/null || true
        curl -sL "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0/ffprobe-linux-x64" -o "$TMP_BIN_DIR/ffprobe" 2>/dev/null || true
    fi
    if [ -f "$TMP_BIN_DIR/ffmpeg" ] && [ -f "$TMP_BIN_DIR/ffprobe" ]; then
        chmod +x "$TMP_BIN_DIR/ffmpeg" "$TMP_BIN_DIR/ffprobe"
        gcloud storage cp "$TMP_BIN_DIR/ffmpeg" "gs://$ARTIFACTS_BUCKET/bin/ffmpeg" --project "$PROJECT"
        gcloud storage cp "$TMP_BIN_DIR/ffprobe" "gs://$ARTIFACTS_BUCKET/bin/ffprobe" --project "$PROJECT"
        echo "✅ Uploaded ffmpeg and ffprobe to gs://$ARTIFACTS_BUCKET/bin/"
    else
        echo "⚠️ Could not download static ffmpeg/ffprobe binaries automatically. Please ensure gs://$ARTIFACTS_BUCKET/bin/ffmpeg exists."
    fi
    rm -rf "$TMP_BIN_DIR"
else
    echo "✅ Static ffmpeg & ffprobe binaries already present in gs://$ARTIFACTS_BUCKET/bin/"
fi

# We must update .env so agents-cli injects it to the deployed reasoning engine!
sed -i "s/^LOGS_BUCKET_NAME=.*/LOGS_BUCKET_NAME=$ARTIFACTS_BUCKET/" .env
sed -i "s/^GCS_BUCKET_NAME=.*/GCS_BUCKET_NAME=$ARTIFACTS_BUCKET/" .env

echo "🛡️ Verifying required Cloud APIs..."
gcloud services enable \
    cloudresourcemanager.googleapis.com \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com \
    storage.googleapis.com \
    discoveryengine.googleapis.com \
    iam.googleapis.com \
    --project "$PROJECT"

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
CLOUDBUILD_SERVICE_ACCOUNT="${GOOGLE_CLOUD_PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

echo "🛡️ Granting deployment IAM roles to Reasoning Engine and Cloud Build service accounts..."
grant_role_or_warn() {
    local member="$1"
    local role="$2"
    if ! gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$member" --role="$role" --condition=None > /dev/null 2>&1; then
        echo "⚠️  Could not grant $role to $member (insufficient Project IAM Admin permission)."
        echo "    If deployment fails, ask an IAM Admin to run: bash ./set-iam-permissions.sh"
    fi
}

grant_role_or_warn "$AGENT_RUNTIME_SERVICE_ACCOUNT" "roles/aiplatform.user"
grant_role_or_warn "$AGENT_RUNTIME_SERVICE_ACCOUNT" "roles/storage.objectAdmin"
grant_role_or_warn "$AGENT_RUNTIME_SERVICE_ACCOUNT" "roles/iam.serviceAccountTokenCreator"
grant_role_or_warn "$CLOUDBUILD_SERVICE_ACCOUNT" "roles/storage.objectAdmin"
grant_role_or_warn "$CLOUDBUILD_SERVICE_ACCOUNT" "roles/logging.logWriter"

# Allow Reasoning Engine service account to sign blobs using the default Compute Engine service account
if ! gcloud iam service-accounts add-iam-policy-binding "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" --member="serviceAccount:$AGENT_RUNTIME_SERVICE_ACCOUNT" --role="roles/iam.serviceAccountTokenCreator" --project="$PROJECT" --condition=None > /dev/null 2>&1; then
    echo "⚠️  Could not grant roles/iam.serviceAccountTokenCreator on ${PROJECT_NUMBER}-compute@developer.gserviceaccount.com to $AGENT_RUNTIME_SERVICE_ACCOUNT."
    echo "    If deployment fails to sign V4 Signed URLs, ask an IAM Admin to run: bash ./set-iam-permissions.sh"
fi

# Pass the project and region explicitly to the agents-cli deployments.
agents-cli deploy --project "$PROJECT" --region "$AGENT_REGION" --no-confirm-project "$@"

# Publish to the targeted Gemini Enterprise App Instance to make the agent visible in the environment.
APP_ID="projects/${PROJECT_NUMBER}/locations/${GEMINI_APP_LOCATION}/collections/default_collection/engines/${INSTANCE}"
echo "🔗 Publishing Agent to Gemini Enterprise..."
agents-cli publish gemini-enterprise --project "$PROJECT" --gemini-enterprise-app-id "$APP_ID"
