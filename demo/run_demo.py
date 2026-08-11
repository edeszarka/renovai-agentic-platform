"""
RenovAI Capstone Demo Script

Starts the MCP server, sends the two example queries from the spec's
success criteria, and prints the responses.

Usage:
    uv run python -m demo.run_demo

Requirements:
    - .env file with GOOGLE_API_KEY set
    - Data pipeline already run (chroma_db, data/models, data/renovai.db exist)
    - mcp package installed (uv sync)
"""

import sys
import json
import subprocess
import time
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()


def _recv_line(fd) -> str:
    """Read one complete JSON-RPC line from the subprocess stdout."""
    while True:
        line = fd.readline()
        if not line:
            raise ConnectionError("MCP server closed connection")
        stripped = line.strip()
        if stripped:
            return stripped


class MCPClient:
    """Minimal MCP stdio client that performs the full init handshake."""

    def __init__(self):
        self._req_id = 0
        self._proc: subprocess.Popen | None = None

    def __enter__(self):
        server_path = str(Path(__file__).resolve().parent.parent / "legacy" / "mcp_server" / "server.py")
        self._proc = subprocess.Popen(
            [sys.executable, server_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._init_handshake()
        return self

    def __exit__(self, *args):
        if self._proc:
            self._proc.terminate()
            self._proc.wait(timeout=5)

    def _send(self, method: str, params: dict = None) -> dict:
        self._req_id += 1
        request = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
        if params is not None:
            request["params"] = params
        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()
        response = json.loads(_recv_line(self._proc.stdout))
        if "error" in response:
            raise RuntimeError(f"MCP error ({response['error']['code']}): {response['error']['message']}")
        return response["result"]

    def _init_handshake(self):
        time.sleep(0.5)
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "renovai-demo", "version": "1.0"},
        })

    def list_tools(self) -> list[dict]:
        result = self._send("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            return json.loads(content[0]["text"])
        return result


def main():
    print("=" * 70)
    print("RenovAI Capstone Demo")
    print("Agents for Good — Hungarian Renovation Due-Diligence Agent")
    print("=" * 70)

    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        print("\nWARNING: GOOGLE_API_KEY not set. Tools that require Gemini will fail.\n")

    with MCPClient() as mcp:
        tools = mcp.list_tools()
        print(f"\nConnected to RenovAI MCP server — {len(tools)} tools available:")
        for t in tools:
            print(f"  - {t['name']}")

        # ── Query 1a: Estimate renovation cost ──
        print("\n" + "─" * 70)
        print('QUERY 1a: "55 m²-es lakást nézek a 8. kerületben,')
        print('         villany és vízvezeték is kell, mennyit költsek felújításra?"')
        print("─" * 70)
        print("\n[1/3] Calling estimate_renovation_cost...")
        cost_result = mcp.call_tool("estimate_renovation_cost", {
            "district": 8, "area_sqm": 55, "num_rooms": 2, "scope": ["villany", "viz_futes"],
        })
        if "error" in cost_result:
            print(f"  ERROR: {cost_result['error']}")
        else:
            print(f"  Cost range (HUF):")
            print(f"     Low:  {cost_result.get('low_huf', 'N/A'):>12,} Ft")
            print(f"     Mid:  {cost_result.get('mid_huf', 'N/A'):>12,} Ft")
            print(f"     High: {cost_result.get('high_huf', 'N/A'):>12,} Ft")
            print(f"     Similar cases: {cost_result.get('num_similar_cases', 0)}")
            if cost_result.get("warning"):
                print(f"     Warning: {cost_result['warning']}")

        # ── Query 1b: Due diligence advice ──
        print("\n[2/3] Calling get_due_diligence_advice...")
        dd_result = mcp.call_tool("get_due_diligence_advice", {
            "district": 8, "area_sqm": 55, "building_type": "tegla",
            "condition": "kozepes", "known_issues": ["kohosalak", "nedvesedes"],
        })
        if "error" in dd_result:
            print(f"  ERROR: {dd_result['error']}")
        else:
            print(f"  Overall risk: {dd_result.get('overall_risk', 'N/A')}")
            print(f"  Questions for seller: {len(dd_result.get('questions_for_seller', []))}")
            print(f"  Inspection checklist: {len(dd_result.get('inspection_checklist', []))}")
            print(f"  Red flags: {len(dd_result.get('red_flags', []))}")
            print(f"  Sources cited: {len(dd_result.get('sources_cited', []))}")

        # ── Query 2: Market data query ──
        print("\n" + "─" * 70)
        print('QUERY 2: "Mennyibe került átlagosan a villanyszerelés a 2023-as arakban?"')
        print("─" * 70)
        print("\n[3/3] Calling query_renovation_market...")
        market_result = mcp.call_tool("query_renovation_market", {
            "question_hu": "Mennyibe került átlagosan a villanyszerelés a 2023-as arakban?",
        })
        if "error" in market_result:
            print(f"  ERROR: {market_result['error']}")
        else:
            print(f"  SQL: {market_result.get('sql', 'N/A')}")
            print(f"  Rows returned: {market_result.get('row_count', 0)}")
            if market_result.get("rows"):
                print(f"  First row: {json.dumps(market_result['rows'][0], ensure_ascii=False)}")

    print("\n" + "=" * 70)
    print("Demo complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
