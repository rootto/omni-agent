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

"""Publish/register deployed Reasoning Engine to Gemini Enterprise Agent Space."""

import json
import logging
import google.auth
import google.auth.transport.requests
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_NUMBER = "687484203981"
APP_ID = "gemini-enterprise-1784209567197"
REASONING_ENGINE_RESOURCE = "projects/687484203981/locations/us-central1/reasoningEngines/3078176810203086848"
DISPLAY_NAME = "omni-agent"
DESCRIPTION = "Gemini Enterprise Video Creation & Editing Agent (Omni-Agent)"

def get_auth_token():
    credentials, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token

def publish_to_enterprise():
    token = get_auth_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-goog-user-project": "omini-test-agent",
    }
    
    # 1. List Engines to locate the exact engine resource
    engines_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{PROJECT_NUMBER}/locations/global/collections/default_collection/engines"
    logger.info("Listing engines in project %s...", PROJECT_NUMBER)
    resp = requests.get(engines_url, headers=headers)
    logger.info("List engines response [%d]: %s", resp.status_code, resp.text[:500])

    # 2. Try direct agent creation/update under the engine or project location
    target_engine_id = APP_ID
    if resp.status_code == 200:
        engines = resp.json().get("engines", [])
        for e in engines:
            name = e.get("name", "")
            if APP_ID in name or "gemini-enterprise" in name:
                logger.info("Found matching engine: %s", name)
                target_engine_id = name.split("/")[-1]
                break

    agents_url = f"https://discoveryengine.googleapis.com/v1alpha/projects/{PROJECT_NUMBER}/locations/global/collections/default_collection/engines/{target_engine_id}/agents"
    payload = {
        "displayName": DISPLAY_NAME,
        "description": DESCRIPTION,
        "adkAgentDefinition": {
            "provisionedReasoningEngine": {
                "reasoningEngine": REASONING_ENGINE_RESOURCE
            }
        }
    }

    logger.info("Posting agent to %s...", agents_url)
    post_resp = requests.post(agents_url, headers=headers, json=payload)
    logger.info("Post response [%d]: %s", post_resp.status_code, post_resp.text)
    
    if post_resp.status_code in (200, 201):
        print(f"\nSUCCESS: Registered agent in Gemini Enterprise: {post_resp.json().get('name')}")
    else:
        print(f"\nFailed to create agent: {post_resp.text}")

if __name__ == "__main__":
    publish_to_enterprise()
