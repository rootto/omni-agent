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

"""Deployment script to deploy Omni-Agent to Vertex AI Reasoning Engine / Gemini Enterprise Agent Engine."""

import sys
import logging
import vertexai
from vertexai.preview import reasoning_engines
from app.agent import root_agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = "omini-test-agent"
LOCATION = "us-central1"
STAGING_BUCKET = "gs://test-omini-bucket123"

def main():
    logger.info("Initializing Vertex AI with project=%s, location=%s, staging_bucket=%s", PROJECT_ID, LOCATION, STAGING_BUCKET)
    vertexai.init(
        project=PROJECT_ID,
        location=LOCATION,
        staging_bucket=STAGING_BUCKET,
    )

    logger.info("Packaging ADK root_agent into AdkApp...")
    adk_app = reasoning_engines.AdkApp(agent=root_agent)

    logger.info("Deploying Reasoning Engine / Agent Engine instance...")
    remote_app = reasoning_engines.ReasoningEngine.create(
        adk_app,
        display_name="omni-agent",
        description="Gemini Enterprise Video Creation & Editing Agent (Omni-Agent)",
        requirements=[
            "google-adk[gcp]>=2.0.0",
            "google-genai>=0.1.0",
            "google-cloud-storage>=2.14.0",
            "google-cloud-logging>=3.12.0",
            "gcsfs>=2024.11.0",
            "aiohttp>=3.13.4",
        ],
        extra_packages=["app"],
    )

    logger.info("Deployment successful!")
    logger.info("Resource Name: %s", remote_app.resource_name)
    logger.info("Reasoning Engine ID: %s", remote_app.name)
    print(f"\nSUCCESS: Deployed Reasoning Engine to {remote_app.resource_name}")

if __name__ == "__main__":
    main()
