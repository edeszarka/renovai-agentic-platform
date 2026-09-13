"""Task B — special-case re-examination of the ``szigeteles`` category.

Every line item whose Phase-1 lookup result is ``szigeteles`` (or whose raw
text clearly mentions chimney lining) is re-classified via the LLM tiers
(Groq → DeepSeek) with the repo-owner's explicit rules, then the deterministic
rule guards in ``scripts/line_item_classifier.py`` are applied:

* chimney lining -> ``futes_rendszer``
* bathroom/wet-room waterproofing -> ``furdo``
* other/ambiguous waterproofing -> ``needs_human_review`` (column set to NULL)
* unambiguous interior thermal/acoustic insulation -> stays ``szigeteles``

Usage::

    python -m scripts.reexamine_szigeteles \
        --database-url sqlite+aiosqlite:///data/renovai.db \
        --out data/reports/szigeteles_reexam.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Optional, Sequence

from dotenv import load_dotenv
from sqlalchemy import select

from renovai.db.models import LineItemORM
from renovai.db.session import get_engine, get_session_maker
from scripts.line_item_classifier import (
    SzigetelesItem,
    default_providers,
    mentions_chimney_lining,
    reexamine_szigeteles_items,
)

load_dotenv()
logger = logging.getLogger("renovai.reexam_szigeteles")

PROVISIONAL = "szigeteles"


async def reexamine(database_url: str, *, dry_run: bool = False) -> Dict[str, object]:
    providers = default_providers()
    engine = get_engine(database_url)
    sm = get_session_maker(engine)
    try:
        async with sm() as session:
            rows = (await session.execute(select(LineItemORM))).scalars().all()
            targets = [
                row
                for row in rows
                if row.category_key_v2 == PROVISIONAL
                or mentions_chimney_lining(row.name_hu, row.notes_hu)
            ]
            items = [
                SzigetelesItem(
                    item_id=(row.id.hex if hasattr(row.id, "hex") else str(row.id)),
                    name_hu=row.name_hu,
                    notes_hu=row.notes_hu,
                )
                for row in targets
            ]
            results = reexamine_szigeteles_items(items, providers=providers)

            by_id = {r.item_id: r for r in results}
            for row in targets:
                key = row.id.hex if hasattr(row.id, "hex") else str(row.id)
                outcome = by_id.get(key)
                if outcome is None:
                    continue
                if not dry_run:
                    row.category_key_v2 = outcome.final_category  # None => needs review
            if not dry_run:
                await session.commit()
    finally:
        await engine.dispose()

    moved = Counter(
        "needs_human_review" if r.needs_human_review else r.final_category
        for r in results
    )
    tier_counts = Counter(r.tier for r in results)
    review_items = [
        {"name_hu": r.name_hu, "reason": r.reason, "llm_category": r.llm_category}
        for r in results
        if r.needs_human_review
    ]
    return {
        "total_reexamined": len(results),
        "outcome_counts": dict(moved),
        "tier_counts": dict(tier_counts),
        "providers_configured": [p.name for p in providers],
        "moved_to_futes_rendszer": [
            r.name_hu for r in results if r.final_category == "futes_rendszer"
        ],
        "moved_to_furdo": [r.name_hu for r in results if r.final_category == "furdo"],
        "stayed_szigeteles": [
            r.name_hu for r in results if r.final_category == "szigeteles"
        ],
        "needs_human_review": review_items,
        "dry_run": dry_run,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Re-examine szigeteles items (Task B)")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db"),
    )
    parser.add_argument("--out", default="data/reports/szigeteles_reexam.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    summary = asyncio.run(reexamine(args.database_url, dry_run=args.dry_run))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"Re-examined {summary['total_reexamined']} items (dry_run={summary['dry_run']})"
    )
    for outcome, count in sorted(
        summary["outcome_counts"].items(), key=lambda kv: -kv[1]
    ):
        print(f"  -> {outcome:<20} {count}")
    print(f"  tiers: {summary['tier_counts']}")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
