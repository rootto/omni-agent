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

"""Plugin to resolve FileData and artifact uploads into session state mappings."""

import logging
from typing import Optional
from google.adk.plugins import BasePlugin
from google.adk.agents.invocation_context import InvocationContext
from google.genai import types

logger = logging.getLogger(__name__)


class FileDataResolverPlugin(BasePlugin):
    """Intercepts uploaded FileData parts in user messages and stores display_name -> file_uri mappings in session state."""

    def __init__(self) -> None:
        super().__init__(name="file_data_resolver")

    async def on_user_message_callback(
        self,
        *,
        invocation_context: InvocationContext,
        user_message: types.Content,
    ) -> Optional[types.Content]:
        if not invocation_context or not invocation_context.session:
            return None

        state = invocation_context.session.state
        mappings = state.get("file_data_mappings", {})

        if not user_message or not getattr(user_message, "parts", None):
            return None

        for part in user_message.parts:
            # Check for FileData
            file_data = getattr(part, "file_data", None)
            if file_data and getattr(file_data, "file_uri", None):
                uri = file_data.file_uri
                display_name = getattr(file_data, "display_name", None) or uri.split("/")[-1]
                mappings[display_name] = uri
                logger.info("FileDataResolverPlugin: Mapped %s -> %s", display_name, uri)
            # Check for artifact references or other file attachments if present
            elif hasattr(part, "file_ref") and part.file_ref:
                uri = getattr(part.file_ref, "uri", None) or str(part.file_ref)
                display_name = getattr(part.file_ref, "display_name", None) or uri.split("/")[-1]
                mappings[display_name] = uri
                logger.info("FileDataResolverPlugin: Mapped file_ref %s -> %s", display_name, uri)

        state["file_data_mappings"] = mappings
        return None
