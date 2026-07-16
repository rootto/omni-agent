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

"""End-to-End multi-turn verification test suite for Critical User Journeys (TEST_SPEC.md)."""
import os
import pytest
import re
import uuid
from pathlib import Path
from unittest.mock import MagicMock
from google.cloud import storage
from google.genai import types

from app import config
from app.tools.video_generation_tool import video_generation_tool


class DummySession:
    def __init__(self):
        self.state = {}


class DummyToolContext:
    def __init__(self, bucket_name: str):
        self.session = DummySession()
        self.state = {}
        self.bucket_name = bucket_name
        self.uploaded_uris = {}
        self.artifact_metadata = {}

    async def save_artifact(self, filename: str, artifact) -> int:
        version = 1
        data = artifact.inline_data.data
        
        mime_type = "video/mp4"
        if hasattr(artifact, "inline_data") and getattr(artifact.inline_data, "mime_type", None):
            mime_type = artifact.inline_data.mime_type
            
        upload_uuid = uuid.uuid4().hex[:12]
        object_name = f"artifacts/{upload_uuid}_{filename}"
        
        storage_client = storage.Client()
        bucket = storage_client.bucket(self.bucket_name)
        blob = bucket.blob(object_name)
        blob.upload_from_string(data, content_type=mime_type)
        
        self.uploaded_uris[filename] = f"gs://{self.bucket_name}/{object_name}"
        
        mock_meta = MagicMock()
        mock_meta.mime_type = mime_type
        self.artifact_metadata[filename] = mock_meta
        
        return version

    async def get_artifact_version(self, filename: str, version: int = None):
        gs_uri = self.uploaded_uris.get(filename)
        if not gs_uri:
            gs_uri = self.session.state.get("file_data_mappings", {}).get(filename)
        if not gs_uri:
            gs_uri = f"gs://{self.bucket_name}/artifacts/fallback_{filename}"
            
        mock_version = MagicMock()
        mock_version.canonical_uri = gs_uri
        return mock_version

    async def load_artifact(self, filename: str):
        return self.artifact_metadata.get(filename)


@pytest.mark.asyncio
@pytest.mark.timeout(900)
async def test_e2e_cuj1_text_to_video_16_9() -> None:
    """Verifies CUJ-1 Text-to-Video generation (16:9 landscape) by hitting the real Gemini Omni Interactions API.
    Asserts that the tool generates a real, valid MP4 video string and dual-link output.
    """
    bucket_name = config.get_gcs_bucket_name()
    tool_context = DummyToolContext(bucket_name)

    output = await video_generation_tool(
        prompt="Create a 16:9 cinematic drone shot flying through a mist-covered pine forest at sunrise, golden light filtering through trees, photorealistic.",
        task="text_to_video",
        aspect_ratio="16:9",
        tool_context=tool_context,
    )

    # 1. Assert tool invocation & parameters did not return an error
    assert "Error:" not in output, f"Real API video generation failed: {output}"

    # 2. Assert interaction.id stored directly in ADK Session Service state
    assert tool_context.session.state.get("previous_interaction_id") is not None
    assert tool_context.session.state["previous_interaction_id"] != ""

    # 3. Assert dual-link format returned
    assert "![generated_video.mp4](gs://" in output
    assert "https://storage." in output

    # 4. Assert that the generated file from the web URL is a VALID, REAL MP4
    import requests
    
    match = re.search(r"Download Video: (https://\S+)", output)
    assert match is not None, "Could not find HTTPS Download Video link in output"
    https_url = match.group(1).rstrip('.')
    
    print(f"Downloading from: {https_url}")
    response = requests.get(https_url)
    assert response.status_code == 200, f"Failed to download video from web client URL: {response.status_code}"
    
    video_bytes = response.content
    size = len(video_bytes)
    assert size > 50000, f"Generated video size is too small ({size} bytes). Likely a dummy mock or failed generation!"

    # Verify the first few bytes are an MP4 header (e.g. ftyp)
    header = video_bytes[:16]
    assert b"ftyp" in header or b"mp4" in header or b"isom" in header, f"Invalid MP4 signature in fetched payload (started with {header!r}). Could be a redirect or error page."


