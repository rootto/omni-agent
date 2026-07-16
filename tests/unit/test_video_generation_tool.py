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

"""Unit tests for video_generation_tool (CUJ-1, CUJ-2, CUJ-3 and ADK Session Service state tracking)."""

import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open, ANY

from app.tools.video_generation_tool import video_generation_tool


class DummySession:
    def __init__(self):
        self.state = {}


class DummyToolContext:
    def __init__(self):
        self.session = DummySession()
        self.state = {}
        self.get_artifact_version = AsyncMock()
        self.save_artifact = AsyncMock()
        self.load_artifact = AsyncMock()


@pytest.mark.asyncio
async def test_video_generation_tool_text_to_video_cuj1_landscape() -> None:
    """Verifies CUJ-1 16:9 text-to-video generation, dual-link output, and session state persistence."""
    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_interaction.id = "v1_mock_interaction_123"
    
    mock_step = MagicMock()
    mock_step.type = "model_output"
    
    mock_part = MagicMock()
    mock_part.type = "video"
    mock_part.data = base64.b64encode(b"dummy_mp4_video_bytes").decode("utf-8")
    
    mock_step.content = [mock_part]
    mock_interaction.steps = [mock_step]
    mock_interaction.finish_reason = "STOP"
    mock_client.interactions.create.return_value = mock_interaction

    tool_context = DummyToolContext()
    tool_context.save_artifact.return_value = 1
    
    mock_version = MagicMock()
    mock_version.canonical_uri = "gs://geapp_agents_storage/artifacts/mock123.mp4"
    tool_context.get_artifact_version.return_value = mock_version

    with patch("google.genai.Client", return_value=mock_client), \
         patch("app.tools.video_generation_tool._generate_signed_url") as mock_sign:
        mock_sign.return_value = "https://storage.cloud.google.com/geapp_agents_storage/artifacts/mock123.mp4"

        result = await video_generation_tool(
            prompt="cinematic drone shot flying through a mist-covered pine forest at sunrise",
            task="text_to_video",
            aspect_ratio="16:9",
            tool_context=tool_context,
        )

        # Assert tool called GenAI Interactions API with correct parameters
        mock_client.interactions.create.assert_called_once()
        call_kwargs = mock_client.interactions.create.call_args.kwargs
        assert call_kwargs["input"] == "cinematic drone shot flying through a mist-covered pine forest at sunrise"

        # Assert interaction.id explicitly saved into ADK Session Service state
        assert tool_context.session.state["previous_interaction_id"] == "v1_mock_interaction_123"

        # Assert multi-link output formatted properly
        assert "![generated_video.mp4](gs://geapp_agents_storage/artifacts/mock123.mp4)" in result
        assert "https://storage.cloud.google.com/geapp_agents_storage/artifacts/mock123.mp4" in result
        assert "Aspect Ratio: 16:9" in result


@pytest.mark.asyncio
async def test_video_generation_tool_text_to_video_cuj1_portrait() -> None:
    """Verifies CUJ-1 9:16 portrait text-to-video generation."""
    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_interaction.id = "v1_portrait_interaction_456"
    
    mock_step = MagicMock()
    mock_step.type = "model_output"
    
    mock_part = MagicMock()
    mock_part.type = "video"
    mock_part.data = base64.b64encode(b"portrait_dummy_bytes").decode("utf-8")
    
    mock_step.content = [mock_part]
    mock_interaction.steps = [mock_step]
    mock_client.interactions.create.return_value = mock_interaction

    tool_context = DummyToolContext()
    tool_context.save_artifact.return_value = 1
    
    mock_version = MagicMock()
    mock_version.canonical_uri = "gs://geapp_agents_storage/artifacts/portrait456.mp4"
    tool_context.get_artifact_version.return_value = mock_version

    with patch("google.genai.Client", return_value=mock_client), \
         patch("app.tools.video_generation_tool._generate_signed_url") as mock_sign:
        mock_sign.return_value = "https://storage.cloud.google.com/geapp_agents_storage/artifacts/portrait456.mp4"

        result = await video_generation_tool(
            prompt="portrait vertical waterfall in rainforest 9:16",
            task="text_to_video",
            aspect_ratio="9:16",
            tool_context=tool_context,
        )

        assert tool_context.session.state["previous_interaction_id"] == "v1_portrait_interaction_456"
        assert "Aspect Ratio: 9:16" in result
        assert "![generated_video.mp4](gs://geapp_agents_storage/artifacts/portrait456.mp4)" in result


