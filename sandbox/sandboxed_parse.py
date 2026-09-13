"""
Sandboxed subprocess entry point for quote parsing.

This script is spawned by ingest_sandboxed.py inside a restricted subprocess.
It reads the XLSX path from a scratch file argument, calls the renovai parser,
and writes the parsed result to a scratch JSON file.  It has no access to the
production database, no network access, and only read-only access to the
existing data directory.

Threat model:
  - Untrusted XLSX files from buyers may contain malicious macros or
    malformed structures designed to exploit openpyxl or our parser.
  - openpyxl itself does not execute macros, but a crafted file could
    cause excessive memory allocation or trigger bugs in the parser.
  - The sandbox limits blast radius: the subprocess has no network, no
    write access to the production database, and writes output only to
    a scratch location.  The parent process validates the output before
    offering to merge it into the real DB.
  - For production, use the Dockerfile.sandbox for full container-level
    isolation with seccomp and no-network namespaces.
"""

import json
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from renovai.ingestion.models import RenovationQuote
from renovai.ingestion.quote_parser import parse_quote


def main():
    if len(sys.argv) < 3:
        print(
            json.dumps(
                {"error": "Usage: sandboxed_parse.py <xlsx_path> <output_json_path>"}
            )
        )
        sys.exit(1)

    xlsx_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not xlsx_path.exists():
        print(json.dumps({"error": f"File not found: {xlsx_path}"}))
        sys.exit(1)

    try:
        quote: RenovationQuote = parse_quote(xlsx_path)
        output_path.write_text(
            quote.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        print(json.dumps({"status": "ok", "output": str(output_path)}))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}))
        sys.exit(1)


if __name__ == "__main__":
    main()
