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

"""Storyboard Generation, Editing, and Multi-Video Generation tools for Omni-Agent (CUJ-8)."""

import os
import json
import asyncio
import logging
from typing import Optional, Any
from pydantic import BaseModel, Field

from google.adk.tools import ToolContext, FunctionTool
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class StoryboardBoard(BaseModel):
    board_index: int = Field(description="1-indexed sequence identifier (1 to 10)")
    duration_seconds: float = Field(description="Target duration for the video clip in seconds, maximum 10.0")
    visual_representation: str = Field(description="Comprehensive visual scene description including subject, environment, and atmosphere")
    camera_movement: str = Field(description="Specific camera motion and cinematography (e.g., slow pan right, crane shot up, drone tracking shot)")
    lighting_and_color: str = Field(description="Specific lighting setup, time of day, and color grading for this scene")
    narrative: str = Field(description="Exact voiceover, dialogue, or on-screen text narrative to be used")
    audio_and_sound_effects: str = Field(description="Sound effects, ambient audio, or music cues for this scene")
    transition_to_next: str = Field(description="How this board visually transitions to the next board (e.g., fade to black, whip pan, match cut)")


class StoryboardResult(BaseModel):
    title: str = Field(description="Title of the video storyboard project")
    overall_video_creation_plan: str = Field(description="Detailed plan explaining how the overall video is going to be created, including visual aesthetic, pacing, camera choreography, visual continuity, and audio design across all boards")
    style_summary: str = Field(description="A concise summary of the determined, consistent visual style across all boards")
    boards: list[StoryboardBoard] = Field(description="An array of up to 10 board objects")


async def generate_storyboard(
    prompt: str,
    max_boards: int = 10,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """MUST be called whenever the user provides long content, news, an article, a script, or asks to create a storyboard or multi-scene video proposal. Decomposes the content into up to 10 structured storyboard boards and saves it to session state.

    Args:
        prompt: Long content or multi-scene narrative description to decompose.
        max_boards: Maximum number of storyboard boards to generate (up to 10).
    """
    logger.info("[generate_storyboard] prompt_len=%d, max_boards=%d", len(prompt), max_boards)

    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")

    client = genai.Client(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                initial_delay=1.0,
                attempts=5,
                http_status_codes=[408, 429, 500, 502, 503, 504],
            ),
            timeout=600 * 1000,
        )
    )
    storyboard_model = os.environ.get("STORYBOARD_MODEL_ID", "gemini-3.6-flash")

    system_instruction = (
        f"You are a professional cinematic storyboard artist and video director. Analyze the following video concept/script "
        f"and decompose it into a structured storyboard of up to {max_boards} boards.\n\n"
        f"CRITICAL STORYBOARD DIRECTIVES:\n"
        f"1. NO BROADCAST / NEWSROOM SIMULATION: Do NOT try simulating live newsrooms with broadcast anchors, virtual television sets, news studios, or synthetic news hosts. Even if the input is a news article, press release, or report, you MUST translate the concepts into immersive, cinematic real-world visual metaphors, scientific animations, or architectural visualizations.\n"
        f"2. OPTIMAL BOARD COUNT & SUMMARIZATION: If the user specifies a specific number of boards (or by default up to {max_boards}), summarize the content so that it fits naturally within that number (no need to always create 10 boards if fewer boards tell the story better).\n"
        f"3. NO REPETITION: Ensure that no visual scenes, concepts, or narrative statements are repeated across boards.\n"
        f"4. CONTINUOUS FLOW: Maintain seamless visual and chronological continuity between boards, so each board transitions smoothly into the next.\n\n"
        f"Provide a comprehensive 'overall_video_creation_plan' explaining exactly how the overall video is going to be created "
        f"(visual aesthetic, camera choreography, lighting style, audio/voiceover integration, pacing, and continuity across scenes). "
        f"Each board represents a video clip of up to 10.0 seconds and MUST include detailed cinematography, lighting, narrative, and audio cues. "
        f"Ensure a consistent global style summary across all boards."
    )

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=storyboard_model,
        contents=f"{system_instruction}\n\nConcept/Script:\n{prompt}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=StoryboardResult,
            temperature=0.4,
        ),
    )

    parsed_json = json.loads(response.text)
    if "boards" in parsed_json and len(parsed_json["boards"]) > max_boards:
        parsed_json["boards"] = parsed_json["boards"][:max_boards]

    if tool_context and tool_context.session and tool_context.session.state is not None:
        tool_context.session.state["current_storyboard"] = parsed_json
        logger.info(
            "[generate_storyboard] Stored current_storyboard in session state with %d boards",
            len(parsed_json.get("boards", [])),
        )

    return parsed_json


