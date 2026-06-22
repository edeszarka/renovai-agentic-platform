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

"""
Deployment script for Agent Runtime.

Packages the source code and deploys to Agent Runtime via
the Vertex AI SDK. Intended to be called by CI/CD or directly:

    uv run python -m app.app_utils.deploy --project=... --region=...
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent


def export_requirements():
    """Export locked requirements from uv lockfile."""
    req_path = PROJECT_DIR / "app" / "app_utils" / ".requirements.txt"
    subprocess.run(
        ["uv", "export", "--no-dev", "--no-hashes", "-o", str(req_path)],
        check=True,
        cwd=str(PROJECT_DIR),
    )
    return req_path


def build_source_tarball() -> str:
    """Create a base64-encoded tarball of the source code."""
    import base64
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in PROJECT_DIR.rglob("*"):
            parts = path.relative_to(PROJECT_DIR).parts
            if any(
                p.startswith(".") or p == "__pycache__" or p == "node_modules"
                for p in parts
            ):
                continue
            if path.is_file():
                arcname = str(path.relative_to(PROJECT_DIR))
                info = tar.gettarinfo(str(path), arcname=arcname)
                if info:
                    with open(path, "rb") as f:
                        tar.addfile(info, f)
    return base64.b64encode(buf.getvalue()).decode()


def deploy(args: argparse.Namespace):
    """Deploy the agent to Agent Runtime."""

    req_path = export_requirements()
    source_tarball = build_source_tarball()
    size_mb = len(source_tarball) * 3 / 4 / 1024 / 1024

    if args.dry_run:
        print(f"[DRY-RUN] Requirements exported to: {req_path}")
        print(f"[DRY-RUN] Source tarball size: ~{size_mb:.1f} MB (base64)")
        print(f"[DRY-RUN] Project: {args.project}")
        print(f"[DRY-RUN] Region: {args.region}")
        print(f"[DRY-RUN] Name: {args.name}")
        print("[DRY-RUN] Entrypoint: app.agent_runtime_app:agent_runtime")
        print("[DRY-RUN] Python: 3.12")
        print("[DRY-RUN] Dry-run complete — no resources created.")
        return

    import vertexai
    from vertexai.agent_engines import ReasoningEngine

    vertexai.init(project=args.project, location=args.region)

    env = [
        {"name": "MCP_SERVER_URL", "value": os.getenv("MCP_SERVER_URL", "https://renovai-mcp-server-83670173168.us-central1.run.app")},
    ]

    existing = None
    metadata_path = PROJECT_DIR / "deployment_metadata.json"
    if metadata_path.exists():
        with open(metadata_path) as f:
            metadata = json.load(f)
        rid = metadata.get("remote_agent_runtime_id", "")
        if rid:
            try:
                existing = ReasoningEngine(rid)
                existing.get()
            except Exception:
                existing = None

    if existing:
        existing.update(
            source_code=source_tarball,
            requirements_file=str(req_path),
        )
        print(f"Updated existing engine: {existing.resource_name}")
    else:
        engine = ReasoningEngine.create(
            display_name=args.name or "renovai-capstone",
            description="RenovAI renovation due-diligence assistant",
            agent_framework="google-adk",
            entrypoint_module="app.agent_runtime_app",
            entrypoint_object="agent_runtime",
            source_code=source_tarball,
            requirements_file=str(req_path),
            python_version="3.12",
            env=env,
        )
        print(f"Created engine: {engine.resource_name}")
        with open(metadata_path, "w") as f:
            json.dump({
                "remote_agent_runtime_id": engine.resource_name,
                "deployment_target": "agent_runtime",
                "is_a2a": False,
                "deployment_timestamp": engine.create_time.isoformat() if hasattr(engine, "create_time") else "",
            }, f, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy to Agent Runtime")
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--name", default="renovai-capstone")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Validate configuration without deploying")
    args = parser.parse_args()
    deploy(args)
