#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-omini-test-agent}"
PROJECT_NUMBER="${PROJECT_NUMBER:-687484203981}"
REGION="${REGION:-us-central1}"
APP_ID="${APP_ID:-gemini-enterprise-17842095_1784209567197}"
SERVICE_NAME="${SERVICE_NAME:-omni-agent}"

# Locate agents-cli binary
AGENTS_CLI="${AGENTS_CLI:-agents-cli}"
if ! command -v "$AGENTS_CLI" &> /dev/null; then
    if [ -f "$HOME/.local/bin/agents-cli" ]; then
        AGENTS_CLI="$HOME/.local/bin/agents-cli"
    else
        AGENTS_CLI="uv run agents-cli"
    fi
fi

clean_deployments() {
    echo "========================================================"
    echo " CLEANING EXISTING DEPLOYMENTS & REGISTRATIONS"
    echo "========================================================"

    # 1. Clean Gemini Enterprise Agent Registry services
    echo "Checking Gemini Enterprise Agent Registry..."
    local agent_services
    agent_services=$(gcloud alpha agent-registry agents list --project="$PROJECT_ID" --location="$REGION" --format="value(name)" 2>/dev/null || true)

    if [ -n "$agent_services" ]; then
        for svc in $agent_services; do
            if [[ "$svc" == *"agentregistry-00000000-0000-0000-7d73"* ]]; then
                echo "Deleting registered Agent Registry service: $svc"
                gcloud alpha agent-registry services delete "$svc" --quiet 2>/dev/null || true
            fi
        done
    else
        echo "No registered services found in Agent Registry."
    fi

    # 2. Clean Vertex AI Reasoning Engines (Agent Platform)
    echo "Checking Vertex AI Reasoning Engines on Agent Platform..."
    local token
    token=$(gcloud auth print-access-token)
    local re_list
    re_list=$(curl -s -H "Authorization: Bearer $token" \
        "https://${REGION}-aiplatform.googleapis.com/v1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines" \
        | python3 -c 'import sys, json; data=json.load(sys.stdin); print("\n".join([re["name"] for re in data.get("reasoningEngines", []) if re.get("displayName") == "'"$SERVICE_NAME"'"]))' 2>/dev/null || true)

    if [ -n "$re_list" ]; then
        for re in $re_list; do
            if [ -n "$re" ]; then
                echo "Deleting Reasoning Engine: $re"
                curl -s -X DELETE -H "Authorization: Bearer $token" \
                    "https://${REGION}-aiplatform.googleapis.com/v1/${re}?force=true" > /dev/null
            fi
        done
        echo "Reasoning Engine deletion requests submitted."
    else
        echo "No existing Reasoning Engines found matching '$SERVICE_NAME'."
    fi

    # 3. Clean local deployment metadata
    if [ -f "deployment_metadata.json" ]; then
        echo "Removing local deployment_metadata.json..."
        rm -f deployment_metadata.json
    fi

    echo "Cleanup complete!"
}

deploy_agent() {
    echo "========================================================"
    echo " DEPLOYING AGENT TO AGENT RUNTIME & GEMINI ENTERPRISE"
    echo "========================================================"

    echo "Deploying via agents-cli..."
    "$AGENTS_CLI" deploy \
        --deployment-target agent_runtime \
        --project "$PROJECT_ID" \
        --region "$REGION" \
        --service-name "$SERVICE_NAME" \
        --no-confirm-project

    echo "Publishing to Gemini Enterprise via agents-cli..."
    local ge_app_id="projects/${PROJECT_NUMBER}/locations/global/collections/default_collection/engines/${APP_ID}"

    "$AGENTS_CLI" publish gemini-enterprise \
        --gemini-enterprise-app-id "$ge_app_id" \
        --display-name "$SERVICE_NAME" \
        --description "Gemini Enterprise Video Creation & Editing Agent (Omni-Agent)" \
        --project "$PROJECT_ID" \
        --project-number "$PROJECT_NUMBER" \
        --registration-type adk

    echo "Deployment and registration completed successfully!"
}

ACTION="${1:-}"

case "$ACTION" in
    clean|--clean)
        clean_deployments
        ;;
    deploy|--deploy)
        deploy_agent
        ;;
    clean-and-deploy|--clean-and-deploy|all)
        clean_deployments
        deploy_agent
        ;;
    *)
        echo "Usage: $0 {clean|deploy|clean-and-deploy}"
        echo "  clean             Deletes existing deployments in Agent Platform and Gemini Enterprise"
        echo "  deploy            Deploys and registers the agent using agents-cli"
        echo "  clean-and-deploy  Performs cleanup followed by a fresh deployment"
        exit 1
        ;;
esac
