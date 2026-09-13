"""Task A — bulk backfill of ``line_items.category_key_v2``.

For every line item in the corpus, resolve its canonical category via the
three-tier classifier (lookup → Groq → DeepSeek; see
``scripts/line_item_classifier.py``) and write the result to the new,
additive ``category_key_v2`` column.  The legacy ``category_key`` column is
never touched.

For the current 1,056 items this is expected to be ~100% tier-1 lookup (every
raw ``name_hu`` is already in the Phase-1 mapping); the LLM tiers matter for
future ingestion of new quotes.

Usage::

    python -m scripts.backfill_category_v2 \
        --database-url sqlite+aiosqlite:///data/renovai.db \
        --stats-out data/reports/backfill_stats.json
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
from typing import Dict, List, Optional, Sequence

from dotenv import load_dotenv
from sqlalchemy import select

from renovai.db.models import LineItemORM
from renovai.db.session import get_engine, get_session_maker
from scripts.line_item_classifier import (
    classify_line_item,
    default_providers,
    load_lookup,
    PHASE1_JSON_DEFAULT,
)

load_dotenv()
logger = logging.getLogger("renovai.backfill_v2")


async def backfill(
    database_url: str,
    lookup: Dict[str, str],
    *,
    dry_run: bool = False,
) -> Dict[str, object]:
    providers = default_providers()
    engine = get_engine(database_url)
    sm = get_session_maker(engine)
    tier_counts: Counter = Counter()
    unresolved: List[dict] = []
    non_lookup: List[dict] = []
    total = 0
    try:
        async with sm() as session:
            rows = (await session.execute(select(LineItemORM))).scalars().all()
            for row in rows:
                total += 1
                result = classify_line_item(
                    row.name_hu, lookup=lookup, providers=providers, notes_hu=row.notes_hu
                )
                tier_counts[result.tier] += 1
                if result.tier != "lookup":
                    non_lookup.append(
                        {
                            "name_hu": row.name_hu,
                            "tier": result.tier,
                            "category_key_v2": result.category_key,
                        }
                    )
                if result.category_key is None:
                    unresolved.append({"name_hu": row.name_hu})
                if not dry_run:
                    row.category_key_v2 = result.category_key
            if not dry_run:
                await session.commit()
    finally:
        await engine.dispose()

    return {
        "total_items": total,
        "tier_counts": dict(tier_counts),
        "lookup_entries": len(lookup),
        "providers_configured": [p.name for p in providers],
        "unresolved": unresolved,
        "non_lookup": non_lookup,
        "dry_run": dry_run,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill category_key_v2 (Task A)")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db"),
    )
    parser.add_argument("--lookup", default=str(PHASE1_JSON_DEFAULT))
    parser.add_argument("--stats-out", default="data/reports/backfill_stats.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    lookup = load_lookup(args.lookup)
    stats = asyncio.run(backfill(args.database_url, lookup, dry_run=args.dry_run))

    out_path = Path(args.stats_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Backfilled {stats['total_items']} line items (dry_run={stats['dry_run']})")
    for tier in ("lookup", "groq", "deepseek", "unresolved"):
        print(f"  {tier:<11} {stats['tier_counts'].get(tier, 0)}")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
