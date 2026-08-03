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

"""Style Gate, Memory Bank Suggestions, and Clean-Text Guardrail Injection tools for Omni-Agent (CUJ-9)."""

import logging
from typing import Optional, List, Dict, Any
from google.adk.tools import ToolContext
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.events import Event
from google.genai import types

logger = logging.getLogger(__name__)


def get_clean_text_guardrail() -> str:
    """Returns the mandatory clean-text guardrail block that prevents style instructions from rendering as text on screen."""
    return (
        "Important:\n"
        "The ONLY text that should appear anywhere in this video is what's specified in the storyboard.\n"
        "Do not render any style guidelines, color names, hex codes, RGB values, or instructional metadata as text in the video itself. Keep all graphic elements and callout boxes completely clean of any technical prompts."
    )


def format_style_prompt(prompt: str, active_style_markdown: Optional[str]) -> str:
    """Appends active style markdown and clean-text guardrail to the prompt if a style is active."""
    if active_style_markdown and isinstance(active_style_markdown, str) and active_style_markdown.strip():
        return (
            f"{prompt.rstrip()}\n\n"
            f"Overall style: {active_style_markdown.strip()}\n\n"
            f"{get_clean_text_guardrail()}"
        )
    return prompt


def _parse_style_memory_text(text: str) -> tuple[str, str]:
    """Parses style name and markdown from saved memory entry text."""
    lines = text.split("\n")
    name = ""
    markdown_lines = []
    in_markdown = False
    for line in lines:
        if line.startswith("Style Name:") and not in_markdown:
            name = line.split("Style Name:", 1)[1].strip()
        elif line.startswith("Style Markdown:"):
            in_markdown = True
        elif in_markdown:
            markdown_lines.append(line)
    markdown = "\n".join(markdown_lines).strip()
    return name, markdown


async def save_style_to_memory(
    tool_context: ToolContext,
    name: str,
    markdown: str,
) -> None:
    """Saves a named visual style to Vertex AI Agent Engine Memory Bank."""
    if not tool_context:
        logger.warning("[save_style_to_memory] No tool_context provided.")
        return

    text = f"Style Name: {name}\nStyle Markdown:\n{markdown}"
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=text)],
    )
    custom_metadata = {"type": "video_style", "style_name": name}
    entry = MemoryEntry(content=content, custom_metadata=custom_metadata)

    try:
        await tool_context.add_memory(
            memories=[entry],
            custom_metadata=custom_metadata,
        )
        logger.info("[save_style_to_memory] Saved style '%s' via add_memory.", name)
    except NotImplementedError:
        logger.info("[save_style_to_memory] Memory service fallback: saving via add_events_to_memory.")
        event = Event(author="user", content=content)
        await tool_context.add_events_to_memory(
            events=[event],
            custom_metadata=custom_metadata,
        )


async def get_saved_styles_from_memory(
    tool_context: ToolContext,
    limit: int = 3,
) -> List[Dict[str, str]]:
    """Retrieves up to the `limit` most recently saved unique visual styles from Memory Bank."""
    if not tool_context:
        return []

    try:
        res = await tool_context.search_memory(query="Style Name:")
    except Exception as e:
        logger.warning("[get_saved_styles_from_memory] search_memory failed: %s", e)
        return []

    saved_styles: List[Dict[str, str]] = []
    seen_names = set()

    memories = list(getattr(res, "memories", []))
    for m in reversed(memories):
        if not (hasattr(m, "content") and m.content and getattr(m.content, "parts", None)):
            continue

        text = ""
        for part in m.content.parts:
            if getattr(part, "text", None):
                text += part.text + "\n"

        name, markdown = _parse_style_memory_text(text.strip())
        if hasattr(m, "custom_metadata") and isinstance(m.custom_metadata, dict):
            meta_name = m.custom_metadata.get("style_name")
            if meta_name and isinstance(meta_name, str):
                name = meta_name.strip()

        if name and markdown and name not in seen_names:
            seen_names.add(name)
            saved_styles.append({"name": name, "markdown": markdown})
            if len(saved_styles) >= limit:
                break

    return saved_styles


async def get_saved_styles_tool(tool_context: ToolContext = None) -> dict:
    """Retrieves up to the 3 most recently saved video visual styles from Vertex AI Memory Bank.

    Call this before creating a video or storyboard to check if any specific styling has been added in previous sessions.
    """
    styles = await get_saved_styles_from_memory(tool_context, limit=3)
    return {"saved_styles": styles}


async def save_style_tool(
    name: str,
    markdown: str,
    tool_context: ToolContext = None,
) -> dict:
    """Saves a user-provided Markdown style to Vertex AI Memory Bank under the given name and sets it as active for the session.

    Args:
        name: Concise name for the visual style (e.g. 'Google Branding', 'Cinematic Noir').
        markdown: Full Markdown style guidelines to apply.
    """
    await save_style_to_memory(tool_context, name, markdown)
    if tool_context:
        if hasattr(tool_context, "state"):
            tool_context.state["active_style_name"] = name
            tool_context.state["active_style_markdown"] = markdown
        if tool_context.session and tool_context.session.state is not None:
            tool_context.session.state["active_style_name"] = name
            tool_context.session.state["active_style_markdown"] = markdown

    return {
        "status": "saved",
        "name": name,
        "message": f"Saved style '{name}' to Memory Bank and set as active style for this session.",
    }


def set_active_style_tool(
    name: str,
    markdown: str,
    tool_context: ToolContext = None,
) -> dict:
    """Sets an existing style as the active style for the current session without saving a duplicate to Memory Bank.

    Args:
        name: Name of the visual style.
        markdown: Full Markdown style guidelines.
    """
    if tool_context:
        if hasattr(tool_context, "state"):
            tool_context.state["active_style_name"] = name
            tool_context.state["active_style_markdown"] = markdown
        if tool_context.session and tool_context.session.state is not None:
            tool_context.session.state["active_style_name"] = name
            tool_context.session.state["active_style_markdown"] = markdown

    return {
        "status": "active",
        "name": name,
        "message": f"Set active style to '{name}' for this session.",
    }


def clear_active_style_tool(tool_context: ToolContext = None) -> dict:
    """Clears any active visual style from the current session state.

    Call this if the user says 'no style' or wants to remove the applied style.
    """
    if tool_context:
        if hasattr(tool_context, "state"):
            tool_context.state["active_style_name"] = ""
            tool_context.state["active_style_markdown"] = ""
        if tool_context.session and tool_context.session.state is not None:
            tool_context.session.state["active_style_name"] = None
            tool_context.session.state["active_style_markdown"] = None

    return {
        "status": "cleared",
        "message": "Cleared active style for this session.",
    }
