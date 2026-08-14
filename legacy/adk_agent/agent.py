import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
from google.adk.agents import Agent as ADKAgent
from google.adk.apps import App
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams

load_dotenv()

if "GOOGLE_CLOUD_PROJECT" not in os.environ:
    try:
        import google.auth
        _, project_id = google.auth.default()
    except Exception:
        project_id = None
    if not project_id:
        try:
            project_id = (
                subprocess.check_output(
                    [r"C:\Users\Edesz\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd", "config", "get", "project"],
                    text=True, timeout=5,
                ).strip()
            )
        except Exception:
            project_id = "project-d065e38c-b25a-4843-973"
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

MCP_SERVER_URL = os.getenv(
    "MCP_SERVER_URL",
    "https://renovai-mcp-server-fxedmdu3vq-uc.a.run.app",
)

root_agent = ADKAgent(
    name="renovai_orchestrator",
    model=os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash"),
    instruction=(
        "You are RenovAI, a Hungarian renovation due-diligence assistant.\n"
        "Your only loyalty is to the apartment buyer.\n"
        "Answer in Hungarian.\n"
        "When you use a tool, always cite the historical quote(s) that "
        "informed your answer.\n"
        "Be honest about data limitations — we have only 30 quotes in "
        "the corpus.\n"
        "If the question spans multiple topics (cost + advice), call "
        "multiple tools and compose a single answer.\n"
        "Never fabricate prices or claims — ground everything in the "
        "tool outputs."
    ),
)

_mcp_toolset: McpToolset | None = None

app = App(
    root_agent=root_agent,
    name="renovai-capstone",
)


async def ensure_mcp_tools():
    global _mcp_toolset
    if _mcp_toolset is None:
        _mcp_toolset = McpToolset(
            connection_params=SseConnectionParams(url=MCP_SERVER_URL),
        )
        root_agent.tools.extend(await _mcp_toolset.get_tools())
