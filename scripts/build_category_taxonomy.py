"""Build the canonical category taxonomy from the real line-item corpus.

This is an **offline, read-only** research script for Phase 1 of the category
workstream.  It:

1. Reads the distinct raw ``name_hu`` strings (and ``notes_hu``) across all
   ingested quotes in the database, **read-only** (SQLite ``mode=ro``).
2. Asks the LLM (DeepSeek primary, Gemini optional secondary — see
   ``scripts/taxonomy_llm_client.py``) to reconcile the three existing
   taxonomies and cluster the real vocabulary into ONE canonical taxonomy.
3. Assigns every distinct raw phrasing to a canonical key (batched).
4. Writes a machine-readable clustering result (for the author of
   ``docs/category_taxonomy.md``) and prints a short summary, including which
   provider/model actually served each call.

It never writes to the database and never modifies application code.

Usage::

    python -m scripts.build_category_taxonomy \
        --database-url sqlite+aiosqlite:///data/renovai.db \
        --out data/reports/category_taxonomy_clusters.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv

from scripts.taxonomy_llm_client import LLMResult, call_llm

load_dotenv()
logger = logging.getLogger("renovai.build_taxonomy")

SKILL_DOC_PATH = Path(".agent/skills/cost_estimate/references/work_categories.md")


# ---------------------------------------------------------------------------
# Inputs: the three existing taxonomies
# ---------------------------------------------------------------------------

def load_seed_categories() -> List[Dict[str, str]]:
    from renovai.db.models import SEED_WORK_CATEGORIES

    return [
        {"key": key, "label_hu": label_hu, "label_en": label_en, "cost_type": cost_type}
        for key, label_hu, label_en, cost_type in SEED_WORK_CATEGORIES
    ]


def load_scope_buckets() -> List[Dict[str, str]]:
    from renovai.predictor.price_model import SCOPE_CATEGORY_MAP

    return [
        {"key": key, "label_hu": info["label_hu"], "label_en": info["label_en"]}
        for key, info in SCOPE_CATEGORY_MAP.items()
    ]


def load_skill_doc_categories(path: Path = SKILL_DOC_PATH) -> List[Dict[str, str]]:
    """Parse the markdown table in the cost-estimate skill reference."""
    rows: List[Dict[str, str]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower() in ("hungarian keyword", "") or set(cells[0]) <= {"-", " "}:
            continue
        rows.append({"key": cells[0], "label_en": cells[1]})
    return rows


# ---------------------------------------------------------------------------
# Corpus extraction (read-only)
# ---------------------------------------------------------------------------

def sqlite_path_from_url(database_url: str) -> Path:
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if database_url.startswith(prefix):
            return Path(database_url[len(prefix):])
    raise ValueError(f"Expected a file-backed SQLite URL, got {database_url!r}")


def extract_vocabulary(db_path: Path) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]]]:
    """Return distinct (name_hu, count) and (notes_hu, count), read-only."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        names = Counter(
            r[0] for r in con.execute(
                "SELECT name_hu FROM line_items WHERE name_hu IS NOT NULL AND TRIM(name_hu) <> ''"
            )
        )
        notes = Counter(
            r[0] for r in con.execute(
                "SELECT notes_hu FROM line_items WHERE notes_hu IS NOT NULL AND TRIM(notes_hu) <> ''"
            )
        )
    finally:
        con.close()
    return names.most_common(), notes.most_common()


# ---------------------------------------------------------------------------
# LLM prompt building
# ---------------------------------------------------------------------------

def _fmt_seed(categories: Sequence[Dict[str, str]]) -> str:
    return "\n".join(
        f"- {c['key']} | {c['label_hu']} | {c['label_en']}" for c in categories
    )


def _fmt_skill(categories: Sequence[Dict[str, str]]) -> str:
    return "\n".join(f"- {c['key']} | {c['label_en']}" for c in categories)


