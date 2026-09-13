"""Task C — generate the human-readable reclassification report.

Reads the corpus (read-only), the Task-A backfill stats and the Task-B
re-examination summary, and writes ``docs/reclassification_report.md``.

Usage::

    python -m scripts.build_reclassification_report \
        --database-url sqlite+aiosqlite:///data/renovai.db \
        --out docs/reclassification_report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from dotenv import load_dotenv

from scripts.line_item_classifier import ascii_key

load_dotenv()

BACKFILL_STATS = Path("data/reports/backfill_stats.json")
SZIGETELES_REEXAM = Path("data/reports/szigeteles_reexam.json")


def sqlite_path_from_url(database_url: str) -> Path:
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if database_url.startswith(prefix):
            return Path(database_url[len(prefix) :])
    raise ValueError(f"Expected a file-backed SQLite URL, got {database_url!r}")


def load_histograms(db_path: Path) -> Tuple[Dict[str, int], Dict[str, int]]:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        before = {
            ascii_key(k) or "egyeb": n
            for k, n in con.execute(
                "SELECT category_key, COUNT(*) FROM line_items GROUP BY category_key"
            )
        }
        after: Dict[str, int] = {}
        for k, n in con.execute(
            "SELECT category_key_v2, COUNT(*) FROM line_items GROUP BY category_key_v2"
        ):
            after["needs_human_review" if k is None else k] = n
    finally:
        con.close()
    return before, after


def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(
    db_path: Path, backfill: Optional[dict], reexam: Optional[dict]
) -> str:
    before, after = load_histograms(db_path)
    total = sum(before.values())

    lines: List[str] = []
    lines.append("# Line-item reclassification report (Phase 2)")
    lines.append("")
    lines.append(
        "Backfill of the canonical taxonomy (`docs/category_taxonomy.md`) onto every "
        "existing line item, plus the repo-owner's special re-examination of the "
        "`szigeteles` category. The legacy `category_key` column is untouched; all "
        "new assignments live in `line_items.category_key_v2`."
    )
    lines.append("")

    # --- Task A ---
    lines.append("## Task A — backfill tier split")
    lines.append("")
    if backfill:
        tiers = backfill.get("tier_counts", {})
        lines.append(
            f"Total line items backfilled: **{backfill.get('total_items', total)}**"
        )
        lines.append("")
        lines.append("| Tier | Items |")
        lines.append("|------|------:|")
        for tier in ("lookup", "groq", "deepseek", "unresolved"):
            lines.append(f"| {tier} | {tiers.get(tier, 0)} |")
        lines.append("")
        lookup_n = tiers.get("lookup", 0)
        total_n = backfill.get("total_items", total) or 1
        lines.append(
            f"**{lookup_n}/{total_n} ({lookup_n / total_n:.1%}) were resolved by the "
            "tier-1 exact lookup — no LLM call was made for the existing corpus.** "
            "The Groq/DeepSeek tiers exist for future ingestion of new `name_hu` "
            "strings that are not yet in the mapping."
        )
        if backfill.get("unresolved"):
            lines.append("")
            lines.append(f"Unresolved (left NULL): {len(backfill['unresolved'])}")
    else:
        lines.append(
            "_backfill stats not found; run `scripts/backfill_category_v2.py` first._"
        )
    lines.append("")

    # --- Task B ---
    lines.append("## Task B — `szigeteles` re-examination")
    lines.append("")
    if reexam:
        outcomes = reexam.get("outcome_counts", {})
        lines.append(f"Items re-examined: **{reexam.get('total_reexamined', 0)}**")
        lines.append("")
        lines.append("| Outcome | Items |")
        lines.append("|---------|------:|")
        for key in ("szigeteles", "futes_rendszer", "furdo", "needs_human_review"):
            lines.append(f"| {key} | {outcomes.get(key, 0)} |")
        lines.append("")
        lines.append(
            f"Provider tiers used for the re-examination: `{reexam.get('tier_counts', {})}`"
        )
        lines.append("")
        review = reexam.get("needs_human_review", [])
        lines.append(f"### Flagged `needs_human_review` ({len(review)})")
        lines.append("")
        if review:
            lines.append("| raw `name_hu` | LLM category | reason |")
            lines.append("|---------------|--------------|--------|")
            for row in review:
                reason = (row.get("reason") or "").replace("|", "/").strip()
                lines.append(
                    f"| `{row.get('name_hu', '')}` | {row.get('llm_category') or '—'} | {reason} |"
                )
        else:
            lines.append("_none_")
        lines.append("")
        stayed = reexam.get("stayed_szigeteles", [])
        if stayed:
            lines.append("Kept as `szigeteles` (interior thermal/acoustic insulation):")
            for name in stayed:
                lines.append(f"- `{name}`")
            lines.append("")
    else:
        lines.append(
            "_re-examination summary not found; run `scripts/reexamine_szigeteles.py` first._"
        )
    lines.append("")

    # --- Histogram ---
    lines.append("## Category histogram: before vs after")
    lines.append("")
    lines.append(
        "Before = legacy `category_key` (produced by `repository.assign_category()`), "
        "normalised to ASCII for comparison. After = new `category_key_v2`. "
        "`needs_human_review` is stored as SQL NULL."
    )
    lines.append("")
    all_keys = sorted(
        set(before) | set(after), key=lambda k: -max(before.get(k, 0), after.get(k, 0))
    )
    lines.append("| category | before | after | Δ |")
    lines.append("|----------|-------:|------:|---:|")
    for key in all_keys:
        b = before.get(key, 0)
        a = after.get(key, 0)
        lines.append(f"| {key} | {b} | {a} | {a - b:+d} |")
    lines.append(
        f"| **total** | **{sum(before.values())}** | **{sum(after.values())}** | |"
    )
    lines.append("")
    eb_before = before.get("egyeb", 0)
    eb_after = after.get("egyeb", 0)
    lines.append(
        f"**`egyeb` catch-all: {eb_before} → {eb_after} "
        f"({eb_after - eb_before:+d}, "
        f"{(eb_after - eb_before) / eb_before:.0%} change).**"
    )
    lines.append("")

    lines.append("## Notes / out of scope")
    lines.append("")
    lines.append(
        "- The LLM was used for text classification only; no price arithmetic was "
        "performed here."
    )
    lines.append(
        "- `renovai/predictor/price_model.py::SCOPE_CATEGORY_MAP` is unchanged; the "
        "canonical categories feed the existing 7 scope buckets as documented."
    )
    lines.append(
        "- Backlog (not actioned): air-conditioning (`klima`) costs likely should not "
        "scale with total apartment area the way other categories do — AC pricing is "
        "closer to a per-unit/per-capacity cost, similar to how "
        "`renovai/predictor/structural_cost.py` keeps certain fixed-cost structural "
        "add-ons outside the per-sqm scaling model. This is a future pricing-model "
        "change, not a categorization change."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the reclassification report (Task C)"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db"),
    )
    parser.add_argument("--out", default="docs/reclassification_report.md")
    parser.add_argument("--backfill-stats", default=str(BACKFILL_STATS))
    parser.add_argument("--reexam", default=str(SZIGETELES_REEXAM))
    args = parser.parse_args(argv)

    db_path = sqlite_path_from_url(args.database_url)
    report = build_report(
        db_path,
        _load_json(Path(args.backfill_stats)),
        _load_json(Path(args.reexam)),
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
