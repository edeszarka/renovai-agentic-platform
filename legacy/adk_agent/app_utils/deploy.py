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

Builds the agent engine locally and deploys it to Agent Runtime via
the Vertex AI SDK. Intended to be called by CI/CD or directly:

    uv run python -m legacy.adk_agent.app_utils.deploy --project=... --region=...
"""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent


def export_requirements():
    """Export locked requirements from uv lockfile, stripping comments and editable installs."""
    raw_path = PROJECT_DIR / "legacy" / "adk_agent" / "app_utils" / ".requirements-raw.txt"
    clean_path = PROJECT_DIR / "legacy" / "adk_agent" / "app_utils" / ".requirements.txt"
    subprocess.run(
        ["uv", "export", "--no-dev", "--no-hashes", "-o", str(raw_path)],
        check=True, cwd=str(PROJECT_DIR),
    )
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    clean = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-e "):
            continue
        clean.append(stripped.split(" #")[0].strip())
    clean_path.write_text("\n".join(clean) + "\n", encoding="utf-8")
    return clean_path


def deploy(args: argparse.Namespace):
    """Deploy the agent to Agent Runtime."""

    req_path = export_requirements()
    extra_packages = [
        str(PROJECT_DIR / "legacy" / "adk_agent"),
        str(PROJECT_DIR / "legacy" / "mcp_server"),
        str(PROJECT_DIR / "legacy" / "adk_agent_stretch_goal"),
        str(PROJECT_DIR / "sandbox"),
        str(PROJECT_DIR / "renovai"),
        str(PROJECT_DIR / "demo"),
        str(PROJECT_DIR / "scripts"),
        str(PROJECT_DIR / "docs"),
    ]

    if args.dry_run:
        print(f"[DRY-RUN] Requirements exported to: {req_path}")
        print(f"[DRY-RUN] Project: {args.project}")
        print(f"[DRY-RUN] Region: {args.region}")
        print(f"[DRY-RUN] Name: {args.name}")
        print(f"[DRY-RUN] Staging bucket: {args.staging_bucket}")
        print(f"[DRY-RUN] Extra packages ({len(extra_packages)} dirs):")
        for p in extra_packages:
            print(f"    {p}")
        print("[DRY-RUN] Dry-run complete — no resources created.")
        return

    import vertexai
    from vertexai.agent_engines import AgentEngine

    vertexai.init(
        project=args.project,
        location=args.region,
        staging_bucket=args.staging_bucket,
    )

    from legacy.adk_agent.agent_runtime_app import _build_agent_runtime
    local_agent = _build_agent_runtime()

    env_vars = {
        "MCP_SERVER_URL": os.getenv(
            "MCP_SERVER_URL",
            "https://renovai-mcp-server-fxedmdu3vq-uc.a.run.app",
        ),
    }

    metadata_path = PROJECT_DIR / "legacy" / "deployment" / "deployment_metadata.json"
    existing_rid = None
    if metadata_path.exists():
        with open(metadata_path) as f:
            metadata = json.load(f)
        existing_rid = metadata.get("remote_agent_runtime_id", "")

    if existing_rid and existing_rid != "None":
        existing = AgentEngine(existing_rid)
        print(f"Updating existing engine: {existing_rid}")
        existing.update(
            agent_engine=local_agent,
            requirements=str(req_path),
            extra_packages=extra_packages,
            env_vars=env_vars,
            display_name=args.name,
        )
        print(f"Updated engine: {existing.resource_name}")
    else:
        engine = AgentEngine.create(
            agent_engine=local_agent,
            requirements=str(req_path),
            extra_packages=extra_packages,
            display_name=args.name,
            description="RenovAI renovation due-diligence assistant",
            env_vars=env_vars,
            min_instances=args.min_instances,
            max_instances=args.max_instances,
            resource_limits={"cpu": args.cpu, "memory": args.memory},
            container_concurrency=args.concurrency,
        )
        resource_name = engine.resource_name
        print(f"Created engine: {resource_name}")
        with open(metadata_path, "w") as f:
            json.dump({
                "remote_agent_runtime_id": resource_name,
                "deployment_target": "agent_runtime",
                "is_a2a": False,
                "deployment_timestamp": str(engine.create_time) if hasattr(engine, "create_time") else "",
            }, f, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy to Agent Runtime")
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--name", default="renovai-capstone")
    parser.add_argument("--staging-bucket", default="gs://renovai-agent-runtime-staging")
    parser.add_argument("--cpu", default="4")
    parser.add_argument("--memory", default="8Gi")
    parser.add_argument("--min-instances", type=int, default=1)
    parser.add_argument("--max-instances", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--dry-run", "-n", action="store_true", help="Validate configuration without deploying")
    args = parser.parse_args()
    deploy(args)