@pytest.mark.asyncio
async def test_video_generation_tool_image_to_video_cuj2() -> None:
    """Verifies CUJ-2 image-to-video generation using file resolution from session state."""
    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_interaction.id = "v1_image_interaction_789"
    
    mock_step = MagicMock()
    mock_step.type = "model_output"
    
    mock_part = MagicMock()
    mock_part.type = "video"
    mock_part.data = base64.b64encode(b"image_to_video_bytes").decode("utf-8")
    
    mock_step.content = [mock_part]
    mock_interaction.steps = [mock_step]
    mock_client.interactions.create.return_value = mock_interaction

    tool_context = DummyToolContext()
    tool_context.save_artifact.return_value = 1
    
    # Mock load_artifact to return a Part with mime_type="image/png"
    mock_artifact = MagicMock()
    mock_artifact.inline_data.mime_type = "image/png"
    tool_context.load_artifact.return_value = mock_artifact

    mock_version = MagicMock()
    mock_version.canonical_uri = "gs://geapp_agents_storage/artifacts/image789.mp4"
    tool_context.get_artifact_version.return_value = mock_version

    fake_image_path = "/tmp/sample_image.png"

    with patch("google.genai.Client", return_value=mock_client), \
         patch("app.tools.video_generation_tool._generate_signed_url") as mock_sign, \
         patch("app.tools.video_generation_tool.ensure_local_file_path", return_value=fake_image_path), \
         patch("builtins.open", mock_open(read_data=b"dummy_image_bytes")):
        
        mock_sign.return_value = "https://storage.cloud.google.com/geapp_agents_storage/artifacts/image789.mp4"

        result = await video_generation_tool(
            prompt="Animate this image: slowly push the camera forward",
            task="image_to_video",
            aspect_ratio="16:9",
            file_uris=["sample_image.png"],
            tool_context=tool_context,
        )

        mock_client.interactions.create.assert_called_once()
        call_kwargs = mock_client.interactions.create.call_args.kwargs
        
        expected_image_base64 = base64.b64encode(b"dummy_image_bytes").decode("utf-8")
        expected_input = [
            {"type": "image", "data": expected_image_base64, "mime_type": "image/png"},
            {"type": "text", "text": "Animate this image: slowly push the camera forward"}
        ]
        assert call_kwargs["input"] == expected_input
        assert tool_context.session.state["previous_interaction_id"] == "v1_image_interaction_789"
        assert "![generated_video.mp4](gs://geapp_agents_storage/artifacts/image789.mp4)" in result


@pytest.mark.asyncio
async def test_video_generation_tool_stateful_edit_cuj3() -> None:
    """Verifies CUJ-3 stateful iterative editing."""
    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_interaction.id = "v1_edit_interaction_000"
    
    mock_step = MagicMock()
    mock_step.type = "model_output"
    
    mock_part = MagicMock()
    mock_part.type = "video"
    mock_part.data = base64.b64encode(b"edited_video_bytes").decode("utf-8")
    
    mock_step.content = [mock_part]
    mock_interaction.steps = [mock_step]
    mock_client.interactions.create.return_value = mock_interaction

    tool_context = DummyToolContext()
    tool_context.save_artifact.return_value = 1
    
    mock_version = MagicMock()
    mock_version.canonical_uri = "gs://geapp_agents_storage/artifacts/edit000.mp4"
    tool_context.get_artifact_version.return_value = mock_version

    previous_steps = [
        {"type": "user_input", "content": [{"type": "text", "text": "initial prompt"}]},
        {"type": "model_output", "content": [{"type": "video", "data": "initial_video_b64"}]}
    ]
    tool_context.session.state["previous_interaction_id"] = "v1_mock_interaction_123"
    tool_context.session.state["previous_interaction_steps"] = previous_steps

    with patch("google.genai.Client", return_value=mock_client), \
         patch("app.tools.video_generation_tool._generate_signed_url") as mock_sign:
        
        mock_sign.return_value = "https://storage.cloud.google.com/geapp_agents_storage/artifacts/edit000.mp4"

        result = await video_generation_tool(
            prompt="Make it golden hour lighting",
            task="edit",
            aspect_ratio="16:9",
            tool_context=tool_context,
        )

        mock_client.interactions.create.assert_called_once()
        call_kwargs = mock_client.interactions.create.call_args.kwargs
        
        expected_input = previous_steps + [
            {"type": "user_input", "content": [{"type": "text", "text": "Make it golden hour lighting"}]}
        ]
        assert call_kwargs["input"] == expected_input
        assert tool_context.session.state["previous_interaction_id"] == "v1_edit_interaction_000"
        assert "![generated_video.mp4](gs://geapp_agents_storage/artifacts/edit000.mp4)" in result