def _fmt_scope(buckets: Sequence[Dict[str, str]]) -> str:
    return "\n".join(
        f"- {b['key']} | {b['label_hu']} | {b['label_en']}" for b in buckets
    )


def build_reconciliation_prompt(
    seed: Sequence[Dict[str, str]],
    skill: Sequence[Dict[str, str]],
    scopes: Sequence[Dict[str, str]],
    vocabulary: Sequence[Tuple[str, int]],
) -> str:
    vocab_lines = "\n".join(f"{count}\t{name}" for name, count in vocabulary)
    return (
        "You are reconciling three overlapping category taxonomies for Hungarian "
        "renovation line items into ONE canonical taxonomy. This is a text "
        "classification task: do not compute or mention any price.\n\n"
        "EXISTING TAXONOMY A — database seed categories (17 keys):\n"
        f"{_fmt_seed(seed)}\n\n"
        "EXISTING TAXONOMY B — cost-estimate skill doc (11 keys):\n"
        f"{_fmt_skill(skill)}\n\n"
        "EXISTING TAXONOMY C — customer-facing scope buckets (7 keys):\n"
        f"{_fmt_scope(scopes)}\n\n"
        "REAL RAW LINE-ITEM VOCABULARY (distinct name_hu with frequency):\n"
        f"{vocab_lines}\n\n"
        "TASK:\n"
        "1. Produce ONE canonical taxonomy (aim for 15-25 categories) that covers "
        "all of A, all of B, and the real vocabulary. For each canonical category "
        "return: key (ascii snake_case, no accents), label_hu, label_en, "
        "definition (one short sentence), and scope_bucket (exactly one of the 7 "
        "scope keys from C, or null if it is a finer category not used in "
        "customer-facing estimates).\n"
        "2. Map EVERY key from taxonomy A to exactly one canonical key "
        "(seed_key_map).\n"
        "3. Map EVERY key from taxonomy B to exactly one canonical key, or null "
        "for meta rows such as 'teljes' (full renovation) that are not work "
        "categories (skill_doc_key_map).\n\n"
        "Return JSON only, in exactly this shape:\n"
        '{"categories": [{"key": "...", "label_hu": "...", "label_en": "...", '
        '"definition": "...", "scope_bucket": "needs_..." or null}], '
        '"seed_key_map": {"<seed key>": "<canonical key>"}, '
        '"skill_doc_key_map": {"<skill key>": "<canonical key>" or null}}'
    )


