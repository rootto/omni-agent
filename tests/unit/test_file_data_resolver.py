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

"""Unit tests for FileDataResolverPlugin (CUJ-2, CUJ-4 file resolution mapping)."""

import pytest
from unittest.mock import MagicMock
from google.genai import types

from app.plugins import FileDataResolverPlugin


class DummySession:
    def __init__(self):
        self.state = {}


class DummyInvocationContext:
    def __init__(self):
        self.session = DummySession()


@pytest.mark.asyncio
async def test_file_data_resolver_plugin_maps_file_data() -> None:
    """Verifies that FileDataResolverPlugin maps uploaded FileData parts into session state."""
    plugin = FileDataResolverPlugin()
    assert plugin.name == "file_data_resolver"

    context = DummyInvocationContext()

    file_data_obj = MagicMock()
    file_data_obj.file_uri = "gs://mock-bucket/uploads/sample_image.png"
    file_data_obj.display_name = "sample_image.png"

    part = MagicMock()
    part.file_data = file_data_obj
    part.file_ref = None

    user_message = MagicMock()
    user_message.parts = [part]

    await plugin.on_user_message_callback(
        invocation_context=context,
        user_message=user_message,
    )

    mappings = context.session.state.get("file_data_mappings")
    assert mappings is not None
    assert mappings["sample_image.png"] == "gs://mock-bucket/uploads/sample_image.png"
