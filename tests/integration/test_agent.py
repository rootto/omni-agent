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

import app.config  # Ensure .env is loaded first
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.genai import types

from app.agent import root_agent


def test_agent_stream() -> None:
    """
    Integration test for the agent stream functionality.
    Tests that the agent returns valid streaming responses.
    """

    session_service = InMemorySessionService()

    session = session_service.create_session_sync(user_id="test_user", app_name="test")
    runner = Runner(agent=root_agent, session_service=session_service, app_name="test")

    message = types.Content(
        role="user", parts=[types.Part.from_text(text="Why is the sky blue?")]
    )

    events = list(
        runner.run(
            new_message=message,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )
    assert len(events) > 0, "Expected at least one message"

    has_text_content = False
    for event in events:
        if (
            event.content
            and event.content.parts
            and any(part.text for part in event.content.parts)
        ):
            has_text_content = True
            break
    assert has_text_content, "Expected at least one message with text content"


def test_agent_style_gate_multi_turn() -> None:
    """Verifies multi-turn stateful Style Gate and style persistence across turns."""
    session_service = InMemorySessionService()
    memory_service = InMemoryMemoryService()

    session = session_service.create_session_sync(user_id="test_user", app_name="omni_agent")
    runner = Runner(
        agent=root_agent,
        session_service=session_service,
        memory_service=memory_service,
        app_name="omni_agent",
    )

    # Turn 1: Request video generation -> agent triggers Style Gate
    message_turn1 = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Create a video of a futuristic city")],
    )
    events_turn1 = list(
        runner.run(
            new_message=message_turn1,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )
    assert len(events_turn1) > 0

    # Turn 2: User provides Markdown style guidelines
    message_turn2 = types.Content(
        role="user",
        parts=[types.Part.from_text(text="Save this style as Google Branding:\n# Google Brand Style\nPrimary blue and yellow.")],
    )
    events_turn2 = list(
        runner.run(
            new_message=message_turn2,
            user_id="test_user",
            session_id=session.id,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        )
    )
    assert len(events_turn2) > 0

    # Verify session state has active style preserved
    updated_session = session_service.get_session_sync(
        app_name="omni_agent", user_id="test_user", session_id=session.id
    )
    assert updated_session is not None
    assert updated_session.state.get("active_style_name") == "Google Branding"
    assert "Google Brand Style" in updated_session.state.get("active_style_markdown", "")

