"""Backfill building taxonomy + floor/completeness onto existing Quote rows.

Reads the canonical markdown corpus under data/processed/quotes_md/ (one file
per quote, named identically to the source xlsx) and extracts:

* ``building_type``  — Task 2.5 rule: ``panel`` -> PANEL, ``családi ház``
  -> TEGLA_CSALADI_HAZ, otherwise TEGLA when the text matches the corpus
  filename grammar (an era/salak clause is present); only text that does
  NOT look like a corpus filename stays NULL. Free text is intentionally
  NOT scanned for product terms (fűtéspanel, üvegtégla, porotherm, Ytong)
  that would be false positives. VÁLYOG is removed from the taxonomy.
* ``building_era``    — canonical representative year (int). Decades map to
  their midpoint (1970-es évek -> 1975); explicit years are kept verbatim
  (1958, 2011, kb. 2005 -> 2005).
* ``floor_number``    — from the filename: ``fsz``/``fszt``/``földszint`` ->
  1 (ground), ``N. emelet``/``N emelet`` -> N.
* ``renovation_completeness`` — strict literal token from the filename:
  ``komplett`` -> "komplett", ``részleges`` -> "reszleges"; blends
  ("majdnem komplett") are left None and reported, not force-fit.
* ``elevator_type``   — from the free-text body: explicit "nincs lift" ->
  "none"; conditional lift-use mentions ("ha lehet használni a liftet") ->
  generic "present"; no mention -> None.

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
    """Classify building type from a corpus filename / front-matter source.

    Task 2.5 explicit rule (derived from the Task A filename grammar):
      * ``panel`` (word-bounded, case/accent-insensitive)  -> PANEL
      * ``családi ház`` (clear textual evidence on one corpus file)
                                                          -> TEGLA_CSALADI_HAZ
      * otherwise, when the text matches the corpus grammar (an era clause
        is present — 47/47 corpus files carry one)         -> TEGLA (default)
      * text that does NOT look like a corpus filename      -> None

    VÁLYOG is not a valid value in this pass (removed from the taxonomy).
    Word-boundary matching keeps product terms (fűtéspanel) from counting
    as building types; "Téglási" (a street name) is not a tégla trigger —
    it classifies via the default only.
    """
    low = text.lower()
    if re.search(r"\bpanel\b", low):
        return BuildingType.PANEL
    if re.search(r"\bcsaládi\s*ház\b", low):
        return BuildingType.TEGLA_CSALADI_HAZ
    if extract_building_era(text) is not None or re.search(r"\bsalak\b", low):
        return BuildingType.TEGLA
    return None


_FLOOR_GROUND_RE = re.compile(r"\b(?:fsz|fszt|földszint)\b")
_FLOOR_NUM_RE = re.compile(r"\b(\d{1,2})\s*\.?\s*(?:emelet|em)\b")


def extract_floor_number(text: str) -> Optional[int]:
    """Return floor number from a corpus filename.

    ``fsz`` / ``fszt`` / ``földszint`` (ground floor) -> 1; ``N. emelet`` /
    ``N emelet`` -> N. Convention: floor 1 = ground floor, matching the rest
    of the codebase (``structural_cost.elevator_surcharge`` treats
    ``floor_number = 1`` as no-floors-above-ground, and the streamlit UI's
    minimum is 1).
    """
    low = text.lower()
    if _FLOOR_GROUND_RE.search(low):
        return 1
    m = _FLOOR_NUM_RE.search(low)
    if m:
        return int(m.group(1))
    return None


def extract_renovation_completeness(text: str) -> Optional[str]:
    """Classify renovation completeness ("reszleges" | "komplett") from a file.

    Strict literal-token grammar: a bare ``komplett`` token -> ``komplett``;
    a bare ``részleges`` token -> ``reszleges``. Blends ("majdnem komplett",
    "többnyire komplett") are NOT forced into the binary field — the strict
    grammar has only the two literals, so those map to None and are reported
    as "neither/unclear" in the summary rather than force-fit.
    """
    low = text.lower()
    if re.search(r"\bmajdnem\s+komplett\b|\btöbbnyire\s+komplett\b", low):
        return None
    if re.search(r"\bkomplett\b", low):
        return "komplett"
    if re.search(r"\brészleges\b", low):
        return "reszleges"
    return None


# Lift / elevator presence in the free-text body. Task 2C investigation found
# conditional-use quotes ("ha lehet használni a liftet", "lifthasználat") in
# 6/47 files, never an explicit size — so the only determinable value is a
# generic "present" (size unknown). Any non-negated lift mention presupposes
# a lift exists (prices are conditioned on being able to use it), so the rule
# is: explicit absence -> "none"; any other lift word -> "present".
_ELEVATOR_ABSENT_RE = re.compile(
    r"\bnincs\s+lift\b|\blift\s+nélkül\b|\blift\s+nincs\b", re.IGNORECASE
)
_ELEVATOR_PRESENT_RE = re.compile(r"\blift\w*\b", re.IGNORECASE)


def extract_elevator_type(full_text: Optional[str]) -> Optional[str]:
    """Return elevator_type from a quote body: "none" | "present" | None.

    Explicit absence ("nincs lift") -> "none". Any other lift mention ->
    generic "present" (size undeterminable). No lift mention at all -> None
    (do not force a value).
    """
    if not full_text:
        return None
    low = full_text.lower()
    if _ELEVATOR_ABSENT_RE.search(low):
        return "none"
    if _ELEVATOR_PRESENT_RE.search(low):
        return "present"
    return None


def extract_taxonomy(
    filename: str,
    front_matter_source: Optional[str] = None,
    body_text: Optional[str] = None,
) -> dict:
    """Extract taxonomy + completeness from a corpus filename.

    The filename (md stem) is authoritative; ``source:`` is the same text in
    normal form, so use whichever is present (filename wins on ties). Floor
    and completeness only ever come from the filename. Elevator presence is
    read from the free-text body (body_text).
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
    return {
        **best,
        "floor_number": extract_floor_number(filename),
        "renovation_completeness": extract_renovation_completeness(filename),
        "elevator_type": extract_elevator_type(body_text or ""),
    }


