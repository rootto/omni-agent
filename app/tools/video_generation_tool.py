import os
import shutil
import subprocess
import asyncio
import base64
import tempfile
import logging
from typing import Optional
from urllib.parse import urlparse
import httpx
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
        """Ensures that ffmpeg and ffprobe static binaries are available and executable in /tmp/bin."""
        if (
            os.path.exists(FFMPEG_PATH)
            and os.access(FFMPEG_PATH, os.X_OK)
            and os.path.exists(FFPROBE_PATH)
            and os.access(FFPROBE_PATH, os.X_OK)
        ):
            return

        os.makedirs(BIN_DIR, exist_ok=True)
        bucket_name = os.environ.get("BINARIES_BUCKET_NAME") or os.environ.get("GCS_BUCKET_NAME") or "geapp_agents_storage"
        logger.info("Static binaries not found or not executable in %s. Downloading from GCS bucket '%s'...", BIN_DIR, bucket_name)
        
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
        except Exception as e:
            logger.error("Failed to initialize GCS client in ensure_binaries: %s", e)
            return

        for binary_name, target_path in [("ffmpeg", FFMPEG_PATH), ("ffprobe", FFPROBE_PATH)]:
            if not (os.path.exists(target_path) and os.access(target_path, os.X_OK)):
                temp_path = f"{target_path}.tmp.{os.getpid()}"
                try:
                    logger.info("Downloading %s from gs://%s/bin/%s...", binary_name, bucket_name, binary_name)
                    blob = bucket.blob(f"bin/{binary_name}")
                    blob.download_to_filename(temp_path)
                    os.chmod(temp_path, 0o755)
                    os.replace(temp_path, target_path)
                    logger.info("%s downloaded and made executable at %s.", binary_name, target_path)
                except Exception as e:
                    logger.error("Failed to download %s from gs://%s/bin/%s: %s", binary_name, bucket_name, binary_name, e)
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    if os.path.exists(target_path):
                        try:
                            os.chmod(target_path, 0o755)
                        except Exception:
                            pass

def _generate_signed_url(gcs_uri: str) -> str:
    """Generates an authenticated HTTPS download URL for a GCS URI."""
    if not gcs_uri.startswith("gs://"):
        return gcs_uri
    import urllib.parse
    parsed = urllib.parse.urlparse(gcs_uri)
    bucket_name = parsed.netloc
    object_path = parsed.path.lstrip('/')
    
    # Attempt V4 signed URL valid for 7 days using IAM signBlob when on ADC credentials
    try:
        import datetime
        import google.auth
        from google.auth.transport.requests import Request
        credentials, _ = google.auth.default()
        if not credentials.valid:
            credentials.refresh(Request())
        
        sa_email = getattr(credentials, "service_account_email", None)
        if not sa_email:
            import urllib.request
            try:
                req = urllib.request.Request(
                    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
                    headers={"Metadata-Flavor": "Google"}
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    sa_email = resp.read().decode("utf-8").strip()
            except Exception:
                sa_email = None

        # Google-managed Service Agents (e.g. service-...@gcp-sa-aiplatform-re...) cannot call signBlob on themselves.
        # Delegate signing to the user-managed default Compute Engine service account.
        if not sa_email or sa_email.startswith("service-") or (sa_email.endswith(".gserviceaccount.com") and "-compute@" not in sa_email and "@gcp-sa-" in sa_email):
            project_number = os.environ.get("PROJECT_NUMBER", "687484203981")
            sa_email = os.environ.get("SIGNING_SERVICE_ACCOUNT", f"{project_number}-compute@developer.gserviceaccount.com")

        client = storage.Client(credentials=credentials)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_path)
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(days=7),
            method="GET",
            service_account_email=sa_email,
            access_token=credentials.token,
        )
    except Exception as e:
        logger.warning("Could not generate V4 signed URL for %s via IAM signBlob (%s). Using URL-encoded storage.googleapis.com URL.", gcs_uri, e)
        encoded_path = urllib.parse.quote(object_path, safe='/')
        return f"https://storage.googleapis.com/{bucket_name}/{encoded_path}"

def has_audio_stream(local_path: str) -> bool:
    """Checks if a video file has an audio stream using ffprobe."""
    ensure_binaries()
    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        local_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return "audio" in res.stdout.lower()

