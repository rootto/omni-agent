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
from app.tools.storyboard_generation_tool import generate_storyboard, update_storyboard


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
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    
    match = re.search(r"Download Video: (https://\S+)", output)
    assert match is not None, "Could not find HTTPS Download Video link in output"
    https_url = match.group(1).rstrip('.')
    
    print(f"Downloading from: {https_url}")
    
    credentials, _ = google.auth.default()
    authed_session = AuthorizedSession(credentials)
    response = authed_session.get(https_url)
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
    upload_uuid1 = uuid.uuid4().hex[:8]
    object_name1 = f"uploads/{upload_uuid1}_sample_image1.png"
    upload_uuid2 = uuid.uuid4().hex[:8]
    object_name2 = f"uploads/{upload_uuid2}_sample_image2.png"
    
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob1 = bucket.blob(object_name1)
    blob2 = bucket.blob(object_name2)
    
    img_bytes = local_img_path.read_bytes()
    blob1.upload_from_string(img_bytes, content_type="image/png")
    blob2.upload_from_string(img_bytes, content_type="image/png")
    
    gs_uri1 = f"gs://{bucket_name}/{object_name1}"
    gs_uri2 = f"gs://{bucket_name}/{object_name2}"
    print(f"Uploaded test fixtures to: {gs_uri1} and {gs_uri2}")

    # 3. Setup DummyToolContext and populate file_data_mappings
    tool_context = DummyToolContext(bucket_name)
    tool_context.session.state["file_data_mappings"] = {
        "artifact_cuj2_image1": gs_uri1,
        "artifact_cuj2_image2": gs_uri2
    }
    
    mock_part = types.Part(inline_data=types.Blob(mime_type="image/png", data=b""))
    tool_context.artifact_metadata["artifact_cuj2_image1"] = mock_part
    tool_context.artifact_metadata["artifact_cuj2_image2"] = mock_part

    # 4. Call video_generation_tool
    output = await video_generation_tool(
        prompt="Animate these two images fading from the first to the second.",
        task="image_to_video",
        aspect_ratio="16:9",
        file_uris=["artifact_cuj2_image1", "artifact_cuj2_image2"],
        tool_context=tool_context,
    )

    # 5. Assertions
    assert "Error:" not in output, f"Real API image animation failed: {output}"
    assert tool_context.session.state.get("previous_interaction_id") is not None
    assert tool_context.session.state["previous_interaction_id"] != ""

    assert "![generated_video.mp4](gs://" in output
    assert "https://storage." in output

    # 6. Verify the generated file is a valid, real MP4
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    
    match = re.search(r"Download Video: (https://\S+)", output)
    assert match is not None, "Could not find HTTPS Download Video link in output"
    https_url = match.group(1).rstrip('.')
    
    print(f"Downloading from: {https_url}")
    credentials, _ = google.auth.default()
    authed_session = AuthorizedSession(credentials)
    response = authed_session.get(https_url)
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
        prompt="Make the video look like an abstract block with clean sliding motion graphics.",
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
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    match = re.search(r"Download Video: (https://\S+)", output)
    assert match is not None
    https_url = match.group(1).rstrip('.')
    
    credentials, _ = google.auth.default()
    authed_session = AuthorizedSession(credentials)
    response = authed_session.get(https_url)
    assert response.status_code == 200
    video_bytes_out = response.content
    assert len(video_bytes_out) > 50000


@pytest.mark.asyncio
@pytest.mark.timeout(900)
async def test_e2e_cuj8_storyboard_to_multi_video() -> None:
    """Verifies CUJ-8 Long-Content Storyboard to Multi-Video Generation by:
    1. Reading sample_long_storyboard_prompt.txt test fixture.
    2. Decomposing it into a structured storyboard via generate_storyboard (Gemini Flash 3.6).
    3. Performing a targeted board update via update_storyboard without regenerating unchanged boards.
    4. Generating multiple video clips in a loop via generate_storyboard_videos and verifying session state tracking.
    """
    from app.tools.storyboard_generation_tool import generate_storyboard_videos

    project_root = Path(__file__).resolve().parent.parent.parent
    fixture_path = project_root / "tests" / "fixtures" / "sample_long_storyboard_prompt.txt"
    assert fixture_path.exists(), f"Long storyboard prompt fixture not found at {fixture_path}"

    long_prompt = fixture_path.read_text()
    bucket_name = config.get_gcs_bucket_name()
    tool_context = DummyToolContext(bucket_name)

    # 1. Generate Storyboard with Gemini Flash 3.6
    storyboard = await generate_storyboard(
        prompt=long_prompt,
        max_boards=10,
        tool_context=tool_context,
    )
    assert "style_summary" in storyboard
    assert "overall_video_creation_plan" in storyboard
    assert "boards" in storyboard
    assert len(storyboard["boards"]) >= 2
    assert tool_context.session.state.get("current_storyboard") == storyboard

    # Check rich cinematography fields on the first board
    board_0 = storyboard["boards"][0]
    assert "visual_representation" in board_0
    assert "camera_movement" in board_0
    assert "lighting_and_color" in board_0
    assert "narrative" in board_0

    # 2. Update a targeted board
    updated_storyboard = await update_storyboard(
        board_index=2,
        duration_seconds=8.0,
        tool_context=tool_context,
    )
    assert updated_storyboard["boards"][1]["duration_seconds"] == 8.0

    # Slice to first 2 boards for E2E speed while verifying multi-video loop execution
    tool_context.session.state["current_storyboard"]["boards"] = updated_storyboard["boards"][:2]

    # 3. Generate videos in a loop across all boards
    output = await generate_storyboard_videos(
        aspect_ratio="16:9",
        tool_context=tool_context,
    )

    assert "Generated Storyboard Videos (2 Boards)" in output
    assert "Overall Video Creation Plan:" in output
    assert "Board 1" in output
    assert "Board 2" in output
    assert "generated_video_board_1.mp4" in output
    assert "generated_video_board_2.mp4" in output
    assert "![generated_video_board_1.mp4](gs://" in output
    assert "![generated_video_board_2.mp4](gs://" in output

    storyboard_ids = tool_context.session.state.get("storyboard_interaction_ids")
    assert isinstance(storyboard_ids, list) and len(storyboard_ids) >= 2




