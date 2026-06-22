import os
import sys
from pathlib import Path

import google.auth

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from google.adk.agents import Agent as ADKAgent
from google.adk.apps import App
from google.adk.tools.mcp_tool import MCPTool

load_dotenv()

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

MCP_SERVER_URL = os.getenv(
    "MCP_SERVER_URL",
    "https://renovai-mcp-server-83670173168.us-central1.run.app",
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

mcp_tool = MCPTool(
    server_name="renovai",
    url=MCP_SERVER_URL,
)
root_agent.tools.append(mcp_tool)

app = App(
    root_agent=root_agent,
    name="renovai-capstone",
)

app = App(
    root_agent=root_agent,
    name="app",
)
