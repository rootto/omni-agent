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

"""Video generation and editing tool powered by Gemini Omni (gemini-omni-flash-preview)."""

import asyncio
import base64
import datetime
import logging
import os
import shutil
import tempfile
import uuid
from typing import Any, Optional
import urllib.parse

import google.auth
from google import genai
from google.genai import types
from google.cloud import storage
from google.adk.tools import ToolContext

from .. import config

logger = logging.getLogger(__name__)





async def ensure_local_file_path(file_ref: str, tool_context: ToolContext, client) -> str:
    """Resolves GCS, Files API, or artifact references to a local file path."""
    if file_ref.startswith("file://"):
        file_ref = file_ref.replace("file://", "", 1)

    if os.path.exists(file_ref):
        return file_ref

    if file_ref.startswith("gs://"):
        parsed = urllib.parse.urlparse(file_ref)
        bucket_name = parsed.netloc
        blob_name = parsed.path.lstrip('/')
        
        # Check if it is a local path masqueraded as gs:// (useful for testing or local run)
        if bucket_name == "local":
            return "/" + blob_name
            
        temp_dir = tempfile.gettempdir()
        local_path = os.path.join(temp_dir, os.path.basename(blob_name))
        
        logger.info("Downloading %s to local path %s", file_ref, local_path)
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        await asyncio.to_thread(blob.download_to_filename, local_path)
        return local_path

    if file_ref.startswith("files/"):
        basename = file_ref.split("/")[-1]
        try:
            temp_dir = tempfile.gettempdir()
            local_path = os.path.join(temp_dir, basename)
            logger.info("Downloading Files API file %s to %s", file_ref, local_path)
            content = await asyncio.to_thread(client.files.download, file=file_ref)
            with open(local_path, "wb") as f:
                f.write(content)
            return local_path
        except Exception as e:
            logger.warning("Files API download failed for %s: %s. Falling back to artifact resolution for %s.", file_ref, e, basename)
            try:
                artifact_version = await tool_context.get_artifact_version(basename)
                canonical_uri = artifact_version.canonical_uri
                return await ensure_local_file_path(canonical_uri, tool_context, client)
            except Exception as ae:
                raise ValueError(f"Could not resolve Files API reference or artifact: {file_ref}") from ae

    try:
        artifact_version = await tool_context.get_artifact_version(file_ref)
        canonical_uri = artifact_version.canonical_uri
        return await ensure_local_file_path(canonical_uri, tool_context, client)
    except Exception as e:
        basename = file_ref.split("/")[-1]
        try:
            artifact_version = await tool_context.get_artifact_version(basename)
            canonical_uri = artifact_version.canonical_uri
            return await ensure_local_file_path(canonical_uri, tool_context, client)
        except Exception as ae:
            raise ValueError(f"Could not resolve file reference: {file_ref}") from ae



