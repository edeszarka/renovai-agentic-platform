#!/usr/bin/env python3
"""
SBOM Generation & Binary Authorization Gate for RenovAI.

Generates a Software Bill of Materials (SPDX 2.3 format) from the project's
dependencies and verifies against a known-good manifest to prevent deployment
of containers with 'hallucinated packages' or unverified dependencies.

Usage:
    # Generate SBOM
    python scripts/generate_sbom.py --generate --output data/sbom/spdx.json

    # Verify against attestation
    python scripts/generate_sbom.py --verify --manifest data/sbom/known_good.json

    # Full gate (generate + verify)
    python scripts/generate_sbom.py --gate
"""

import os
import sys
import json
import hashlib
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Known-good package manifest
# These are the blessed versions that have been reviewed for security.
# Any deviation triggers the Binary Authorization gate.
# ---------------------------------------------------------------------------

KNOWN_GOOD_MANIFEST: dict[str, str] = {
    # Core dependencies (from pyproject.toml)
    "openpyxl": "3.1.5",
    "pandas": "2.2.0",
    "streamlit": "1.35.0",
    "chromadb": "0.5.0",
    "google-generativeai": "0.8.0",
    "langchain": "0.3.0",
    "langchain-google-genai": "2.0.0",
    "scikit-learn": "1.5.0",
    "fastapi": "0.115.0",
    "pydantic": "2.9.0",
    "pydantic-settings": "2.5.0",
    "uvicorn": "0.31.0",
    "python-dotenv": "1.0.1",
    "rich": "13.9.0",
    "joblib": "1.4.0",
    "tabpfn": "0.1.0",
    "tiktoken": "0.8.0",
    "langchain-community": "0.3.0",
    "rank-bm25": "0.2.2",
    "sqlalchemy": "2.0.35",
    "alembic": "1.13.0",
    "aiosqlite": "0.20.0",
    "tenacity": "9.0.0",
    "diskcache": "5.6.3",
    "openai": "1.50.0",
    "google-cloud-aiplatform": "1.148.1",
    "google-adk": "2.1.0",
    "google-cloud-logging": "3.16.0",
}

# Packages whose presence would indicate a hallucinated / unverified dependency
BLOCKED_PACKAGES: list[str] = [
    "requests",           # Should use httpx or aiohttp
    "beautifulsoup4",     # No web scraping in this system
    "selenium",           # No browser automation
    "flask",              # Using FastAPI, not Flask
    "django",             # Wrong framework
    "tensorflow",         # Using scikit-learn + TabPFN
    "torch",              # Using scikit-learn + TabPFN
    "transformers",       # Using Google GenAI SDK directly
    "psycopg2",           # Using SQLAlchemy + aiosqlite
    "mysql-connector",    # Using SQLAlchemy + aiosqlite
    "cryptography",       # Not needed (identities use hmac + hashlib)
    "paramiko",           # No SSH
    "docker",             # No Docker SDK from within the container
]


# ---------------------------------------------------------------------------
# SBOM Generation (SPDX 2.3)
# ---------------------------------------------------------------------------

def scan_installed_packages() -> dict[str, str]:
    """Scan all installed Python packages using importlib.metadata."""
    try:
        from importlib.metadata import distributions, version
    except ImportError:
        from importlib_metadata import distributions, version

    packages: dict[str, str] = {}
    for dist in distributions():
        name = dist.metadata.get("Name", dist.metadata["Name"])
        ver = dist.version
        packages[name.lower()] = ver
    return packages


def generate_spdx_sbom(
    packages: dict[str, str],
    project_name: str = "renovai",
    project_version: str = "0.1.0",
) -> dict[str, Any]:
    """Generate an SPDX 2.3 SBOM document from installed packages."""
    now = datetime.now(timezone.utc).isoformat()

    # Build SPDX document
    spdx: dict[str, Any] = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{project_name}-{project_version}-sbom",
        "creationInfo": {
            "created": now,
            "creators": [
                "Tool: renovai-sbom-generator-1.0",
                "Organization: RenovAI",
            ],
        },
        "packages": [],
        "relationships": [],
    }

    for pkg_name, pkg_version in sorted(packages.items()):
        pkg_spdx_id = f"SPDXRef-Package-{pkg_name}"

        # Compute file hash of the package's top-level module for integrity
        pkg_hash = ""
        try:
            import importlib
            mod = importlib.import_module(pkg_name.replace("-", "_"))
            mod_file = getattr(mod, "__file__", None)
            if mod_file and os.path.isfile(mod_file):
                pkg_hash = hashlib.sha256(open(mod_file, "rb").read()).hexdigest()
        except (ImportError, Exception):
            pass

        spdx_pkg = {
            "SPDXID": pkg_spdx_id,
            "name": pkg_name,
            "versionInfo": pkg_version,
            "supplier": "NOASSERTION",
            "downloadLocation": "NOASSERTION",
            "packageFileName": f"{pkg_name}-{pkg_version}",
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "checksums": [{"algorithm": "SHA256", "checksumValue": pkg_hash}] if pkg_hash else [],
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": f"pkg:pypi/{pkg_name}@{pkg_version}",
                }
            ],
        }
        spdx["packages"].append(spdx_pkg)

        # Relationship to document root
        spdx["relationships"].append({
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": pkg_spdx_id,
        })

    return spdx


