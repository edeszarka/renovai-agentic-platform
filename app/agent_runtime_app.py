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
import logging
import os
from typing import Any

import vertexai
from dotenv import load_dotenv
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.cloud import logging as google_cloud_logging
from vertexai.agent_engines.templates.adk import AdkApp

from app.agent import app as adk_app, ensure_mcp_tools  # type: ignore[import-untyped]
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback

# Load environment variables from .env file at runtime
load_dotenv()

gemini_location = os.environ.get("GOOGLE_CLOUD_LOCATION")
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")


class AgentEngineApp(AdkApp):
    def set_up(self) -> None:
        """Initialize the agent engine app with logging and telemetry."""
        vertexai.init()
        setup_telemetry()
        super().set_up()
        logging.basicConfig(level=logging.INFO)
        try:
            logging_client = google_cloud_logging.Client()
            self.logger = logging_client.logger(__name__)
        except Exception:
            self.logger = logging.getLogger(__name__)
        if gemini_location:
            os.environ["GOOGLE_CLOUD_LOCATION"] = gemini_location
        import asyncio
        asyncio.run(ensure_mcp_tools())

    def register_feedback(self, feedback: dict[str, Any]) -> None:
        """Collect and log feedback."""
        feedback_obj = Feedback.model_validate(feedback)
        self.logger.log_struct(feedback_obj.model_dump(), severity="INFO")

    def register_operations(self) -> dict[str, list[str]]:
        """Registers the operations of the Agent."""
        operations = super().register_operations()
        operations[""] = [*operations.get("", []), "register_feedback"]
        return operations

    def clone(self) -> "AgentEngineApp":
        """Returns a clone of the Agent Runtime application."""
        return self


def _build_agent_runtime() -> AgentEngineApp:
    vertexai.init()
    return AgentEngineApp(
        app=adk_app,
        artifact_service_builder=lambda: (
            GcsArtifactService(bucket_name=logs_bucket_name)
            if logs_bucket_name
            else InMemoryArtifactService()
        ),
    )


_agent_runtime_instance: AgentEngineApp | None = None


def _get_agent_runtime() -> AgentEngineApp:
    global _agent_runtime_instance
    if _agent_runtime_instance is None:
        _agent_runtime_instance = _build_agent_runtime()
    return _agent_runtime_instance


class _LazyAgentRuntime:
    """Proxy that lazily constructs AgentEngineApp on first attribute access."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_get_agent_runtime(), name)

    def __init_subclass__(self, **kwargs):
        pass


agent_runtime: Any = _LazyAgentRuntime()