async def video_generation_tool(
    prompt: str,
    task: str,
    aspect_ratio: str = "16:9",
    file_uris: list[str] | None = None,
    tool_context: ToolContext | None = None,
) -> str:
    """Generates or edits a video using Gemini Omni (gemini-omni-flash-preview) via Google GenAI Interactions API.

    Args:
        prompt: Detailed natural language description or edit instruction for the video.
        task: Type of task: "text_to_video", "image_to_video", "reference_to_video", or "edit".
        aspect_ratio: Aspect ratio for generation ("16:9" default landscape or "9:16" portrait).
        file_uris: Optional list of reference or input file names (saved as artifacts).
        tool_context: ADK runtime tool context for session state and Interaction ID persistence.

    Returns:
        Dual-link markdown containing an inline gs:// video player and direct HTTPS download URL,
        or a structured safety rejection message if blocked by safety policies.
    """
    logger.info(
        "[video_generation_tool] prompt=%s, task=%s, aspect_ratio=%s, file_uris=%s",
        prompt,
        task,
        aspect_ratio,
        file_uris,
    )

    # Force Vertex AI Enterprise usage
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
            timeout=600 * 1000,  # 10 minutes
        )
    )
    omni_model = config.get_omni_model_id()

    # Handle multi-turn stateful edits mapping
    edit_previous_video = False
    if task == "edit" and (not file_uris):
        edit_previous_video = True
        logger.info("[video_generation_tool] Stateful edit: editing previous video in this session.")

    # 1. Resolve file references
    local_file_path = None
    file_ref = None
    file_mime_type = None

    if file_uris:
        # We assume only one input file for now (first in the list)
        file_ref = file_uris[0]

    # Resolve mime_type from artifact service if available
    if file_ref and tool_context:
        try:
            artifact_part = await tool_context.load_artifact(file_ref)
            if artifact_part:
                if artifact_part.inline_data:
                    file_mime_type = artifact_part.inline_data.mime_type
                elif artifact_part.file_data:
                    file_mime_type = artifact_part.file_data.mime_type
                logger.info("[video_generation_tool] Resolved mime_type from artifact: %s", file_mime_type)
        except Exception as e:
            logger.info("Could not load artifact to get mime_type: %s", e)

    # Check if previously generated video is large
    if edit_previous_video:
        try:
            prev_local_path = await ensure_local_file_path("generated_video.mp4", tool_context, client)
            local_file_path = prev_local_path
        except Exception as e:
            logger.warning("Could not resolve previous video: %s", e)

    # Resolve input video
    if file_ref:
        local_file_path = await ensure_local_file_path(file_ref, tool_context, client)

    # Discard previous interaction state if this is a new video or edit on a newly uploaded video
    if not edit_previous_video:
        if tool_context:
            if getattr(tool_context, "session", None) and getattr(tool_context.session, "state", None) is not None:
                tool_context.session.state["previous_interaction_id"] = None
            elif getattr(tool_context, "state", None) is not None:
                tool_context.state["previous_interaction_id"] = None


    previous_interaction_id = None
    if edit_previous_video and tool_context:
        if getattr(tool_context, "session", None) and getattr(tool_context.session, "state", None) is not None:
            previous_interaction_id = tool_context.session.state.get("previous_interaction_id")
        elif getattr(tool_context, "state", None) is not None:
            previous_interaction_id = tool_context.state.get("previous_interaction_id")
        
        if not previous_interaction_id:
            logger.warning("[video_generation_tool] edit_previous_video=True but no previous_interaction_id found. Falling back to new video.")
            edit_previous_video = False

    generation_config = None
    if not edit_previous_video:
        generation_config = {
            "video_config": {
                "task": task,
                "aspect_ratio": aspect_ratio,
            }
        }

    if edit_previous_video and previous_interaction_id:
        input_data = prompt
    else:
        if file_ref:
            # Use resolved file_mime_type if found, otherwise guess
            mime_type = file_mime_type
            if not mime_type:
                mime_type = "application/octet-stream"
                if local_file_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    mime_type = "image/png"
                    if local_file_path.lower().endswith((".jpg", ".jpeg")):
                        mime_type = "image/jpeg"
                    elif local_file_path.lower().endswith(".webp"):
                        mime_type = "image/webp"
                elif local_file_path.lower().endswith((".mp4", ".mov", ".webm", ".avi")):
                    mime_type = "video/mp4"
                    if local_file_path.lower().endswith(".mov"):
                        mime_type = "video/mov"
                    elif local_file_path.lower().endswith(".webm"):
                        mime_type = "video/webm"

            # Determine file_type (image/video) based on mime_type
            file_type = "video"
            if mime_type.startswith("image/"):
                file_type = "image"
            elif mime_type.startswith("video/"):
                file_type = "video"

            logger.info("[video_generation_tool] Constructing inline payload: type=%s, mime_type=%s", file_type, mime_type)

            with open(local_file_path, "rb") as f:
                file_bytes = f.read()
            base64_data = base64.b64encode(file_bytes).decode("utf-8")
            file_input = {"type": file_type, "data": base64_data, "mime_type": mime_type}
            input_data = [
                file_input,
                {"type": "text", "text": prompt}
            ]
        else:
            input_data = prompt

    # Invoke interactions API
    try:
        kwargs = {
            "model": omni_model,
            "input": input_data,
            "generation_config": generation_config,
        }
        if edit_previous_video and previous_interaction_id:
            kwargs["previous_interaction_id"] = previous_interaction_id
            
        interaction = client.interactions.create(**kwargs)
    except Exception as e:
        err_msg = str(e)
        if "The prompt could not be processed" in err_msg or "SAFETY" in err_msg.upper():
            logger.error("[video_generation_tool] Video generation blocked by safety/policy filters: %s", e)
            return (
                "Error: The video generation model blocked your request due to safety/policy filters.\n\n"
                "### Why did this happen?\n"
                "Creative video generation models have strict guardrails regarding deepfakes and sensitive healthcare content. "
                "This issue was likely triggered by one of the following:\n"
                "1. **Deepfake Prevention Policy**: Requests containing terms like 'anchor', 'host', 'presenter', 'speaker', or 'person' (asking the model to generate a human being speaking).\n"
                "2. **Sensitive Medical Content Filters**: Direct usage of technical medical terms (like 'cancer', 'oncology', 'ovarian', or 'tumor') in the visual generation description.\n\n"
                "### How to fix it (Human-in-the-Loop):\n"
                "Please update your request message to:\n"
                "- Avoid requesting human presenters or anchors. Instead, ask for **abstract design styles**, **kinetic typography**, **clean sliding transitions**, **data infographics**, or **motion graphics**.\n"
                "- Replace technical medical terms in the visual description with neutral placeholder metaphors (e.g. 'therapeutic access' instead of 'oncology/cancer funding', 'specialized diagnostic cohort' instead of 'ovarian cancer screening')."
            )
        else:
            raise e

    # Extract model output video part
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
                    file_data = part.get("file_data") or part.get("fileData")
                    if inline_data and inline_data.get("mime_type", "").startswith("video/"):
                        raw_data = inline_data.get("data")
                        if isinstance(raw_data, str):
                            video_bytes = base64.b64decode(raw_data)
                        else:
                            video_bytes = raw_data
                        break
                    elif file_data:
                        file_uri = file_data.get("file_uri") or file_data.get("fileUri")
                        if file_uri:
                            try:
                                local_output_path = await ensure_local_file_path(file_uri, tool_context, client)
                                with open(local_output_path, "rb") as f:
                                    video_bytes = f.read()
                                break
                            except Exception as dl_err:
                                logger.warning("Failed to download file_uri %s: %s", file_uri, dl_err)
                else:
                    inline_data = getattr(part, "inline_data", None)
                    if (
                        inline_data
                        and inline_data.mime_type
                        and inline_data.mime_type.startswith("video/")
                    ):
                        raw_data = inline_data.data
                        if isinstance(raw_data, str):
                            video_bytes = base64.b64decode(raw_data)
                        else:
                            video_bytes = raw_data
                        break
                    
                    file_data = getattr(part, "file_data", None)
                    if file_data and getattr(file_data, "file_uri", None):
                        try:
                            local_output_path = await ensure_local_file_path(file_data.file_uri, tool_context, client)
                            with open(local_output_path, "rb") as f:
                                video_bytes = f.read()
                            break
                        except Exception as dl_err:
                            logger.warning("Failed to download file_uri %s: %s", file_data.file_uri, dl_err)
            if video_bytes:
                break

    if not video_bytes:
        raise ValueError("No video was generated or returned by the interactions model.")

    # Save video as an artifact
    filename = f"generated_video_{uuid.uuid4().hex[:8]}.mp4"
    video_part = types.Part(
        inline_data=types.Blob(
            mime_type="video/mp4",
            data=video_bytes
        )
    )
    version = await tool_context.save_artifact(filename=filename, artifact=video_part)

    # Load previous steps
    prev_steps = []
    if edit_previous_video and steps:
        prev_steps = list(steps)

    # Save interaction ID and serialized steps to the session state
    serialized_steps = list(prev_steps)
    for step in interaction.steps:
        if hasattr(step, "model_dump"):
            serialized_steps.append(step.model_dump())
        else:
            serialized_steps.append(step)

    if tool_context:
        if getattr(tool_context, "session", None) and getattr(tool_context.session, "state", None) is not None:
            tool_context.session.state["previous_interaction_id"] = interaction.id
            tool_context.session.state["previous_interaction_steps"] = serialized_steps
        elif getattr(tool_context, "state", None) is not None:
            tool_context.state["previous_interaction_id"] = interaction.id
            tool_context.state["previous_interaction_steps"] = serialized_steps
            
    logger.info("[video_generation_tool] Saved to tool_context.state: previous_interaction_id=%s, count(previous_interaction_steps)=%d", interaction.id, len(serialized_steps))

    # Retrieve the canonical URI of the saved artifact for logging
    artifact_version = await tool_context.get_artifact_version(filename, version=version)
    canonical_uri = artifact_version.canonical_uri
    logger.info("Artifact saved to canonical URI: %s", canonical_uri)

    signed_url = None
    if canonical_uri.startswith("gs://"):
        try:
            parsed = urllib.parse.urlparse(canonical_uri)
            bucket_name = parsed.netloc
            blob_name = parsed.path.lstrip('/')
            
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            
            credentials, _ = google.auth.default()
            
            # Compute Engine credentials cannot sign locally. We must use the IAM API via impersonated credentials.
            if not hasattr(credentials, "sign_bytes") and hasattr(credentials, "service_account_email") and credentials.service_account_email != "default":
                from google.auth import impersonated_credentials
                logger.info("Wrapping credentials with IAM ImpersonatedCredentials for signing.")
                credentials = impersonated_credentials.Credentials(
                    source_credentials=credentials,
                    target_principal=credentials.service_account_email,
                    target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
                    lifetime=7200
                )
            
            try:
                signed_url = blob.generate_signed_url(
                    expiration=datetime.timedelta(hours=2),
                    version="v4",
                    credentials=credentials
                )
            except Exception as e:
                logger.warning("Could not generate signed URL with default credentials: %s", e)
                # Fallback to direct download URI (often requires auth)
                signed_url = f"https://storage.cloud.google.com/{bucket_name}/{blob_name}"
        except Exception as e:
            logger.error("Failed to parse and sign canonical URI %s: %s", canonical_uri, e)

    video_link = f"![Generated Video]({signed_url})" if signed_url else f"![{filename}](artifact://{filename}?version={version})"
    dl_link = f"[Download Video]({signed_url if signed_url else canonical_uri})"

    return (
        f"Video generated successfully (Task: {task}, Aspect Ratio: {aspect_ratio}).\n\n"
        f"Saved to artifacts:\n"
        f"{video_link}\n\n"
        f"{dl_link}"
    )
