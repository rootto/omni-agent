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

"""Externalized configuration module enforcing Zero Hardcoding."""

import os
from pathlib import Path

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def get_google_cloud_project() -> str:
    """Returns the configured Google Cloud Project ID."""
    return os.getenv("GOOGLE_CLOUD_PROJECT", "marketing-agent-poc-500621")


def get_google_cloud_region() -> str:
    """Returns the configured Google Cloud Region (defaults to global)."""
    return os.getenv("GOOGLE_CLOUD_REGION", os.getenv("GOOGLE_CLOUD_LOCATION", "global"))


def get_agent_model_id() -> str:
    """Returns the primary reasoning model ID for the orchestration agent."""
    return os.getenv("AGENT_MODEL_ID", "gemini-3.5-flash")


def get_omni_model_id() -> str:
    """Returns the Gemini Omni model ID for video generation and editing."""
    return os.getenv("OMNI_MODEL_ID", "gemini-omni-flash-preview")


def get_gcs_bucket_name() -> str:
    """Returns the GCS bucket name for video artifact storage."""
    # Prioritize GCS_BUCKET_NAME, fallback to LOGS_BUCKET_NAME (commonly injected in deployed runtimes)
    return os.getenv(
        "GCS_BUCKET_NAME",
        os.getenv("LOGS_BUCKET_NAME", "geapp_agents_storage"),
    )