def build_assignment_prompt(
    categories: Sequence[Dict[str, str]],
    items: Sequence[Tuple[int, str]],
) -> str:
    category_lines = "\n".join(
        f"- {c['key']} | {c.get('label_hu', '')} | {c.get('definition', '')}"
        for c in categories
    )
    payload = [{"item_id": idx, "name_hu": name} for idx, name in items]
    return (
        "Assign every Hungarian renovation line-item description below to "
        "exactly ONE category key from the CANONICAL CATEGORIES list. Do not "
        "invent keys and do not mention prices.\n\n"
        f"CANONICAL CATEGORIES:\n{category_lines}\n\n"
        "Return JSON only:\n"
        '{"assignments": [{"item_id": <int>, "category_key": "<key>"}]}\n\n'
        "ITEMS:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


# ---------------------------------------------------------------------------
# Parsing / normalisation
# ---------------------------------------------------------------------------

def loads_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def chunked(seq: Sequence[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(seq), size):
        yield list(seq[i:i + size])


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

@dataclass
class CallLog:
    purpose: str
    provider: str
    model: str


@dataclass
class TaxonomyBuild:
    categories: List[Dict[str, Any]] = field(default_factory=list)
    seed_key_map: Dict[str, Optional[str]] = field(default_factory=dict)
    skill_doc_key_map: Dict[str, Optional[str]] = field(default_factory=dict)
    members: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    calls: List[CallLog] = field(default_factory=list)


def _canonical_keys(categories: Sequence[Dict[str, Any]]) -> List[str]:
    return [c["key"] for c in categories if c.get("key")]


def reconcile_taxonomy(
    seed, skill, scopes, vocabulary, calls: List[CallLog]
) -> Dict[str, Any]:
    prompt = build_reconciliation_prompt(seed, skill, scopes, vocabulary)
    result: LLMResult = call_llm(prompt, response_json=True)
    calls.append(CallLog("reconcile_taxonomy", result.provider, result.model))
    data = loads_json(result.text)
    if not isinstance(data, dict) or not data.get("categories"):
        raise RuntimeError("Reconciliation response did not contain 'categories'")
    return data


def assign_vocabulary(
    categories: Sequence[Dict[str, Any]],
    vocabulary: Sequence[Tuple[str, int]],
    calls: List[CallLog],
    *,
    batch_size: int = 100,
) -> Dict[str, List[Dict[str, Any]]]:
    keys = set(_canonical_keys(categories))
    members: Dict[str, List[Dict[str, Any]]] = {k: [] for k in keys}
    # small stable integer ids per batch, mapped back to the raw string
    for batch in chunked(list(vocabulary), batch_size):
        items = [(i, name) for i, (name, _count) in enumerate(batch)]
        prompt = build_assignment_prompt(categories, items)
        result: LLMResult = call_llm(prompt, response_json=True)
        calls.append(CallLog("assign_vocabulary", result.provider, result.model))
        data = loads_json(result.text)
        assignments = data.get("assignments") if isinstance(data, dict) else None
        if not isinstance(assignments, list):
            raise RuntimeError("Assignment response did not contain 'assignments'")
        by_id = {a.get("item_id"): a.get("category_key") for a in assignments}
        for idx, (name, count) in enumerate(batch):
            key = by_id.get(idx)
            if key not in keys:
                key = "egyéb" if "egyéb" in keys else sorted(keys)[0]
            members.setdefault(key, []).append({"name_hu": name, "count": count})
    return members


def run(database_url: str, out_path: Path, batch_size: int) -> TaxonomyBuild:
    db_path = sqlite_path_from_url(database_url)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    seed = load_seed_categories()
    skill = load_skill_doc_categories()
    scopes = load_scope_buckets()
    vocabulary, _notes = extract_vocabulary(db_path)
    logger.info(
        "Extracted %d distinct name_hu strings from %s", len(vocabulary), db_path
    )

    build = TaxonomyBuild()
    reconciled = reconcile_taxonomy(seed, skill, scopes, vocabulary, build.calls)
    build.categories = reconciled.get("categories", [])
    build.seed_key_map = reconciled.get("seed_key_map", {})
    build.skill_doc_key_map = reconciled.get("skill_doc_key_map", {})
    build.members = assign_vocabulary(
        build.categories, vocabulary, build.calls, batch_size=batch_size
    )

    payload = {
        "methodology": {
            "database": db_path.name,
            "distinct_name_hu": len(vocabulary),
            "batch_size": batch_size,
            "providers_used": sorted({c.provider for c in build.calls}),
            "calls": [asdict(c) for c in build.calls],
        },
        "categories": build.categories,
        "seed_key_map": build.seed_key_map,
        "skill_doc_key_map": build.skill_doc_key_map,
        "members": build.members,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return build


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the canonical category taxonomy")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db"),
    )
    parser.add_argument("--out", default="data/reports/category_taxonomy_clusters.json")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    build = run(args.database_url, Path(args.out), args.batch_size)

    print(f"Canonical categories: {len(build.categories)}")
    for c in build.categories:
        n = len(build.members.get(c["key"], []))
        print(f"  {c['key']:<24} {n:>4} distinct phrasings  scope={c.get('scope_bucket')}")
    print("Providers used:", sorted({c.provider for c in build.calls}))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
