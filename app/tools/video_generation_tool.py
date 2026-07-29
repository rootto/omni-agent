import os
import shutil
import subprocess
import asyncio
import base64
import tempfile
import logging
from typing import Optional
from urllib.parse import urlparse
from google.cloud import storage

from google.adk.tools import ToolContext, FunctionTool
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

SYSTEM_FFMPEG = shutil.which("ffmpeg")
SYSTEM_FFPROBE = shutil.which("ffprobe")

if SYSTEM_FFMPEG and SYSTEM_FFPROBE:
    FFMPEG_PATH = SYSTEM_FFMPEG
    FFPROBE_PATH = SYSTEM_FFPROBE
    logger.info("Using system ffmpeg and ffprobe from PATH: %s, %s", FFMPEG_PATH, FFPROBE_PATH)
    def ensure_binaries():
        pass
else:
    BIN_DIR = "/tmp/bin"
    FFMPEG_PATH = os.path.join(BIN_DIR, "ffmpeg")
    FFPROBE_PATH = os.path.join(BIN_DIR, "ffprobe")
    
    def ensure_binaries():
        """Ensures that ffmpeg and ffprobe static binaries are available in /tmp/bin."""
        if os.path.exists(FFMPEG_PATH) and os.path.exists(FFPROBE_PATH):
            return

        os.makedirs(BIN_DIR, exist_ok=True)
        logger.info("Static binaries not found in %s. Downloading from GCS...", BIN_DIR)
        
        storage_client = storage.Client()
        bucket = storage_client.bucket("geapp_agents_storage")
        
        # Download ffmpeg
        if not os.path.exists(FFMPEG_PATH):
            logger.info("Downloading ffmpeg from GCS...")
            blob = bucket.blob("bin/ffmpeg")
            blob.download_to_filename(FFMPEG_PATH)
            os.chmod(FFMPEG_PATH, 0o755)
            logger.info("ffmpeg downloaded and made executable.")

        # Download ffprobe
        if not os.path.exists(FFPROBE_PATH):
            logger.info("Downloading ffprobe from GCS...")
            blob = bucket.blob("bin/ffprobe")
            blob.download_to_filename(FFPROBE_PATH)
            os.chmod(FFPROBE_PATH, 0o755)
            logger.info("ffprobe downloaded and made executable.")

def _generate_signed_url(gcs_uri: str) -> str:
    """Generates an authenticated HTTPS download URL for a GCS URI."""
    if not gcs_uri.startswith("gs://"):
        return gcs_uri
    parsed = urlparse(gcs_uri)
    bucket_name = parsed.netloc
    object_path = parsed.path.lstrip('/')
    return f"https://storage.googleapis.com/{bucket_name}/{object_path}"

def get_video_duration(local_path: str) -> float:
    """Gets duration of a local video file using ffprobe."""
    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        local_path
    ]
    logger.info("Running ffprobe: %s", " ".join(cmd))
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return float(result.stdout.strip())

def split_video(local_path: str, temp_dir: str) -> list[str]:
    """Splits a video into chunks of up to 10 seconds without re-encoding."""
    output_pattern = os.path.join(temp_dir, "chunk_%03d.mp4")
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i", local_path,
        "-f", "segment",
        "-segment_time", "10",
        "-c", "copy",
        output_pattern
    ]
    logger.info("Running ffmpeg split: %s", " ".join(cmd))
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    
    chunks = sorted([
        os.path.join(temp_dir, f)
        for f in os.listdir(temp_dir)
        if f.startswith("chunk_") and f.endswith(".mp4")
    ])
    return chunks

def concat_videos(edited_chunks: list[str], output_path: str) -> None:
    """Concatenates video segments using ffmpeg concat demuxer."""
    temp_dir = os.path.dirname(output_path)
    list_file_path = os.path.join(temp_dir, "concat_list.txt")
    
    with open(list_file_path, "w") as f:
        for chunk in edited_chunks:
            escaped_path = chunk.replace("'", "'\\''")
            f.write(f"file '{escaped_path}'\n")
            
    cmd = [
        FFMPEG_PATH,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file_path,
        "-c", "copy",
        output_path
    ]
    logger.info("Running ffmpeg concat: %s", " ".join(cmd))
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