@pytest.mark.asyncio
@pytest.mark.timeout(900)
async def test_e2e_cuj2_image_to_video() -> None:
    """Verifies CUJ-2 Image-to-Video animation by uploading a local image,
    resolving its GCS URI in the session state, and calling the Omni Interactions API.
    """
    # 1. Ensure local fixture exists
    project_root = Path(__file__).resolve().parent.parent.parent
    local_img_path = project_root / "tests" / "fixtures" / "sample_image.png"
    assert local_img_path.exists(), f"Local fixture image not found at {local_img_path}. Run setup_test_fixtures.py first."

    # 2. Upload image to GCS uploads/ folder to simulate ADK / FileDataResolverPlugin upload
    bucket_name = config.get_gcs_bucket_name()
    upload_uuid = uuid.uuid4().hex[:8]
    object_name = f"uploads/{upload_uuid}_sample_image.png"
    
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    
    img_bytes = local_img_path.read_bytes()
    blob.upload_from_string(img_bytes, content_type="image/png")
    
    gs_uri = f"gs://{bucket_name}/{object_name}"
    print(f"Uploaded test fixture to: {gs_uri}")

    # 3. Setup DummyToolContext and populate file_data_mappings
    tool_context = DummyToolContext(bucket_name)
    # Map the extension-less artifact key to the GCS URI
    tool_context.session.state["file_data_mappings"] = {
        "artifact_cuj2_image": gs_uri
    }
    
    # Set the real types.Part metadata loaded by load_artifact
    mock_part = types.Part(
        inline_data=types.Blob(
            mime_type="image/png",
            data=b""
        )
    )
    tool_context.artifact_metadata["artifact_cuj2_image"] = mock_part

    # 4. Call video_generation_tool
    output = await video_generation_tool(
        prompt="Animate this image: slowly push the camera forward over the ridge while subtle clouds drift across the peaks.",
        task="image_to_video",
        aspect_ratio="16:9",
        file_uris=["artifact_cuj2_image"],
        tool_context=tool_context,
    )

    # 5. Assertions
    assert "Error:" not in output, f"Real API image animation failed: {output}"
    assert tool_context.session.state.get("previous_interaction_id") is not None
    assert tool_context.session.state["previous_interaction_id"] != ""

    assert "![generated_video.mp4](gs://" in output
    assert "https://storage." in output

    # 6. Verify the generated file is a valid, real MP4
    import requests
    
    match = re.search(r"Download Video: (https://\S+)", output)
    assert match is not None, "Could not find HTTPS Download Video link in output"
    https_url = match.group(1).rstrip('.')
    
    print(f"Downloading from: {https_url}")
    response = requests.get(https_url)
    assert response.status_code == 200, f"Failed to download video from web client URL: {response.status_code}"
    
    video_bytes = response.content
    size = len(video_bytes)
    assert size > 50000, f"Generated video size is too small ({size} bytes)."

    header = video_bytes[:16]
    assert b"ftyp" in header or b"mp4" in header or b"isom" in header, f"Invalid MP4 signature (started with {header!r})."


@pytest.mark.asyncio
@pytest.mark.timeout(1800)
async def test_e2e_cuj3_stateful_edit() -> None:
    """Verifies CUJ-3 stateful iterative editing by generating a video,
    then editing it in a second turn, verifying steps accumulation in session state.
    """
    bucket_name = config.get_gcs_bucket_name()
    tool_context = DummyToolContext(bucket_name)

    # Turn 1: Generate initial video
    output1 = await video_generation_tool(
        prompt="Create a 5-second 16:9 cinematic video of a red car driving on a coastal road at sunset.",
        task="text_to_video",
        aspect_ratio="16:9",
        tool_context=tool_context,
    )
    assert "Error:" not in output1
    
    # Capture initial interaction state
    interaction_id_1 = tool_context.session.state.get("previous_interaction_id")
    steps_1 = tool_context.session.state.get("previous_interaction_steps")
    assert interaction_id_1 is not None
    assert steps_1 is not None
    assert len(steps_1) > 0

    # Turn 2: Edit the video (Stateful Edit)
    output2 = await video_generation_tool(
        prompt="Make the car blue and speed up the video.",
        task="edit",
        aspect_ratio="16:9",
        tool_context=tool_context,
    )
    assert "Error:" not in output2

    # Capture turn 2 interaction state
    interaction_id_2 = tool_context.session.state.get("previous_interaction_id")
    steps_2 = tool_context.session.state.get("previous_interaction_steps")
    
    assert interaction_id_2 is not None
    assert interaction_id_2 != interaction_id_1
    assert steps_2 is not None
    
    # Assert steps accumulated
    assert len(steps_2) > len(steps_1)
    
    assert "![generated_video.mp4](gs://" in output2
    assert "https://storage." in output2



@pytest.mark.asyncio
@pytest.mark.timeout(1200)
async def test_e2e_cuj4_uploaded_video_edit_standard() -> None:
    """Verifies CUJ-4 by uploading a standard 5s video and editing it (no splitting)."""
    # 1. Ensure local fixture exists
    project_root = Path(__file__).resolve().parent.parent.parent
    local_video_path = project_root / "tests" / "fixtures" / "sample_video.mp4"
    assert local_video_path.exists(), f"Local fixture video not found at {local_video_path}"

    # 2. Upload to GCS to simulate ADK upload
    bucket_name = config.get_gcs_bucket_name()
    upload_uuid = uuid.uuid4().hex[:8]
    object_name = f"uploads/{upload_uuid}_sample_video.mp4"
    
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    
    video_bytes = local_video_path.read_bytes()
    blob.upload_from_string(video_bytes, content_type="video/mp4")
    
    gs_uri = f"gs://{bucket_name}/{object_name}"
    print(f"Uploaded test video fixture to: {gs_uri}")

    # 3. Setup DummyToolContext and populate file_data_mappings
    tool_context = DummyToolContext(bucket_name)
    # Map the extension-less artifact key to the GCS URI
    tool_context.session.state["file_data_mappings"] = {
        "artifact_cuj4_standard_video": gs_uri
    }
    
    # Set the real types.Part metadata loaded by load_artifact
    mock_part = types.Part(
        inline_data=types.Blob(
            mime_type="video/mp4",
            data=b""
        )
    )
    tool_context.artifact_metadata["artifact_cuj4_standard_video"] = mock_part

    # 4. Call video_generation_tool
    output = await video_generation_tool(
        prompt="Make the video look like it has a high contrast black and white filter applied.",
        task="edit",
        aspect_ratio="16:9",
        file_uris=["artifact_cuj4_standard_video"],
        tool_context=tool_context,
    )

    # 5. Assertions
    assert "Error:" not in output, f"Real API video edit failed: {output}"
    assert "![generated_video.mp4](gs://" in output
    assert "https://storage." in output

    # 6. Verify output file
    import requests
    match = re.search(r"Download Video: (https://\S+)", output)
    assert match is not None
    https_url = match.group(1).rstrip('.')
    response = requests.get(https_url)
    assert response.status_code == 200
    video_bytes_out = response.content
    assert len(video_bytes_out) > 50000




