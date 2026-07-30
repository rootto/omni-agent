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

"""Unit tests for storyboard_generation_tool, update_storyboard_tool, and multi-video storyboard generation (CUJ-8)."""

import json
import base64
import pytest
from unittest.mock import MagicMock, patch

from app.tools.storyboard_generation_tool import generate_storyboard, update_storyboard
from app.tools.video_generation_tool import video_generation_tool


class DummySession:
    def __init__(self):
        self.state = {}


class DummyToolContext:
    def __init__(self):
        self.session = DummySession()
        self.state = {}
        self.get_artifact_version = MagicMock()
        self.save_artifact = MagicMock()
        self.load_artifact = MagicMock()


@pytest.mark.asyncio
async def test_generate_storyboard_cuj8() -> None:
    """Verifies long-content storyboard decomposition, max_boards limit, and session state persistence."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    
    mock_storyboard_json = {
        "title": "The Chronicles of Europa",
        "overall_video_creation_plan": "IMAX 70mm style cinematography with slow panning shots, deep blue lighting, and orchestral audio cues.",
        "style_summary": "Photorealistic cinematic sci-fi documentary, IMAX 70mm aesthetic, deep space blues.",
        "boards": [
            {
                "board_index": 1,
                "duration_seconds": 10.0,
                "visual_representation": "Wide orbital shot of Europa and the research vessel Hypatia.",
                "camera_movement": "Slow panning right across the icy crust.",
                "lighting_and_color": "Deep space blues and sharp solar specular highlights.",
                "narrative": "Four hundred million miles from Earth lies our solar system's greatest mystery.",
                "audio_and_sound_effects": "Low orchestral hum with telemetry bleeps.",
                "transition_to_next": "Match cut to submersible."
            },
            {
                "board_index": 2,
                "duration_seconds": 9.5,
                "visual_representation": "A robotic submersible descends through a borehole in the ice.",
                "camera_movement": "Downward tracking shot following the submersible.",
                "lighting_and_color": "Dark aqueous blue with searchlights.",
                "narrative": "Piercing solid glacier, our autonomous explorers enter a hidden world.",
                "audio_and_sound_effects": "Underwater sonar ping and bubbling.",
                "transition_to_next": "Fade to black."
            }
        ]
    }
    mock_response.text = json.dumps(mock_storyboard_json)
    mock_client.models.generate_content.return_value = mock_response

    tool_context = DummyToolContext()

    with patch("google.genai.Client", return_value=mock_client):
        result = await generate_storyboard(
            prompt="A long cinematic documentary script about Europa and its subsurface ocean...",
            max_boards=10,
            tool_context=tool_context,
        )

        assert mock_client.models.generate_content.assert_called_once
        assert result["style_summary"] == mock_storyboard_json["style_summary"]
        assert len(result["boards"]) == 2
        assert tool_context.session.state["current_storyboard"] == result


@pytest.mark.asyncio
async def test_update_storyboard_cuj8() -> None:
    """Verifies targeted board and style update without regenerating unchanged boards."""
    tool_context = DummyToolContext()
    tool_context.session.state["current_storyboard"] = {
        "style_summary": "Original style",
        "boards": [
            {
                "board_index": 1,
                "duration_seconds": 10.0,
                "visual_representation": "Scene 1 visual",
                "narrative": "Scene 1 narrative"
            },
            {
                "board_index": 2,
                "duration_seconds": 10.0,
                "visual_representation": "Scene 2 visual",
                "narrative": "Scene 2 narrative"
            }
        ]
    }

    updated_result = await update_storyboard(
        board_index=2,
        visual_representation="Updated Scene 2 at sunset",
        duration_seconds=7.5,
        tool_context=tool_context,
    )

    assert updated_result["style_summary"] == "Original style"
    assert updated_result["boards"][0]["visual_representation"] == "Scene 1 visual"
    assert updated_result["boards"][1]["visual_representation"] == "Updated Scene 2 at sunset"
    assert updated_result["boards"][1]["duration_seconds"] == 7.5
    assert tool_context.session.state["current_storyboard"] == updated_result


@pytest.mark.asyncio
async def test_video_generation_tool_with_board_index() -> None:
    """Verifies that video_generation_tool saves board-specific artifacts and maintains storyboard_interaction_ids."""
    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_interaction.id = "v1_board_1_id"
    
    mock_step = MagicMock()
    mock_step.type = "model_output"
    
    mock_part = MagicMock()
    mock_part.type = "video"
    mock_part.data = base64.b64encode(b"board_1_bytes").decode("utf-8")
    
    mock_step.content = [mock_part]
    mock_interaction.steps = [mock_step]
    mock_interaction.finish_reason = "STOP"
    mock_client.interactions.create.return_value = mock_interaction

    tool_context = DummyToolContext()
    async def save_artifact_mock(filename, artifact):
        return 1
    tool_context.save_artifact = save_artifact_mock
    
    mock_version = MagicMock()
    mock_version.canonical_uri = "gs://geapp_agents_storage/artifacts/board_1.mp4"
    async def get_artifact_version_mock(filename, version=1):
        return mock_version
    tool_context.get_artifact_version = get_artifact_version_mock

    with patch("google.genai.Client", return_value=mock_client), \
         patch("app.tools.video_generation_tool._generate_signed_url") as mock_sign:
        mock_sign.return_value = "https://storage.cloud.google.com/geapp_agents_storage/artifacts/board_1.mp4"

        result = await video_generation_tool(
            prompt="Wide orbital shot of Europa and the research vessel Hypatia.",
            task="text_to_video",
            aspect_ratio="16:9",
            board_index=1,
            tool_context=tool_context,
        )

        assert "Board Index: 1" in result
        assert "generated_video_board_1.mp4" in result
        assert tool_context.session.state["storyboard_interaction_ids"] == ["v1_board_1_id"]
        assert tool_context.session.state["previous_interaction_id"] == "v1_board_1_id"


@pytest.mark.asyncio
async def test_generate_storyboard_videos_cuj8() -> None:
    """Verifies that generate_storyboard_videos loops over all storyboard boards and returns multiple video clips."""
    from app.tools.storyboard_generation_tool import generate_storyboard_videos

    tool_context = DummyToolContext()
    tool_context.session.state["current_storyboard"] = {
        "title": "AstraZeneca Oncology Breakthroughs",
        "overall_video_creation_plan": "High-tech medical lab visual aesthetic with dynamic holographic infographics and energetic voiceover.",
        "style_summary": "Clean medical aesthetic, holographic UI elements, glowing cyan and white.",
        "boards": [
            {
                "board_index": 1,
                "duration_seconds": 10.0,
                "visual_representation": "High-tech medical laboratory background with shifting holographic elements.",
                "camera_movement": "Slow dolly forward.",
                "lighting_and_color": "Cyan holographic glow.",
                "narrative": "Oncology Breakthroughs: July 2026.",
                "audio_and_sound_effects": "Subtle futuristic synthesizer hum."
            },
            {
                "board_index": 2,
                "duration_seconds": 10.0,
                "visual_representation": "Glowing digital global map highlighting oncology revenue.",
                "camera_movement": "Orbiting camera around global map.",
                "lighting_and_color": "High contrast cyan and white.",
                "narrative": "AstraZeneca leads with $14.1 billion H1 oncology revenue.",
                "audio_and_sound_effects": "Data beep transitions."
            }
        ]
    }

    async def mock_gen_impl(**kwargs):
        idx = kwargs.get("board_index", 1)
        return f"![generated_video_board_{idx}.mp4](gs://mock/board_{idx}.mp4)\n**Download:** https://mock/board_{idx}.mp4"

    with patch("app.tools.video_generation_tool._generate_or_edit_video_impl", side_effect=mock_gen_impl) as mock_impl:
        output_md = await generate_storyboard_videos(aspect_ratio="16:9", tool_context=tool_context)

        assert mock_impl.call_count == 2
        assert "Generated Storyboard Videos (2 Boards)" in output_md
        assert "Overall Video Creation Plan:" in output_md
        assert "🎬 Video 1 of 2 | Board 1" in output_md
        assert "Sequence Order:** #1 of 2 in storyboard" in output_md
        assert "Video / Board #1 Player & Download:" in output_md
        assert "Board 1" in output_md
        assert "Board 2" in output_md
        assert "generated_video_board_1.mp4" in output_md
        assert "generated_video_board_2.mp4" in output_md
        assert "Merge Storyboard Videos?" in output_md
        assert "Yes, merge them" in output_md


@pytest.mark.asyncio
async def test_merge_storyboard_videos_cuj8():
    tool_context = DummyToolContext()
    tool_context.session.state["current_storyboard"] = {
        "title": "Merge Test",
        "boards": [
            {"board_index": 1, "duration_seconds": 10.0},
            {"board_index": 2, "duration_seconds": 10.0},
        ],
    }

    class DummyArtifactVersion:
        def __init__(self, uri):
            self.canonical_uri = uri

    async def mock_get_artifact_version(filename, version=None):
        return DummyArtifactVersion(uri=f"gs://mock/{filename}")

    async def mock_ensure_local_file_path(uri, tool_context, client):
        return "/tmp/mock_video.mp4"

    def mock_concat_videos(paths, out_path):
        with open(out_path, "wb") as f:
            f.write(b"mock_merged_mp4_bytes")

    async def mock_save_artifact(filename, artifact, version=None):
        return 1

    tool_context.get_artifact_version = mock_get_artifact_version
    tool_context.save_artifact = mock_save_artifact

    with patch("app.tools.video_generation_tool.ensure_local_file_path", side_effect=mock_ensure_local_file_path), \
         patch("app.tools.video_generation_tool.merge_storyboard_clips", side_effect=mock_concat_videos):
        from app.tools.storyboard_generation_tool import merge_storyboard_videos
        res_md = await merge_storyboard_videos(tool_context=tool_context)

        assert "Merged Storyboard Video" in res_md
        assert "Total Clips Merged:** 2" in res_md
        assert "merged_storyboard_video.mp4" in res_md


@pytest.mark.asyncio
async def test_merge_storyboard_videos_expired_session_fallback():
    """Verifies that when session.state['current_storyboard'] is missing/expired,
    merge_storyboard_videos automatically scans artifact storage for generated board clips.
    """
    tool_context = DummyToolContext()
    # Empty session state (simulates expired/cleared session cache)
    tool_context.session.state = {}

    class DummyArtifactVersion:
        def __init__(self, uri):
            self.canonical_uri = uri

    async def mock_get_artifact_version(filename, version=None):
        if filename in ["generated_video_board_1.mp4", "generated_video_board_2.mp4", "generated_video_board_3.mp4", "merged_storyboard_video.mp4"]:
            return DummyArtifactVersion(uri=f"gs://mock/{filename}")
        return None

    async def mock_ensure_local_file_path(uri, tool_context, client):
        return "/tmp/mock_video.mp4"

    def mock_concat_videos(paths, out_path):
        assert len(paths) == 3
        with open(out_path, "wb") as f:
            f.write(b"mock_merged_mp4_bytes")

    async def mock_save_artifact(filename, artifact, version=None):
        return 1

    tool_context.get_artifact_version = mock_get_artifact_version
    tool_context.save_artifact = mock_save_artifact

    with patch("app.tools.video_generation_tool.ensure_local_file_path", side_effect=mock_ensure_local_file_path), \
         patch("app.tools.video_generation_tool.merge_storyboard_clips", side_effect=mock_concat_videos):
        from app.tools.storyboard_generation_tool import merge_storyboard_videos
        res_md = await merge_storyboard_videos(tool_context=tool_context)

        assert "Merged Storyboard Video" in res_md
        assert "Total Clips Merged:** 3" in res_md
        assert "merged_storyboard_video.mp4" in res_md
