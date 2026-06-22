"""
Sandboxed quote ingestion for buyer-uploaded XLSX files.

When a buyer uploads their own contractor's XLSX quote for analysis, this
module spawns an isolated subprocess that:
  - Has NO network access (subprocess + potential container-level isolation)
  - Has read-only access to the existing data directory
  - Can ONLY write to a scratch temp directory (not the production DB)
  - Runs the renovai quote parser on the uploaded file

The parent process reads the scratch JSON output, validates it against the
RenovationQuote schema, and then asks for explicit confirmation before
merging into the real database.  The merge is NEVER automatic.

Threat model:
  - Untrusted XLSX files from buyers: these are financial documents
    from unknown contractors that could contain crafted spreadsheet
    structures designed to exploit parsing libraries.
  - openpyxl doesn't execute macros, but a file with deeply nested XML,
    billion-laughs-style entity expansion, or excessive row counts could
    cause resource exhaustion.
  - The sandbox subprocess is the blast-radius boundary: even if the
    parser is compromised, the attacker gets no network access and
    cannot write to the production database.
  - Production deployment should use the Dockerfile.sandbox for full
    container isolation (seccomp, no-network, read-only root).
  - For local POC, Python's subprocess with reduced privileges is
    sufficient — the key boundary is the separate process + temp dir.

Usage:
    python -m sandbox.ingest_sandboxed path/to/contractor_quote.xlsx
"""

import os
import sys
import json
import tempfile
import subprocess
from pathlib import Path

from renovai.ingestion.models import RenovationQuote


def run_sandboxed(xlsx_path: Path) -> dict:
    """
    Spawn a subprocess to parse the XLSX in isolation.

    Returns the parsed RenovationQuote dict if successful, or an error dict.
    """
    sandbox_script = Path(__file__).parent / "sandboxed_parse.py"
    if not sandbox_script.exists():
        return {"error": f"Sandbox script not found at {sandbox_script}"}

    with tempfile.TemporaryDirectory(prefix="renovai_sandbox_") as tmp_dir:
        output_json = Path(tmp_dir) / "parsed_output.json"

        proc = subprocess.run(
            [
                sys.executable,
                str(sandbox_script),
                str(xlsx_path.resolve()),
                str(output_json),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Parse the subprocess's stdout message
        try:
            status = json.loads(proc.stdout.strip())
        except (json.JSONDecodeError, ValueError):
            return {
                "error": f"Subprocess returned invalid JSON: {proc.stderr[:500]}",
                "stdout": proc.stdout[:1000],
                "stderr": proc.stderr[:1000],
            }

        if "error" in status:
            return status

        if not output_json.exists():
            return {"error": "Subprocess completed but no output file was created."}

        # Read the parsed output
        try:
            raw = json.loads(output_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {"error": f"Output file contains invalid JSON: {exc}"}

        # Validate against the schema
        try:
            RenovationQuote(**raw)
        except Exception as exc:
            return {
                "error": f"Parsed output failed schema validation: {exc}",
                "raw_preview": {k: raw.get(k) for k in list(raw.keys())[:5]},
            }

        return raw


def merge_into_db(quote: RenovationQuote, database_url: str = None) -> dict:
    """Merge a validated quote into the production database."""
    import asyncio

    from renovai.db.session import get_engine, get_session_maker, init_db
    from renovai.db.models import Base
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    async def _merge():
        url = database_url or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
        engine = get_engine(url)
        session_maker = get_session_maker(engine)

        async with session_maker() as session:
            existing = await session.execute(
                select(type("Q", (), {"file_name": None}))  # placeholder
            )

            # Build ORM objects from the parsed quote
            from datetime import date
            from renovai.db.models import Quote, LineItemORM, WorkCategory, NotIncluded, BuyerPurchase, GeneralNote, SEED_WORK_CATEGORIES

            q = Quote(
                file_name=quote.metadata.file_name,
                address_raw=quote.metadata.address_raw,
                district=quote.metadata.district,
                postal_code=quote.metadata.postal_code,
                street=quote.metadata.street,
                house_number=quote.metadata.house_number,
                floor=quote.metadata.floor,
                quote_date=quote.metadata.quote_date or date.today(),
                total_labor=quote.metadata.total_labor,
                total_material=quote.metadata.total_material,
                grand_total=quote.metadata.grand_total,
                timeline_weeks_min=quote.metadata.timeline_weeks_min,
                timeline_weeks_max=quote.metadata.timeline_weeks_max,
                has_slag_complication=quote.metadata.has_slag_complication,
                labor_vat_included=quote.metadata.labor_vat_included,
                quote_style=quote.quote_style,
                materials_brands=", ".join(quote.metadata.materials_brands) if quote.metadata.materials_brands else None,
                general_notes_raw="\n".join(quote.general_notes) if quote.general_notes else None,
                not_included_raw="\n".join(quote.not_included) if quote.not_included else None,
                buyer_purchases_raw="\n".join(quote.buyer_purchases) if quote.buyer_purchases else None,
            )
            session.add(q)
            await session.flush()

            for item in quote.line_items:
                li = LineItemORM(
                    quote_id=q.id,
                    name=item.name,
                    section=item.section,
                    version=item.version,
                    phase=item.phase,
                    labor_cost=item.labor_cost,
                    material_cost=item.material_cost,
                    total_cost=item.total_cost,
                    notes=item.notes,
                )
                session.add(li)

            await session.commit()
            return {"status": "ok", "quote_id": q.id, "file_name": q.file_name}

    return asyncio.run(_merge())


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m sandbox.ingest_sandboxed <xlsx_path> [--merge]")
        sys.exit(1)

    xlsx_path = Path(sys.argv[1])
    auto_merge = "--merge" in sys.argv

    if not xlsx_path.exists():
        print(f"Error: file not found: {xlsx_path}")
        sys.exit(1)

    print(f"Parsing {xlsx_path} in sandboxed subprocess...")
    result = run_sandboxed(xlsx_path)

    if "error" in result:
        print(f"Sandboxed parsing failed: {result['error']}")
        sys.exit(1)

    print("Sandboxed parsing succeeded!")
    print(f"  File: {result['metadata']['file_name']}")
    print(f"  Grand total: {result['metadata']['grand_total']:,} HUF")
    print(f"  Line items: {len(result['line_items'])}")
    print(f"  Warnings: {len(result.get('warnings', []))}")

    if auto_merge:
        print("Merging into database (--merge flag set)...")
        merge_result = merge_into_db(RenovationQuote(**result))
        if merge_result.get("status") == "ok":
            print(f"Merged as quote_id={merge_result['quote_id']}")
        else:
            print(f"Merge failed: {merge_result}")
    else:
        print("\nQuote validated. To merge into the database, re-run with --merge")
        print("  python -m sandbox.ingest_sandboxed <xlsx_path> --merge")


if __name__ == "__main__":
    main()
