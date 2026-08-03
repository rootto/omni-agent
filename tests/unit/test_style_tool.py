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

"""Unit tests for Style Gate, Memory Bank Suggestions, and Clean-Text Guardrail Injection (CUJ-9)."""

import pytest
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.sessions import Session

from app.tools.style_tool import (
    save_style_to_memory,
    get_saved_styles_from_memory,
    format_style_prompt,
    get_clean_text_guardrail,
    get_saved_styles_tool,
    save_style_tool,
    set_active_style_tool,
    clear_active_style_tool,
)


class DummyMemoryToolContext:
    def __init__(self, user_id: str = "test_user"):
        self._memory_service = InMemoryMemoryService()
        self._session = Session(id="session_1", user_id=user_id, app_name="test_app")

    @property
    def session(self):
        return self._session

    @property
    def user_id(self):
        return self._session.user_id

    async def add_memory(self, *, memories, custom_metadata=None):
        return await self._memory_service.add_memory(
            app_name="test_app",
            user_id=self.user_id,
            memories=memories,
            custom_metadata=custom_metadata,
        )

    async def add_events_to_memory(self, *, events, session_id=None, custom_metadata=None):
        return await self._memory_service.add_events_to_memory(
            app_name="test_app",
            user_id=self.user_id,
            events=events,
            session_id=session_id,
            custom_metadata=custom_metadata,
        )

    async def search_memory(self, query: str):
        return await self._memory_service.search_memory(
            app_name="test_app",
            user_id=self.user_id,
            query=query,
        )


@pytest.mark.asyncio
async def test_save_and_get_saved_styles_limit_and_deduplication() -> None:
    """Verifies storing styles in Memory Bank, retrieving up to the latest 3 styles, and deduplicating by style name."""
    ctx = DummyMemoryToolContext()

    # Save 4 styles in sequence
    await save_style_to_memory(ctx, "Style One", "# One\nRed and black")
    await save_style_to_memory(ctx, "Style Two", "# Two\nGreen and gold")
    await save_style_to_memory(ctx, "Style Three", "# Three\nBlue and white")
    await save_style_to_memory(ctx, "Style Four", "# Four\nPurple and silver")

    # Get saved styles (should return at most 3 latest styles)
    styles = await get_saved_styles_from_memory(ctx, limit=3)
    assert len(styles) == 3
    names = [s["name"] for s in styles]
    assert "Style Four" in names
    assert "Style Three" in names
    assert "Style Two" in names
    assert "Style One" not in names


@pytest.mark.asyncio
async def test_style_tools_session_state_persistence() -> None:
    """Verifies save_style_tool, set_active_style_tool, get_saved_styles_tool, and clear_active_style_tool."""
    ctx = DummyMemoryToolContext()

    # Save a style using save_style_tool
    save_res = await save_style_tool(
        name="Google Branding",
        markdown="# Google Brand Style\nUse primary Google colors.",
        tool_context=ctx,
    )
    assert save_res["status"] == "saved"
    assert ctx.session.state.get("active_style_name") == "Google Branding"
    assert ctx.session.state.get("active_style_markdown") == "# Google Brand Style\nUse primary Google colors."

    # Check saved styles in memory
    get_res = await get_saved_styles_tool(tool_context=ctx)
    saved_list = get_res.get("saved_styles", [])
    assert len(saved_list) == 1
    assert saved_list[0]["name"] == "Google Branding"
    assert saved_list[0]["markdown"] == "# Google Brand Style\nUse primary Google colors."

    # Clear active style
    clear_res = clear_active_style_tool(tool_context=ctx)
    assert clear_res["status"] == "cleared"
    assert ctx.session.state.get("active_style_name") is None
    assert ctx.session.state.get("active_style_markdown") is None

    # Set active style directly
    set_res = set_active_style_tool(
        name="Minimalist",
        markdown="# Minimalist Style\nMonochrome only.",
        tool_context=ctx,
    )
    assert set_res["status"] == "active"
    assert ctx.session.state.get("active_style_name") == "Minimalist"
    assert ctx.session.state.get("active_style_markdown") == "# Minimalist Style\nMonochrome only."


def test_format_style_prompt_and_clean_text_guardrail() -> None:
    """Verifies clean-text guardrail block and prompt formatting with/without active style."""
    guardrail = get_clean_text_guardrail()
    assert "The ONLY text that should appear anywhere in this video is what's specified in the storyboard." in guardrail
    assert "Do not render any style guidelines, color names, hex codes, RGB values, or instructional metadata as text in the video itself." in guardrail

    base_prompt = "A drone flying over snow-capped mountains"

    # Without style
    unmodified = format_style_prompt(base_prompt, None)
    assert unmodified == base_prompt

    # With style
    style_md = "# Cinematic Style\nTeal and orange color grade."
    formatted = format_style_prompt(base_prompt, style_md)
    assert base_prompt in formatted
    assert "Overall style: # Cinematic Style\nTeal and orange color grade." in formatted
    assert formatted.endswith(guardrail)


@pytest.mark.asyncio
async def test_save_style_to_memory_truncation_and_error_handling() -> None:
    """Verifies that large Markdown styles are capped to prevent 400 INVALID_ARGUMENT (Fact length > 2048 chars)."""
    ctx = DummyMemoryToolContext()

    long_markdown = "A" * 3000
    await save_style_to_memory(ctx, "Long Style", long_markdown)

    res = await ctx._memory_service.search_memory(app_name="test_app", user_id="test_user", query="Style Name:")
    memories = list(getattr(res, "memories", []))
    assert len(memories) > 0
    saved_text = memories[0].content.parts[0].text
    assert len(saved_text) <= 2048, "Memory Bank fact length must never exceed 2048 characters!"
    assert "..." in saved_text