def merge_storyboard_clips(video_paths: list[str], output_path: str) -> None:
    """Concatenates independent storyboard video clips into a single continuous MP4 file.
    
    Normalizes each clip into a standardized MP4 intermediate file with consistent H.264 video,
    24 fps, and stereo AAC audio (injecting silent audio if missing). Merges using ffmpeg's
    concat demuxer with a filelist and falls back to filter_complex concat if needed.
    """
    ensure_binaries()
    temp_dir = os.path.dirname(output_path)
    norm_mp4_files = []
    total_input_duration = 0.0

    for idx, chunk_path in enumerate(video_paths, start=1):
        try:
            dur = get_video_duration(chunk_path)
        except Exception as e:
            dur = 10.0
            logger.warning("[merge_storyboard_clips] Could not read duration for %s: %s", chunk_path, e)
        
        total_input_duration += dur
        has_audio = has_audio_stream(chunk_path)
        logger.warning("[merge_storyboard_clips] Clip %d/%d (%s): duration=%.2fs, has_audio=%s", idx, len(video_paths), chunk_path, dur, has_audio)
        
        norm_path = os.path.join(temp_dir, f"norm_clip_{idx:03d}.mp4")
        
        if has_audio:
            cmd_norm = [
                FFMPEG_PATH,
                "-y",
                "-i", chunk_path,
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", "24",
                "-c:a", "aac",
                "-ar", "44100",
                "-ac", "2",
                norm_path
            ]
        else:
            cmd_norm = [
                FFMPEG_PATH,
                "-y",
                "-i", chunk_path,
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", "24",
                "-c:a", "aac",
                "-ar", "44100",
                "-ac", "2",
                "-shortest",
                norm_path
            ]
            
        logger.warning("[merge_storyboard_clips] Normalizing clip %d to standard MP4: %s", idx, " ".join(cmd_norm))
        res_norm = subprocess.run(cmd_norm, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res_norm.returncode != 0:
            logger.warning("[merge_storyboard_clips] Normalization failed for clip %d (rc=%d): %s. Retrying with fallback synthetic audio...", idx, res_norm.returncode, res_norm.stderr.decode('utf-8', errors='ignore'))
            cmd_fallback = [
                FFMPEG_PATH,
                "-y",
                "-i", chunk_path,
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", "24",
                "-c:a", "aac",
                "-ar", "44100",
                "-ac", "2",
                "-shortest",
                norm_path
            ]
            res_fb = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res_fb.returncode != 0:
                logger.error("[merge_storyboard_clips] Fallback normalization failed for clip %d (rc=%d): %s", idx, res_fb.returncode, res_fb.stderr.decode('utf-8', errors='ignore'))
                raise RuntimeError(f"ffmpeg normalization failed for clip {idx}: {res_fb.stderr.decode('utf-8', errors='ignore')}")
            
        norm_mp4_files.append(norm_path)

    # Method 1: Concat Demuxer with filelist.txt
    concat_list_file = os.path.join(temp_dir, "concat_filelist.txt")
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for p in norm_mp4_files:
            # Escape single quotes if any in file path
            escaped_p = p.replace("'", "'\\''")
            f.write(f"file '{escaped_p}'\n")

    cmd_concat_demux = [
        FFMPEG_PATH,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_file,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path
    ]
    logger.warning("[merge_storyboard_clips] Concatenating %d MP4 files via concat demuxer: %s", len(norm_mp4_files), " ".join(cmd_concat_demux))
    res_demux = subprocess.run(cmd_concat_demux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    if res_demux.returncode != 0:
        logger.warning("[merge_storyboard_clips] Concat demuxer failed (rc=%d): %s. Falling back to filter_complex concat...", res_demux.returncode, res_demux.stderr.decode('utf-8', errors='ignore'))
        
        # Method 2: filter_complex fallback
        filter_inputs = []
        filter_spec_parts = []
        for i, p in enumerate(norm_mp4_files):
            filter_inputs.extend(["-i", p])
            filter_spec_parts.append(f"[{i}:v][{i}:a]")
        filter_spec = "".join(filter_spec_parts) + f"concat=n={len(norm_mp4_files)}:v=1:a=1[outv][outa]"

        cmd_filter_concat = [
            FFMPEG_PATH,
            "-y",
            *filter_inputs,
            "-filter_complex", filter_spec,
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", "24",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            output_path
        ]
        logger.warning("[merge_storyboard_clips] Running filter_complex concat fallback: %s", " ".join(cmd_filter_concat))
        res_filter = subprocess.run(cmd_filter_concat, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res_filter.returncode != 0:
            logger.error("[merge_storyboard_clips] filter_complex concat failed (rc=%d): %s", res_filter.returncode, res_filter.stderr.decode('utf-8', errors='ignore'))
            raise RuntimeError(f"ffmpeg video concat failed: {res_filter.stderr.decode('utf-8', errors='ignore')}")

    try:
        merged_dur = get_video_duration(output_path)
        logger.warning("[merge_storyboard_clips] SUCCESS: Merged %d clips into %s. Total input duration=%.2fs, Merged duration=%.2fs", len(video_paths), output_path, total_input_duration, merged_dur)
        if merged_dur < (total_input_duration * 0.8):
            logger.error("[merge_storyboard_clips] WARNING: Merged video duration (%.2fs) is shorter than expected total input duration (%.2fs)!", merged_dur, total_input_duration)
    except Exception as e:
        logger.warning("[merge_storyboard_clips] Could not verify final merged duration: %s", e)

def get_video_duration(local_path: str) -> float:
    """Gets duration of a local video file using ffprobe."""
    ensure_binaries()
    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        local_path
    ]
    logger.warning("Running ffprobe: %s", " ".join(cmd))
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    return float(result.stdout.strip())

def split_video(local_path: str, temp_dir: str) -> list[str]:
    """Splits a video into chunks of up to 10 seconds without re-encoding."""
    ensure_binaries()
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
    """Concatenates video segments using ffmpeg concat demuxer with fallback re-encoding."""
    ensure_binaries()
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
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        logger.warning("ffmpeg concat with -c copy failed (rc=%d). Falling back to re-encoding concat.", res.returncode)
        cmd_reencode = [
            FFMPEG_PATH,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file_path,
            "-c:v", "libx264",
            "-c:a", "aac",
            output_path
        ]
        subprocess.run(cmd_reencode, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

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
                    generation_config=generation_config,
                    timeout=httpx.Timeout(600.0, connect=60.0),
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
        parts = blob_name.rstrip("/").split("/")
        if len(parts) >= 2 and parts[-1].isdigit():
            clean_name = f"{parts[-2]}_v{parts[-1]}"
        else:
            clean_name = parts[-1]
        if not clean_name.endswith((".mp4", ".mov", ".avi", ".webm")):
            clean_name += ".mp4"
            
        local_path = os.path.join(temp_dir, clean_name)
        
        logger.warning("Downloading GCS artifact %s to unique local path %s", video_ref, local_path)
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
    board_index: Optional[int] = None,
) -> str:
    """Generates a new video or edits an existing/uploaded video using the stateful interactions API with Gemini Omni Flash.

    Args:
        prompt: Description of the video to generate, or edits to apply.
        edit_previous_video: Set to True to edit the previously generated video in this session.
        video_to_edit: Optional file path or GCS URI of a new uploaded video to edit.
        board_index: Optional 1-indexed board number when generating a storyboard video clip.
    """
    logger.info(
        "[generate_or_edit_video] prompt=%s, edit_previous=%s, video_to_edit=%s, board_index=%s",
        prompt,
        edit_previous_video,
        video_to_edit,
        board_index,
    )

    if board_index is not None and tool_context and tool_context.session and tool_context.session.state is not None:
        storyboard_ids = tool_context.session.state.get("storyboard_interaction_ids", [])
        if edit_previous_video and isinstance(storyboard_ids, list) and 1 <= board_index <= len(storyboard_ids):
            tool_context.session.state["previous_interaction_id"] = storyboard_ids[board_index - 1]
            logger.info("[generate_or_edit_video] Set previous_interaction_id to storyboard board %d ID: %s", board_index, storyboard_ids[board_index - 1])

    if tool_context and tool_context.session and tool_context.session.state is not None:
        active_style_markdown = tool_context.session.state.get("active_style_markdown")
        from app.tools.style_tool import format_style_prompt
        prompt = format_style_prompt(prompt, active_style_markdown)

    ensure_binaries()

    import os
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")

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
    omni_model = os.environ.get("OMNI_MODEL_ID", "gemini-omni-flash-preview")

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
        if local_file_paths:
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
            timeout=httpx.Timeout(600.0, connect=60.0),
        )
    except Exception as e:
        err_msg = str(e)
        logger.error(
            "[generate_or_edit_video] Video generation raised an exception: %s | FULL PROMPT: %r",
            err_msg,
            prompt,
        )
        
        # Check for Safety/Policy Blocks (Deepfakes, Restricted Individuals, Responsible AI, etc.)
        if any(term in err_msg.lower() for term in [
            "restricted individuals",
            "safety",
            "content_blocked",
            "responsible ai",
            "violates",
            "invalid_request",
        ]):
            prefix = f"[Board {board_index} Safety Block] " if board_index is not None else ""
            if "output contains" in err_msg.lower():
                # Hallucination block (the video model hallucinated a restricted entity and it was blocked prior to returning)
                return (
                    f"{prefix}Error: The video generation model successfully compiled the prompt, but the resulting video output triggered the safety/policy filters and was blocked.\n\n"
                    "### Why did this happen?\n"
                    "Even if your prompt was innocent (e.g. 'a cat playing with yarn'), the model may have hallucinated a human face or a restricted public figure in the background. When the video was finalized, Google's output filters caught it and rejected the entire clip.\n\n"
                    "### How to fix it:\n"
                    "Ask me to retry the exact same prompt (generation is non-deterministic, so a second try often passes), or add explicit instructions to your prompt to exclude humans (e.g. 'no people, only animals, close up shot')."
                )
            else:
                return (
                    f"{prefix}Error: The video generation model blocked your input prompt due to Google's Responsible AI safety/policy filters.\n\n"
                    "### Why did this happen?\n"
                    "Creative video generation models have strict guardrails regarding sensitive topics, medical claims, public figures, or deepfakes. This issue was triggered because your input likely contained terms referencing humans, clinical trials, or medical claims alongside video generation requests.\n\n"
                    "### How to fix it:\n"
                    "Please update your storyboard board description to rely on abstract design styles, data visualizations, or neutral placeholder metaphors without human figures or medical claims."
                )
        
        # Generic errors that are NOT safety related shouldn't be masked
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
    filename = f"generated_video_board_{board_index}.mp4" if board_index is not None else "generated_video.mp4"
    version = await tool_context.save_artifact(filename=filename, artifact=video_part)

    # Save interaction ID and serialized steps to the session state
    tool_context.session.state["previous_interaction_id"] = interaction.id

    storyboard_ids = list(tool_context.session.state.get("storyboard_interaction_ids", []))
    if board_index is not None and 1 <= board_index <= len(storyboard_ids):
        storyboard_ids[board_index - 1] = interaction.id
    else:
        storyboard_ids.append(interaction.id)
    tool_context.session.state["storyboard_interaction_ids"] = storyboard_ids

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

    board_header = f"### 🎬 Video / Board #{board_index}\n**Sequence Order:** #{board_index} in storyboard\n\n" if board_index is not None else ""
    board_info = f"\nBoard Index: {board_index}" if board_index is not None else ""
    if canonical_uri.startswith("gs://"):
        http_url = _generate_signed_url(canonical_uri)
        return f"{board_header}Video generated successfully!\n\nSaved to GCS: ![{filename}]({canonical_uri})\n\nDownload Video: {http_url}\nAspect Ratio: {aspect_ratio}{board_info}"
    else:
        return f"{board_header}Video generated successfully!\n\nSaved to artifacts: ![{filename}](artifact://{filename}?version={version})\nDownload Video: artifact://{filename}?version={version}\nAspect Ratio: {aspect_ratio}{board_info}"
        
async def video_generation_tool(
    prompt: str,
    edit_previous_video: bool = False,
    video_to_edit: Optional[str] = None,
    task: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    file_uris: Optional[list[str]] = None,
    board_index: Optional[int] = None,
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
        board_index: Optional 1-indexed storyboard board number.
    """
    
    # Map test arguments to new arguments if needed
    if task == "edit" and not edit_previous_video and not video_to_edit:
        if not file_uris:
            edit_previous_video = True
            
    # 1. Collect diagnostics
    diag_parts = [
        f"edit_previous_video={edit_previous_video}",
        f"video_to_edit={video_to_edit}",
        f"board_index={board_index}",
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
        res = await _generate_or_edit_video_impl(prompt, edit_previous_video, video_to_edit, tool_context, task, file_uris, aspect_ratio=aspect_ratio or "16:9", board_index=board_index)
        return f"{res}\n\n---\n**Debug Diagnostics:** `{diag_str}`"
    except Exception as e:
        logger.error("[generate_or_edit_video] Failed: %s", e)
        return f"Error executing tool: {e}\n\n---\n**Debug Diagnostics:** `{diag_str}`"

generate_or_edit_video = FunctionTool(func=video_generation_tool)