async def process_chunk(
    chunk_path: str,
    prompt: str,
    client,
    omni_model: str,
    semaphore: asyncio.Semaphore,
) -> str:
    """Edits a single video chunk. If it fails, falls back to original chunk."""
    async with semaphore:
        logger.info("Processing video chunk: %s", chunk_path)
        with open(chunk_path, "rb") as f:
            chunk_bytes = f.read()
        base64_data = base64.b64encode(chunk_bytes).decode("utf-8")
        
        input_data = [
            {"type": "video", "data": base64_data, "mime_type": "video/mp4"},
            {"type": "text", "text": prompt}
        ]
        generation_config = {
            "video_config": {
                "task": "edit",
            }
        }
        
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                interaction = await asyncio.to_thread(
                    client.interactions.create,
                    model=omni_model,
                    input=input_data,
                    generation_config=generation_config
                )
                
                video_bytes = None
                for step in interaction.steps:
                    step_type = step.get("type") if isinstance(step, dict) else getattr(step, "type", None)
                    if step_type == "model_output":
                        step_content = step.get("content") if isinstance(step, dict) else getattr(step, "content", [])
                        for part in step_content:
                            part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
                            
                            if part_type == "video":
                                part_data = part.get("data") if isinstance(part, dict) else getattr(part, "data", None)
                                if part_data:
                                    video_bytes = base64.b64decode(part_data)
                                    break
                            elif isinstance(part, dict):
                                inline_data = part.get("inline_data") or part.get("inlineData")
                                if inline_data and inline_data.get("mime_type", "").startswith("video/"):
                                    raw_data = inline_data.get("data")
                                    if isinstance(raw_data, str):
                                        video_bytes = base64.b64decode(raw_data)
                                    else:
                                        video_bytes = raw_data
                                    break
                            else:
                                if (
                                    getattr(part, "inline_data", None)
                                    and part.inline_data.mime_type
                                    and part.inline_data.mime_type.startswith("video/")
                                ):
                                    raw_data = part.inline_data.data
                                    if isinstance(raw_data, str):
                                        video_bytes = base64.b64decode(raw_data)
                                    else:
                                        video_bytes = raw_data
                                    break
                        if video_bytes:
                            break
                
                if not video_bytes:
                    raise ValueError("No video returned in model output step.")
                
                edited_chunk_path = chunk_path.replace(".mp4", "_edited.mp4")
                with open(edited_chunk_path, "wb") as f:
                    f.write(video_bytes)
                logger.info("Successfully edited chunk saved to: %s", edited_chunk_path)
                return edited_chunk_path
                
            except Exception as e:
                logger.warning("Attempt %d failed for chunk %s: %s", attempt + 1, chunk_path, e)
                if attempt == max_attempts - 1:
                    logger.error("Failed all attempts to edit chunk %s. Falling back to original chunk.", chunk_path)
                    return chunk_path
                await asyncio.sleep(2 * (attempt + 1))


async def ensure_local_file_path(video_ref: str, tool_context: ToolContext, client) -> str:
    """Resolves GCS, Files API, or artifact references to a local video file path."""
    if video_ref.startswith("file://"):
        video_ref = video_ref.replace("file://", "", 1)

    if os.path.exists(video_ref):
        return video_ref

    # Resolve display name via state mapping if present
    if tool_context and tool_context.session.state:
        state_dict = tool_context.session.state.to_dict() if hasattr(tool_context.session.state, "to_dict") else tool_context.session.state
        mappings = state_dict.get("file_data_mappings", {})
        if video_ref in mappings:
            mapped_uri = mappings[video_ref]
            logger.info("Resolved display name %s to GCS URI %s via session state mapping", video_ref, mapped_uri)
            return await ensure_local_file_path(mapped_uri, tool_context, client)

    if video_ref.startswith("gs://"):
        parsed = urlparse(video_ref)
        bucket_name = parsed.netloc
        blob_name = parsed.path.lstrip('/')
        
        # Check if it is a local path masqueraded as gs:// (useful for testing or local run)
        if bucket_name == "local":
            return "/" + blob_name
            
        temp_dir = tempfile.gettempdir()
        local_path = os.path.join(temp_dir, os.path.basename(blob_name))
        
        logger.info("Downloading %s to local path %s", video_ref, local_path)
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        await asyncio.to_thread(blob.download_to_filename, local_path)
        return local_path

    if video_ref.startswith("files/"):
        basename = video_ref.split("/")[-1]
        try:
            temp_dir = tempfile.gettempdir()
            local_path = os.path.join(temp_dir, basename)
            logger.info("Downloading Files API file %s to %s", video_ref, local_path)
            content = await asyncio.to_thread(client.files.download, file=video_ref)
            with open(local_path, "wb") as f:
                f.write(content)
            return local_path
        except Exception as e:
            logger.warning("Files API download failed for %s: %s. Falling back to artifact resolution for %s.", video_ref, e, basename)
            try:
                artifact_version = await tool_context.get_artifact_version(basename)
                canonical_uri = artifact_version.canonical_uri
                return await ensure_local_file_path(canonical_uri, tool_context, client)
            except Exception as ae:
                raise ValueError(f"Could not resolve Files API reference or artifact: {video_ref}") from ae

    try:
        artifact_version = await tool_context.get_artifact_version(video_ref)
        canonical_uri = artifact_version.canonical_uri
        return await ensure_local_file_path(canonical_uri, tool_context, client)
    except Exception as e:
        basename = video_ref.split("/")[-1]
        try:
            artifact_version = await tool_context.get_artifact_version(basename)
            canonical_uri = artifact_version.canonical_uri
            return await ensure_local_file_path(canonical_uri, tool_context, client)
        except Exception as ae:
            raise ValueError(f"Could not resolve video reference: {video_ref}") from ae