def read_front_matter_source(md_path: Path) -> Optional[str]:
    """Read the ``source:`` YAML front-matter field, if present."""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            head = f.read(1024)
    except OSError:
        return None
    m = re.search(r"^source:\s*(.+)$", head, flags=re.MULTILINE)
    return m.group(1).strip() if m else None


def read_body(md_path: Path) -> str:
    """Read the full markdown body (front-matter + prose) for free-text scans."""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


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
                db_quote.floor_number = row.get("floor_number")
                db_quote.elevator_type = row.get("elevator_type")
                db_quote.renovation_completeness = row.get("renovation_completeness")
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


async def run(
    corpus_dir: Path, checkpoint_path: Path, batch_size: int, dry_run: bool
) -> None:
    md_files = sorted(corpus_dir.glob("*.md"))
    logger.info("Corpus: %d markdown files in %s", len(md_files), corpus_dir)
    if not md_files:
        logger.error("No .md files found — aborting.")
        sys.exit(2)

    checkpoint = load_checkpoint(checkpoint_path)
    results: dict[str, dict] = {}
    unresolved_log: list[str] = []

    for i in range(0, len(md_files), batch_size):
        batch = md_files[i : i + batch_size]
        for md_path in batch:
            stem = md_path.stem
            if stem in checkpoint:
                results[stem] = checkpoint[stem]
                continue
            source = read_front_matter_source(md_path)
            body = read_body(md_path)
            tax = extract_taxonomy(stem, source, body)
            row = {
                "file_name": f"{stem}.xlsx",
                "building_type": tax["building_type"].value
                if tax["building_type"]
                else None,
                "building_era": tax["building_era"],
                "floor_number": tax["floor_number"],
                "elevator_type": tax["elevator_type"],
                "renovation_completeness": tax["renovation_completeness"],
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
    engine = get_engine(
        os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")
    )
    all_rows = [
        {
            "file_name": r["file_name"],
            "building_type": r["building_type"],
            "building_era": r["building_era"],
            "floor_number": r["floor_number"],
            "elevator_type": r["elevator_type"],
            "renovation_completeness": r["renovation_completeness"],
        }
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
