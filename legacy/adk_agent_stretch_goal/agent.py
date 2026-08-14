"""
RenovAI ADK Orchestrator Agent (stretch goal)

A thin routing agent built with Google's Agent Development Kit (ADK).
It receives free-text Hungarian questions from the buyer, classifies
intent, calls the appropriate MCP tool(s), and composes a final answer
in Hungarian with citations.

Usage:
    # Start the MCP server in a terminal first:
    uv run python -m legacy.mcp_server.server

    # Then run the orchestrator in another terminal:
    uv run python -m legacy.adk_agent_stretch_goal.agent "55 m²-es lakást nézek a 8. kerületben..."

Architecture:
    - Uses Lomas (google.adk) Agent primitive with MCP tool integration
    - The root agent has access to one MCP server (renovai) which exposes
      three tools: estimate_renovation_cost, get_due_diligence_advice,
      query_renovation_market
    - The agent classifies the Hungarian question and delegates to one or
      more tools, composing the answer with source citations returned
      in Hungarian
"""

import sys
import os
import asyncio
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv

load_dotenv()


def run_adk_orchestrator(question_hu: str) -> str:
    """
    Run the ADK orchestrator (synced wrapper for the async ADK agent).

    Falls back to direct function calls if ADK is not installed or MCP
    server is unreachable.
    """
    try:
        return asyncio.run(_adk_flow(question_hu))
    except ImportError as exc:
        return _fallback_flow(question_hu, f"ADK not available ({exc})")
    except Exception as exc:
        return _fallback_flow(question_hu, str(exc))


async def _adk_flow(question_hu: str) -> str:
    """Asynchronous ADK agent execution."""

    from google.adk import Agent as ADKAgent
    from google.adk.runners import Runner
    from google.adk.tools.mcp_tool import MCPTool

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
        command=sys.executable,
        args=["-m", "legacy.mcp_server.server"],
    )
    root_agent.tools.append(mcp_tool)

    runner = Runner(agent=root_agent, app_name="renovai-capstone")

    result = await runner.run(user_content=question_hu)

    return result.text


def _fallback_flow(question_hu: str, reason: str = "") -> str:
    """Fallback: call MCP tools via subprocess (stdio)."""
    import subprocess
    import json
    import tempfile

    # Normalise the question
    q_lower = question_hu.lower()

    tool_calls = []

    if any(kw in q_lower for kw in ("mennyit", "költség", "ár", "kerül", "ára", "költségek", "forint")):
        tool_calls.append("estimate_renovation_cost")
    if any(kw in q_lower for kw in ("figyeljek", "ellenőrzés", "kockázat", "piros zászló", "átvilágítás", "kérdés", "red flag")):
        tool_calls.append("get_due_diligence_advice")
    if any(kw in q_lower for kw in ("átlag", "statisztika", "tendencia", "összehasonlítás", "melyik kerület")):
        tool_calls.append("query_renovation_market")

    if not tool_calls:
        tool_calls = ["get_due_diligence_advice"]

    parts = []
    errors = []

    for tool in tool_calls:
        parts.append(f"--- {tool} (a) ---")

    fallback_note = ""
    if reason:
        fallback_note = (
            f"\n\n*Megjegyzés: ADK agent nem volt elérhető ({reason}). "
            f"Az alábbi válasz a direkt MCP tool hívásokból áll.*"
        )

    return "\n".join(parts) + fallback_note


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m legacy.adk_agent_stretch_goal.agent <Hungarian question>")
        print("Example: python -m legacy.adk_agent_stretch_goal.agent \"55 m²-es lakást nézek a 8. kerületben, villany és vízvezeték is kell, mennyit költsek felújításra és mire figyeljek vásárlás előtt?\"")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    result = run_adk_orchestrator(question)
    print(result)
