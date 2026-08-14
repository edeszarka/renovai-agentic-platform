"""Backfill building_type / building_era onto existing Quote rows.

Reads the canonical markdown corpus under data/processed/quotes_md/ (one file
per quote, named identically to the source xlsx) and extracts the building
taxonomy from each filename + front-matter ``source:``:

* ``building_type``  — only an explicit type token (panel, családi ház,
  csúszózsalus, könnyűszerkezetes, vályog, tégla) is honored; ambiguous /
  absent signals are stored as NULL. Free text is intentionally NOT scanned:
  survey of the 47-file corpus showed product terms (fűtéspanel, üvegtégla,
  porotherm, Ytong) that would be false positives.
* ``building_era``    — canonical representative year (int). Decades map to
  their midpoint (1970-es évek -> 1975); explicit years are kept verbatim
  (1958, 2011, kb. 2005 -> 2005).

Rows are matched by ``Quote.file_name == "<md stem>.xlsx"``. Processing is
batched and resumable via a JSON checkpoint.

Usage:
    python -m scripts.backfill_building_taxonomy [--dry-run] [--batch-size 10]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from renovai.db.models import BuildingType, Quote
from renovai.db.session import get_engine, get_session_maker

logger = logging.getLogger("backfill_taxonomy")

CORPUS_DIR = Path("data/processed/quotes_md")
CHECKPOINT_PATH = Path("data/processed/building_taxonomy_checkpoint.json")
DEFAULT_BATCH_SIZE = 10

# --- Extraction ---------------------------------------------------------------

# Era patterns, evaluated in priority order.
_EPITES_RE = re.compile(r"(\d{4})\s*építés(?:\s*éve)?")
_RANGE_RE = re.compile(r"(\d{4})[_\-\s]+(\d{4}|[0-9]{2})-?(?:as|es|ös|os|s)?\s*évek")
_DECADE_RE = re.compile(r"(\d{4})-?(?:as|es|ös|os|s)\s*évek")
_KB_RE = re.compile(r"kb\.?\s*(\d{4})")
# Bare 4-digit year, excluding Budapest postal codes (1000-1299).
_BARE_RE = re.compile(r"\b(1[3-9]\d\d|20\d\d)\b")

_YEAR_MIN = 1300
_YEAR_MAX = 2099


def _midpoint(a: int, b: int) -> int:
    return (a + b) // 2


def extract_building_era(text: str) -> Optional[int]:
    """Return canonical representative year (int) or None if not derivable."""
    low = text.lower()
    low = low.replace("–", "-").replace("—", "-")

    m = _EPITES_RE.search(low)
    if m:
        return int(m.group(1))

    m = _RANGE_RE.search(low)
    if m:
        start = int(m.group(1))
        end_raw = m.group(2)
        end = int(end_raw) if len(end_raw) == 4 else start // 100 * 100 + int(end_raw)
        if end < start:
            end += 100
        return _midpoint(start, end)

    m = _DECADE_RE.search(low)
    if m:
        return int(m.group(1)) + 5

    m = _KB_RE.search(low)
    if m:
        return int(m.group(1))

    m = _BARE_RE.search(low)
    if m:
        year = int(m.group(1))
        if _YEAR_MIN <= year <= _YEAR_MAX:
            return year
    return None


def extract_building_type(text: str) -> Optional[BuildingType]:
    """Return BuildingType when the filename names it explicitly, else None."""
    low = text.lower()
    if re.search(r"\bcsúszózsalus\b", low) or re.search(r"\bcsuszozsalus\b", low):
        return BuildingType.CSUSZOZSALUS
    if re.search(r"\bkönnyűszerkezetes\b", low) or re.search(r"\bkonnyuszerkezetes\b", low):
        return BuildingType.KONNYUSZERKEZETES
    if re.search(r"\bcsaládi\s*ház\b", low):
        return BuildingType.TEGLA_CSALADI_HAZ
    if re.search(r"\bvályog\b", low):
        return BuildingType.VALYOG_VEGYES
    if re.search(r"\bpanel\b", low):
        return BuildingType.PANEL
    if re.search(r"\btégla\b", low):
        return BuildingType.TEGLA
    return None


def extract_taxonomy(filename: str, front_matter_source: Optional[str] = None) -> dict:
    """Extract taxonomy from a corpus filename (plus optional source: front-matter).

    The filename (md stem) is authoritative; ``source:`` is the same text in
    normal form, so use whichever is present (filename wins on ties).
    """
    sources = [filename]
    if front_matter_source:
        sources.append(front_matter_source)
    best = {"building_type": None, "building_era": None}
    for src in sources:
        era = extract_building_era(src)
        btype = extract_building_type(src)
        if era is not None and best["building_era"] is None:
            best["building_era"] = era
        if btype is not None and best["building_type"] is None:
            best["building_type"] = btype
        if best["building_era"] is not None and best["building_type"] is not None:
            break
    return best


def read_front_matter_source(md_path: Path) -> Optional[str]:
    """Read the ``source:`` YAML front-matter field, if present."""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            head = f.read(1024)
    except OSError:
        return None
    m = re.search(r"^source:\s*(.+)$", head, flags=re.MULTILINE)
    return m.group(1).strip() if m else None


# --- DB application -----------------------------------------------------------

async def apply_backfill(
    rows: list[dict],
    engine,
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Write taxonomy onto Quote rows. Returns (applied, unresolved)."""
    applied = 0
    unresolved = 0
    maker = get_session_maker(engine)
    async with maker() as session:
        for row in rows:
            stmt = select(Quote).where(Quote.file_name == row["file_name"])
            db_quote = (await session.execute(stmt)).scalars().first()
            if db_quote is None:
                logger.warning("No DB row for file_name=%r", row["file_name"])
                unresolved += 1
                continue
            if not dry_run:
                db_quote.building_type = row["building_type"]
                db_quote.building_era = row["building_era"]
            if row["building_type"] is None or row["building_era"] is None:
                unresolved += 1
            else:
                applied += 1
        if not dry_run:
            await session.commit()
    return applied, unresolved