async def update_storyboard(
    board_index: Optional[int] = None,
    visual_representation: Optional[str] = None,
    camera_movement: Optional[str] = None,
    lighting_and_color: Optional[str] = None,
    narrative: Optional[str] = None,
    audio_and_sound_effects: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    style_summary: Optional[str] = None,
    overall_video_creation_plan: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Updates targeted boards or style summary in the current storyboard in ADK Session Service state without regenerating unchanged boards.

    Args:
        board_index: 1-indexed board index to modify.
        visual_representation: Updated visual description for the board.
        camera_movement: Updated camera motion for the board.
        lighting_and_color: Updated lighting setup for the board.
        narrative: Updated narrative/voiceover for the board.
        audio_and_sound_effects: Updated sound/music cues for the board.
        duration_seconds: Updated duration in seconds (<= 10.0).
        style_summary: Updated global style summary across boards.
        overall_video_creation_plan: Updated overall video creation plan.
    """
    if not (tool_context and tool_context.session and tool_context.session.state is not None):
        return {"error": "No session state available to update storyboard."}

    current_storyboard = tool_context.session.state.get("current_storyboard")
    if not current_storyboard or not isinstance(current_storyboard, dict):
        logger.warning("[update_storyboard] No current_storyboard found in session state.")
        return {
            "error": "No current_storyboard found in session state. Please ask me to create a storyboard proposal first before updating."
        }

    if style_summary is not None:
        current_storyboard["style_summary"] = style_summary
    if overall_video_creation_plan is not None:
        current_storyboard["overall_video_creation_plan"] = overall_video_creation_plan

    if board_index is not None:
        boards = current_storyboard.get("boards", [])
        for board in boards:
            if board.get("board_index") == board_index:
                if visual_representation is not None:
                    board["visual_representation"] = visual_representation
                if camera_movement is not None:
                    board["camera_movement"] = camera_movement
                if lighting_and_color is not None:
                    board["lighting_and_color"] = lighting_and_color
                if narrative is not None:
                    board["narrative"] = narrative
                if audio_and_sound_effects is not None:
                    board["audio_and_sound_effects"] = audio_and_sound_effects
                if duration_seconds is not None:
                    board["duration_seconds"] = min(float(duration_seconds), 10.0)
                break
        else:
            return {"error": f"Board with index {board_index} not found in storyboard."}

    tool_context.session.state["current_storyboard"] = current_storyboard
    logger.info("[update_storyboard] Updated current_storyboard in session state (board_index=%s)", board_index)
    return current_storyboard


async def generate_storyboard_videos(
    prompt: Optional[str] = None,
    aspect_ratio: str = "16:9",
    tool_context: Optional[ToolContext] = None,
) -> str:
    """Loops through each storyboard board in the approved session state and creates a video for each board using Gemini Omni.

    Args:
        prompt: Optional script or narrative prompt to auto-generate a storyboard if none exists in session state.
        aspect_ratio: Aspect ratio for all generated videos ("16:9" or "9:16").
    """
    if not (tool_context and tool_context.session and tool_context.session.state is not None):
        raise ValueError("No session state available to retrieve storyboard for video generation.")

    current_storyboard = tool_context.session.state.get("current_storyboard")
    if not current_storyboard or not isinstance(current_storyboard, dict):
        if prompt:
            logger.warning("[generate_storyboard_videos] current_storyboard missing in session state. Auto-generating from prompt: %s", prompt)
            current_storyboard = await generate_storyboard(prompt=prompt, max_boards=10, tool_context=tool_context)
        else:
            return (
                "⚠️ **No approved storyboard found in session state.**\n\n"
                "Please ask me to create a storyboard proposal first (e.g., *'Create a storyboard for ...'*), "
                "or provide the script/prompt you would like me to turn into videos."
            )

    boards = current_storyboard.get("boards", [])
    if not boards:
        raise ValueError("Current storyboard contains no boards.")

    from app.tools.video_generation_tool import _generate_or_edit_video_impl

    title = current_storyboard.get("title", "Storyboard Videos")
    overall_plan = current_storyboard.get("overall_video_creation_plan", "")
    style_summary = current_storyboard.get("style_summary", "")

    logger.info(
        "[generate_storyboard_videos] Starting generation loop for %d boards (aspect_ratio=%s)",
        len(boards),
        aspect_ratio,
    )

    results_md = [
        f"# {title} — Generated Storyboard Videos ({len(boards)} Boards)\n",
        f"**Overall Video Creation Plan:** {overall_plan}\n" if overall_plan else "",
        f"**Style Summary:** {style_summary}\n\n---\n",
    ]

    for board in boards:
        idx = board.get("board_index", 1)
        vis = board.get("visual_representation", "")
        cam = board.get("camera_movement", "")
        light = board.get("lighting_and_color", "")
        narr = board.get("narrative", "")
        audio = board.get("audio_and_sound_effects", "")
        duration = board.get("duration_seconds", 10.0)

        board_prompt_parts = [vis]
        if cam:
            board_prompt_parts.append(f"Camera movement: {cam}.")
        if light:
            board_prompt_parts.append(f"Lighting and color: {light}.")
        if narr:
            board_prompt_parts.append(f"Narrative voiceover/text: {narr}.")
        if audio:
            board_prompt_parts.append(f"Audio/Sound: {audio}.")
        if style_summary:
            board_prompt_parts.append(f"Overall style: {style_summary}.")

        full_prompt = " ".join(board_prompt_parts)
        logger.info("[generate_storyboard_videos] Generating video for board %d/%d: %s", idx, len(boards), full_prompt)

        try:
            clip_res = await _generate_or_edit_video_impl(
                prompt=full_prompt,
                edit_previous_video=False,
                video_to_edit=None,
                tool_context=tool_context,
                task="text_to_video",
                aspect_ratio=aspect_ratio,
                board_index=idx,
            )
            results_md.append(
                f"### 🎬 Video {idx} of {len(boards)} | Board {idx} (Duration: {duration}s)\n"
                f"**Sequence Order:** #{idx} of {len(boards)} in storyboard\n"
                f"**Scene Description:** {vis}\n"
                f"**Camera Movement:** {cam}\n"
                f"**Lighting & Color:** {light}\n"
                f"**Narrative:** {narr}\n"
                f"**Audio Cues:** {audio}\n\n"
                f"**Video / Board #{idx} Player & Download:**\n"
                f"{clip_res}\n\n---\n"
            )
        except Exception as e:
            logger.error(
                "[generate_storyboard_videos] Board %d failed: %s | FULL PROMPT: %r",
                idx,
                e,
                full_prompt,
            )
            results_md.append(
                f"### 🎬 Video {idx} of {len(boards)} | Board {idx} (Duration: {duration}s)\n"
                f"**Sequence Order:** #{idx} of {len(boards)} in storyboard\n"
                f"**Error generating Video / Board #{idx}:** {e}\n\n---\n"
            )

    results_md.append(
        f"### 🎬 Merge Storyboard Videos?\n"
        f"Would you like me to merge all **{len(boards)}** of these video clips together into a single continuous video in sequence order? \n"
        f"*If yes, just reply **\"Yes, merge them\"** (or type `/merge`), and I will use ffmpeg to stitch them together and give you the full video!*\n"
    )

    return "\n".join(results_md)


async def merge_storyboard_videos(
    tool_context: ToolContext = None,
) -> str:
    """Merges all generated storyboard video clips into a single continuous video in sequence order using ffmpeg.

    Should be called after generate_storyboard_videos_tool when the user asks to merge, stitch, or combine the storyboard video clips together.
    """
    logger.warning("[merge_storyboard_videos] Executing merge_storyboard_videos_tool...")
    if not tool_context:
        err_msg = "Error: No tool_context provided to merge_storyboard_videos."
        logger.error("[merge_storyboard_videos] %s", err_msg)
        return err_msg

    boards = []
    source = "session_state"
    if tool_context.session and tool_context.session.state:
        current_sb = tool_context.session.state.get("current_storyboard")
        if current_sb and current_sb.get("boards"):
            boards = current_sb["boards"]

    # In serverless runtime environments where active session cache clears after generation,
    # automatically discover generated_video_board_{idx}.mp4 from artifact storage.
    if not boards:
        logger.warning("[merge_storyboard_videos] Session state 'current_storyboard' empty or expired. Scanning tool_context artifacts for generated_video_board_{idx}.mp4...")
        source = "artifact_discovery"
        for idx in range(1, 51):
            filename = f"generated_video_board_{idx}.mp4"
            try:
                art_ver = await tool_context.get_artifact_version(filename)
                if art_ver and getattr(art_ver, "canonical_uri", None):
                    boards.append({"board_index": idx})
                else:
                    break
            except Exception:
                break

    if not boards:
        err_msg = "Error: No storyboard boards found in session state or artifact storage. Please generate storyboard videos first before merging."
        logger.error("[merge_storyboard_videos] %s", err_msg)
        return err_msg

    logger.warning("[merge_storyboard_videos] Starting ffmpeg merge across %d storyboard boards (source=%s)", len(boards), source)

    from app.tools.video_generation_tool import (
        ensure_local_file_path,
        merge_storyboard_clips,
        _generate_signed_url,
    )
    import tempfile
    from google.genai import types

    try:
        from google.cloud import storage
        client = storage.Client()
    except Exception:
        client = None
    local_video_paths = []

    for idx, board in enumerate(boards, start=1):
        board_idx = board.get("board_index", idx)
        filename = f"generated_video_board_{board_idx}.mp4"
        try:
            art_ver = await tool_context.get_artifact_version(filename)
            logger.warning("[merge_storyboard_videos] Loading clip %d/%d: %s (uri=%s)", idx, len(boards), filename, getattr(art_ver, "canonical_uri", "None"))
            local_path = await ensure_local_file_path(art_ver.canonical_uri, tool_context, client)
            local_video_paths.append(local_path)
        except Exception as e:
            logger.error("[merge_storyboard_videos] Could not load video artifact %s: %s", filename, e)
            return f"Error: Could not retrieve generated video clip for Board #{board_idx} ({filename}): {e}. Ensure all board clips have been generated first."

    temp_dir = tempfile.mkdtemp()
    merged_output_path = os.path.join(temp_dir, "merged_storyboard_video.mp4")

    logger.warning("[merge_storyboard_videos] Merging %d clips into %s...", len(local_video_paths), merged_output_path)
    try:
        merge_storyboard_clips(local_video_paths, merged_output_path)
        logger.warning("[merge_storyboard_videos] Successfully created merged video: %s", merged_output_path)
    except Exception as e:
        logger.error("[merge_storyboard_videos] ffmpeg concat failed: %s", e)
        return f"Error: ffmpeg failed to merge storyboard video clips: {e}"

    with open(merged_output_path, "rb") as f:
        merged_bytes = f.read()

    video_part = types.Part(
        inline_data=types.Blob(
            mime_type="video/mp4",
            data=merged_bytes,
        )
    )

    version = await tool_context.save_artifact(
        filename="merged_storyboard_video.mp4",
        artifact=video_part,
    )

    art_ver = await tool_context.get_artifact_version("merged_storyboard_video.mp4", version=version)
    canonical_uri = art_ver.canonical_uri

    if canonical_uri.startswith("gs://"):
        download_url = _generate_signed_url(canonical_uri)
        return (
            f"### 🎬 Merged Storyboard Video (All {len(boards)} Boards Combined)\n\n"
            f"**Total Clips Merged:** {len(boards)} in chronological sequence order\n\n"
            f"**Merged Video Player & Download:**\n"
            f"![merged_storyboard_video.mp4]({canonical_uri})\n\n"
            f"**Download Full Merged Video:** {download_url}\n"
        )
    else:
        download_url = f"artifact://merged_storyboard_video.mp4?version={version}"
        return (
            f"### 🎬 Merged Storyboard Video (All {len(boards)} Boards Combined)\n\n"
            f"**Total Clips Merged:** {len(boards)} in chronological sequence order\n\n"
            f"**Merged Video Player & Download:**\n"
            f"![merged_storyboard_video.mp4]({download_url})\n\n"
            f"**Download Full Merged Video:** {download_url}\n"
        )


storyboard_generation_tool = FunctionTool(func=generate_storyboard)
update_storyboard_tool = FunctionTool(func=update_storyboard)
generate_storyboard_videos_tool = FunctionTool(func=generate_storyboard_videos)
merge_storyboard_videos_tool = FunctionTool(func=merge_storyboard_videos)