ensure_local_video_path = ensure_local_file_path


async def _generate_or_edit_video_impl(
    prompt: str,
    edit_previous_video: bool = False,
    video_to_edit: Optional[str] = None,
    tool_context: ToolContext = None,
    task: Optional[str] = None,
    file_uris: Optional[list[str]] = None,
    aspect_ratio: str = "16:9",
) -> str:
    """Generates a new video or edits an existing/uploaded video using the stateful interactions API with Gemini Omni Flash.

    Args:
        prompt: Description of the video to generate, or edits to apply.
        edit_previous_video: Set to True to edit the previously generated video in this session.
        video_to_edit: Optional file path or GCS URI of a new uploaded video to edit.
    """
    logger.info(
        "[generate_or_edit_video] prompt=%s, edit_previous=%s, video_to_edit=%s",
        prompt,
        edit_previous_video,
        video_to_edit,
    )

    ensure_binaries()

    import os
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"

    from google import genai
    
    # Configure client with higher timeout (10 minutes) and automatic retries for 429 and transient errors
    client = genai.Client(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                initial_delay=1.0,
                attempts=5,
                http_status_codes=[408, 429, 500, 502, 503, 504],
            ),
            timeout=600 * 1000,  # 10 minutes (600,000 milliseconds)
        )
    )
    omni_model = "gemini-omni-flash-preview"

    # Auto-map generated_video.mp4 edits to edit_previous_video = True
    uris_to_process = []
    if video_to_edit:
        if video_to_edit.split("/")[-1] == "generated_video.mp4":
            logger.info("[generate_or_edit_video] Mapping video_to_edit='generated_video.mp4' to edit_previous_video=True")
            edit_previous_video = True
        else:
            uris_to_process.append(video_to_edit)
            
    if file_uris:
        for uri in file_uris:
            if uri.split("/")[-1] == "generated_video.mp4":
                logger.info("[generate_or_edit_video] Mapping file_uri='generated_video.mp4' to edit_previous_video=True")
                edit_previous_video = True
            elif uri not in uris_to_process:
                uris_to_process.append(uri)

    local_file_paths = []
    is_large_video = False
    duration = 0.0

    # Check if previously generated video is large
    if edit_previous_video:
        try:
            prev_local_path = await ensure_local_video_path("generated_video.mp4", tool_context, client)
            duration = get_video_duration(prev_local_path)
            if duration > 10.0:
                logger.info("Previously generated video is large (%.2fs). Treating as fresh edit of 'generated_video.mp4'.", duration)
                edit_previous_video = False
                video_to_edit = "generated_video.mp4"
                local_video_path = prev_local_path
                is_large_video = True
        except Exception as e:
            logger.warning("Could not check duration of previous video: %s", e)

    # Check if input video is large
    if uris_to_process and not is_large_video:
        for uri in uris_to_process:
            local_path = await ensure_local_video_path(uri, tool_context, client)
            local_file_paths.append(local_path)
            
            if not is_large_video:
                if local_path.endswith(".png") or local_path.endswith(".jpg") or local_path.endswith(".jpeg"):
                    dur = 0.0
                else:
                    try:
                        dur = get_video_duration(local_path)
                    except Exception as e:
                        logger.warning("[generate_or_edit_video] Could not get input video duration: %s", e)
                        dur = 0.0
                
                logger.info("[generate_or_edit_video] Input file %s duration: %.2fs", local_path, dur)
                if dur > 10.0:
                    is_large_video = True
                    edit_previous_video = False

    # Discard previous interaction state if this is a new video or edit on a newly uploaded video
    if not edit_previous_video:
        tool_context.session.state["previous_interaction_id"] = None
        tool_context.session.state["previous_interaction_steps"] = None

    # Handle Large Video Flow (>10s)
    if is_large_video:
        local_video_path = local_file_paths[0]
        logger.info("Processing large video of %.2fs by splitting into chunks...", duration)
        with tempfile.TemporaryDirectory() as temp_dir:
            chunks = split_video(local_video_path, temp_dir)
            logger.info("Split video into %d chunks.", len(chunks))
            
            semaphore = asyncio.Semaphore(3)
            tasks = [
                process_chunk(chunk, prompt, client, omni_model, semaphore)
                for chunk in chunks
            ]
            
            edited_chunks = await asyncio.gather(*tasks)
            logger.info("All chunks processed. Edited chunks: %s", edited_chunks)
            
            local_output_path = os.path.join(temp_dir, "combined_output.mp4")
            concat_videos(edited_chunks, local_output_path)
            
            filename = "generated_video.mp4"
            with open(local_output_path, "rb") as f:
                output_bytes = f.read()
            
            video_part = types.Part(
                inline_data=types.Blob(
                    mime_type="video/mp4",
                    data=output_bytes
                )
            )
            
            version = await tool_context.save_artifact(filename=filename, artifact=video_part)
            
            artifact_version = await tool_context.get_artifact_version(filename, version=version)
            canonical_uri = artifact_version.canonical_uri
            
            if canonical_uri.startswith("gs://"):
                http_url = _generate_signed_url(canonical_uri)
                return f"Video generated successfully!\n\nSaved to GCS: ![{filename}]({canonical_uri})\n\nDownload Video: {http_url}\nAspect Ratio: {aspect_ratio}"
            else:
                return f"Video generated successfully!\n\nSaved to artifacts: ![{filename}](artifact://{filename}?version={version})\nDownload Video: artifact://{filename}?version={version}\nAspect Ratio: {aspect_ratio}"

    # Handle Standard Video Flow (<=10s or new generation)
    state_keys = list(tool_context.session.state.to_dict().keys()) if hasattr(tool_context.session.state, "to_dict") else list(tool_context.session.state.keys()) if hasattr(tool_context.session.state, "keys") else []
    logger.info("[generate_or_edit_video] edit_previous_video=%s, tool_context.session.state keys: %s", edit_previous_video, state_keys)
    logger.info("[generate_or_edit_video] previous_interaction_id: %s, previous_interaction_steps exists: %s", tool_context.session.state.get("previous_interaction_id") if tool_context.session.state else None, "previous_interaction_steps" in tool_context.session.state if tool_context.session.state else False)
    steps = None
    previous_interaction_id = None
    if edit_previous_video and tool_context and hasattr(tool_context, "session") and tool_context.session and tool_context.session.state:
        steps = tool_context.session.state.get("previous_interaction_steps")
        previous_interaction_id = tool_context.session.state.get("previous_interaction_id")
        if not steps or not previous_interaction_id:
            logger.warning(
                "[generate_or_edit_video] edit_previous_video=True but no previous steps or interaction ID found in session. Falling back to new video."
            )
            edit_previous_video = False
            previous_interaction_id = None

    generation_config = None
    if edit_previous_video and steps:
        new_step = {
            "type": "user_input",
            "content": [
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }
        input_data = list(steps) + [new_step]
    else:
        if uris_to_process:
            input_data = []
            has_video = False
            for i, local_path in enumerate(local_file_paths):
                uri = uris_to_process[i]
                
                with open(local_path, "rb") as f:
                    file_bytes = f.read()
                
                # Check magic numbers for robust typing
                is_png = file_bytes.startswith(b'\x89PNG')
                is_jpeg = file_bytes.startswith(b'\xff\xd8\xff')
                
                # Resolves mime_type using local path and artifact load metadata
                mime_type = "video/mp4"
                if local_path.endswith(".png") or is_png:
                    mime_type = "image/png"
                elif local_path.endswith(".jpg") or local_path.endswith(".jpeg") or is_jpeg:
                    mime_type = "image/jpeg"
                else:
                    try:
                        part = await tool_context.load_artifact(uri)
                        if part and hasattr(part, "inline_data") and part.inline_data:
                            mime_type = part.inline_data.mime_type
                    except:
                        pass
    
                is_image = "image" in mime_type
                if not is_image:
                    has_video = True
                    
                base64_data = base64.b64encode(file_bytes).decode("utf-8")
                
                input_data.append({
                    "type": "image" if is_image else "video",
                    "mime_type": mime_type,
                    "data": base64_data
                })
            
            input_data.append({
                "type": "text",
                "text": prompt
            })
            
            video_config = {}
            if task == "edit" and has_video:
                video_config["task"] = "edit"
            if aspect_ratio and aspect_ratio in ["16:9", "9:16"]:
                video_config["aspect_ratio"] = aspect_ratio
            generation_config = {"video_config": video_config} if video_config else None
        else:
            input_data = prompt
            video_config = {}
            if aspect_ratio and aspect_ratio in ["16:9", "9:16"]:
                video_config["aspect_ratio"] = aspect_ratio
            generation_config = {"video_config": video_config} if video_config else None

    # Invoke interactions API
    try:
        interaction = client.interactions.create(
            model=omni_model,
            input=input_data,
            generation_config=generation_config,
        )
    except Exception as e:
        err_msg = str(e)
        logger.error("[generate_or_edit_video] Video generation raised an exception: %s", err_msg)
        
        # Check for Safety/Policy Blocks (Deepfakes, Restricted Individuals, etc.)
        if "restricted individuals" in err_msg.lower() or "safety" in err_msg.lower() or "content_blocked" in err_msg.lower():
            if "output contains" in err_msg.lower():
                # Hallucination block (the video model hallucinated a restricted entity and it was blocked prior to returning)
                return (
                    "Error: The video generation model successfully compiled the prompt, but the resulting video output triggered the safety/policy filters and was blocked.\n\n"
                    "### Why did this happen?\n"
                    "Even if your prompt was innocent (e.g. 'a cat playing with yarn'), the model may have hallucinated a human face or a restricted public figure in the background. When the video was finalized, Google's output filters caught it and rejected the entire clip.\n\n"
                    "### How to fix it:\n"
                    "Ask me to retry the exact same prompt (generation is non-deterministic, so a second try often passes), or add explicit instructions to your prompt to exclude humans (e.g. 'no people, only animals, close up shot')."
                )
            else:
                return (
                    "Error: The video generation model blocked your input prompt due to safety/policy filters.\n\n"
                    "### Why did this happen?\n"
                    "Creative video generation models have strict guardrails regarding deepfakes. This issue was triggered because your input likely contained terms referencing humans (e.g., 'anchor', 'person') alongside animation requests.\n\n"
                    "### How to fix it:\n"
                    "Please update your request message to rely on abstract design styles or neutral placeholder metaphors."
                )
        
        # Generic 400 errors or timeout that are NOT safety related shouldn't be masked
        raise e

    # Extract model output video part
    video_part = None
    for step in interaction.steps:
        step_type = step.get("type") if isinstance(step, dict) else getattr(step, "type", None)
        if step_type == "model_output":
            step_content = step.get("content") if isinstance(step, dict) else getattr(step, "content", [])
            for part in step_content:
                part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
                
                # Case 1: VideoContent object or dict with type='video'
                if part_type == "video":
                    part_data = part.get("data") if isinstance(part, dict) else getattr(part, "data", None)
                    if part_data:
                        video_bytes = base64.b64decode(part_data)
                        video_part = types.Part(
                            inline_data=types.Blob(
                                mime_type="video/mp4",
                                data=video_bytes
                            )
                        )
                        break
                # Case 2: Dictionary representation of Part
                elif isinstance(part, dict):
                    inline_data = part.get("inline_data") or part.get("inlineData")
                    if inline_data and inline_data.get("mime_type", "").startswith("video/"):
                        raw_data = inline_data.get("data")
                        if isinstance(raw_data, str):
                            video_bytes = base64.b64decode(raw_data)
                        else:
                            video_bytes = raw_data
                        video_part = types.Part(
                            inline_data=types.Blob(
                                mime_type=inline_data.get("mime_type"),
                                data=video_bytes
                            )
                        )
                        break
                # Case 3: Part object
                else:
                    if (
                        getattr(part, "inline_data", None)
                        and part.inline_data.mime_type
                        and part.inline_data.mime_type.startswith("video/")
                    ):
                        video_part = part
                        break
            if video_part:
                break

    if not video_part:
        raise ValueError("No video was generated or returned by the interactions model.")

    # Save video as an artifact
    filename = "generated_video.mp4"
    version = await tool_context.save_artifact(filename=filename, artifact=video_part)

    # Save interaction ID and serialized steps to the session state
    tool_context.session.state["previous_interaction_id"] = interaction.id

    serialized_steps = []
    if edit_previous_video and steps:
        serialized_steps.extend(steps)
        
    for step in interaction.steps:
        if hasattr(step, "model_dump"):
            serialized_steps.append(step.model_dump())
        else:
            serialized_steps.append(step)
    tool_context.session.state["previous_interaction_steps"] = serialized_steps
    logger.info("[generate_or_edit_video] Saved to tool_context.session.state: previous_interaction_id=%s, count(previous_interaction_steps)=%d", interaction.id, len(serialized_steps))

    # Retrieve the canonical URI of the saved artifact
    artifact_version = await tool_context.get_artifact_version(filename, version=version)
    canonical_uri = artifact_version.canonical_uri

    if canonical_uri.startswith("gs://"):
        http_url = _generate_signed_url(canonical_uri)
        return f"Video generated successfully!\n\nSaved to GCS: ![{filename}]({canonical_uri})\n\nDownload Video: {http_url}\nAspect Ratio: {aspect_ratio}"
    else:
        return f"Video generated successfully!\n\nSaved to artifacts: ![{filename}](artifact://{filename}?version={version})\nDownload Video: artifact://{filename}?version={version}\nAspect Ratio: {aspect_ratio}"
        
async def video_generation_tool(
    prompt: str,
    edit_previous_video: bool = False,
    video_to_edit: Optional[str] = None,
    task: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    file_uris: Optional[list[str]] = None,
    tool_context: ToolContext = None,
) -> str:
    """Generates a new video or edits an existing/uploaded video using the stateful interactions API with Gemini Omni Flash.

    Args:
        prompt: Description of the video to generate, or edits to apply.
        edit_previous_video: Set to True to edit the previously generated video in this session.
        video_to_edit: Optional file path or GCS URI of a new uploaded video to edit.
        task: (Test compat) Task type.
        aspect_ratio: (Test compat) Aspect ratio.
        file_uris: (Test compat) File URIs for inputs.
    """
    
    # Map test arguments to new arguments if needed
    if task == "edit" and not edit_previous_video and not video_to_edit:
        if not file_uris:
            edit_previous_video = True
            
    # 1. Collect diagnostics
    diag_parts = [
        f"edit_previous_video={edit_previous_video}",
        f"video_to_edit={video_to_edit}",
        f"tool_context.session.state exists: {tool_context.session.state is not None}",
    ]
    if tool_context.session.state is not None:
        diag_parts.append(f"state_keys={list(tool_context.session.state.to_dict().keys()) if hasattr(tool_context.session.state, 'to_dict') else list(tool_context.session.state)}")
        diag_parts.append(f"prev_interaction_id={tool_context.session.state.get('previous_interaction_id')}")
        diag_parts.append(f"prev_interaction_steps_count={len(tool_context.session.state.get('previous_interaction_steps', [])) if tool_context.session.state.get('previous_interaction_steps') else 0}")
    else:
        diag_parts.append("state=None")
    diag_str = " | ".join(diag_parts)

    try:
        res = await _generate_or_edit_video_impl(prompt, edit_previous_video, video_to_edit, tool_context, task, file_uris, aspect_ratio=aspect_ratio or "16:9")
        return f"{res}\n\n---\n**Debug Diagnostics:** `{diag_str}`"
    except Exception as e:
        logger.error("[generate_or_edit_video] Failed: %s", e)
        return f"Error executing tool: {e}\n\n---\n**Debug Diagnostics:** `{diag_str}`"

generate_or_edit_video = FunctionTool(func=video_generation_tool)