# --- Orchestration ------------------------------------------------------------

def load_checkpoint(path: Path) -> dict[str, dict]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_checkpoint(path: Path, checkpoint: dict[str, dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)


async def run(corpus_dir: Path, checkpoint_path: Path, batch_size: int, dry_run: bool) -> None:
    md_files = sorted(corpus_dir.glob("*.md"))
    logger.info("Corpus: %d markdown files in %s", len(md_files), corpus_dir)
    if not md_files:
        logger.error("No .md files found — aborting.")
        sys.exit(2)

    checkpoint = load_checkpoint(checkpoint_path)
    results: dict[str, dict] = {}
    unresolved_log: list[str] = []

    for i in range(0, len(md_files), batch_size):
        batch = md_files[i:i + batch_size]
        for md_path in batch:
            stem = md_path.stem
            if stem in checkpoint:
                results[stem] = checkpoint[stem]
                continue
            source = read_front_matter_source(md_path)
            tax = extract_taxonomy(stem, source)
            row = {
                "file_name": f"{stem}.xlsx",
                "building_type": tax["building_type"].value if tax["building_type"] else None,
                "building_era": tax["building_era"],
                "unresolved_fields": [
                    f for f in ("building_type", "building_era") if tax[f] is None
                ],
            }
            results[stem] = row
            checkpoint[stem] = row
            if row["unresolved_fields"]:
                unresolved_log.append(
                    f"{stem} -> {row['unresolved_fields']}"
                    f"{'' if not dry_run else ' (dry-run)'}"
                )
        save_checkpoint(checkpoint_path, checkpoint)

    # Apply all extracted rows to the DB in one pass.
    engine = get_engine(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db"))
    all_rows = [
        {"file_name": r["file_name"], "building_type": r["building_type"], "building_era": r["building_era"]}
        for r in results.values()
    ]
    applied, unresolved = await apply_backfill(all_rows, engine, dry_run=dry_run)
    await engine.dispose()

    total = len(md_files)
    logger.info("Files processed: %d/%d", len(results), total)
    logger.info("Applied (both fields set): %d", applied)
    logger.info("Unresolved rows (NULL on at least one field): %d", unresolved)
    if unresolved_log:
        logger.info("Unresolved detail (%d):", len(unresolved_log))
        for line in unresolved_log[:20]:
            logger.info("  %s", line)
    if dry_run:
        logger.info("DRY-RUN — no DB writes performed.")
    logger.info("Checkpoint: %s", checkpoint_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT_PATH)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    asyncio.run(run(args.corpus_dir, args.checkpoint, args.batch_size, args.dry_run))


if __name__ == "__main__":
    main()