# ---------------------------------------------------------------------------
# Binary Authorization Gate
# ---------------------------------------------------------------------------

def verify_manifest(
    installed_packages: dict[str, str],
    known_good: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Verify installed packages against the known-good manifest.

    Returns:
    {
        "gate_passed": bool,
        "allowed_packages": [...],
        "unknown_packages": [...],
        "version_mismatches": [...],
        "blocked_packages_found": [...],
    }
    """
    known = known_good or KNOWN_GOOD_MANIFEST

    result: dict[str, Any] = {
        "gate_passed": True,
        "allowed_packages": [],
        "unknown_packages": [],
        "version_mismatches": [],
        "blocked_packages_found": [],
    }

    for name, version in installed_packages.items():
        name_lower = name.lower()

        # Check against blocked list
        if name_lower in BLOCKED_PACKAGES:
            result["blocked_packages_found"].append(f"{name_lower}=={version}")
            result["gate_passed"] = False
            continue

        # Check against known-good manifest
        if name_lower in known:
            expected_ver = known[name_lower]
            if version != expected_ver:
                result["version_mismatches"].append(
                    f"{name_lower}: installed={version}, expected={expected_ver}"
                )
                result["gate_passed"] = False
            else:
                result["allowed_packages"].append(f"{name_lower}=={version}")
        else:
            # Unknown package — warn but don't block immediately
            result["unknown_packages"].append(f"{name_lower}=={version}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RenovAI SBOM Generator & Binary Authorization Gate",
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="Generate SPDX SBOM from installed packages",
    )
    parser.add_argument(
        "--output", type=str, default="data/sbom/spdx.json",
        help="Output path for SBOM file",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Verify installed packages against known-good manifest",
    )
    parser.add_argument(
        "--manifest", type=str, default=None,
        help="Path to known-good manifest JSON (optional)",
    )
    parser.add_argument(
        "--gate", action="store_true",
        help="Run full gate: generate SBOM + verify manifest, exit with code 0/1",
    )
    parser.add_argument(
        "--fail-on-unknown", action="store_true",
        help="Treat unknown packages as gate failures",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    # Load packages
    logger.info("Scanning installed packages...")
    installed = scan_installed_packages()
    logger.info("Found %d installed packages", len(installed))

    if args.generate or args.gate:
        sbom = generate_spdx_sbom(installed)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(sbom, f, indent=2, ensure_ascii=False)
        logger.info("SBOM written to %s (%d packages)", output_path, len(sbom["packages"]))

    if args.verify or args.gate:
        # Load custom manifest if provided
        manifest = KNOWN_GOOD_MANIFEST
        if args.manifest:
            manifest_path = Path(args.manifest)
            if manifest_path.exists():
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                logger.info("Loaded manifest from %s", args.manifest)

        result = verify_manifest(installed, manifest)

        # Apply --fail-on-unknown
        if args.fail_on_unknown and result["unknown_packages"]:
            result["gate_passed"] = False

        # Print results
        print(f"\n{'='*60}")
        print(f"Binary Authorization Gate")
        print(f"{'='*60}")
        print(f"Gate passed: {'YES' if result['gate_passed'] else 'NO'}")
        print(f"Allowed packages: {len(result['allowed_packages'])}")
        print(f"Unknown packages: {len(result['unknown_packages'])}")
        print(f"Version mismatches: {len(result['version_mismatches'])}")
        print(f"Blocked packages: {len(result['blocked_packages_found'])}")

        if result["unknown_packages"]:
            print(f"\nUnknown packages:")
            for pkg in result["unknown_packages"]:
                print(f"  [yellow]?[/yellow] {pkg}")

        if result["version_mismatches"]:
            print(f"\nVersion mismatches:")
            for mm in result["version_mismatches"]:
                print(f"  [red]![/red] {mm}")

        if result["blocked_packages_found"]:
            print(f"\nBlocked packages (hallucinated):")
            for pkg in result["blocked_packages_found"]:
                print(f"  [red]BLOCKED[/red] {pkg}")

        if not result["gate_passed"]:
            sys.exit(1)

    if not any([args.generate, args.verify, args.gate]):
        parser.print_help()


if __name__ == "__main__":
    main()
