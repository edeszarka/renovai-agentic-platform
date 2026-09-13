#!/usr/bin/env python
"""
Re-ingestion health check — verifies quote dates, year coverage, and count
matching against the raw quote folders. Run after any re-ingestion or at any
time to confirm the DB state is consistent.

Pass/fail on every check; prints a summary at the end.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUOTES_DIR = ROOT / "data" / "raw" / "quotes"
DB_URL = "sqlite+aiosqlite:///data/renovai.db"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def _report(passed: bool, msg: str) -> None:
    global _PASS, _FAIL
    if passed:
        _PASS += 1
        print(f"  PASS  {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL  {msg}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_folder_counts():
    """Every year folder's XLSX count must match the DB Jan-1 count."""
    import asyncio

    from sqlalchemy import text

    from renovai.db.session import get_engine, get_session_maker

    async def _inner():
        eng = get_engine(DB_URL)
        sm = get_session_maker(eng)
        async with sm() as s:
            for y in sorted(
                p.name for p in QUOTES_DIR.iterdir() if p.is_dir() and p.name.isdigit()
            ):
                folder = QUOTES_DIR / y
                xlsx_count = len(list(folder.glob("*.xlsx")))
                r = await s.execute(
                    text("SELECT count(*) FROM quotes WHERE quote_date = :d"),
                    {"d": f"{y}-01-01"},
                )
                db_count = r.scalar()
                ok = db_count == xlsx_count
                _report(
                    ok, f"{y}: {db_count} Jan-1 DB quotes vs {xlsx_count} folder XLSX"
                )
        await eng.dispose()

    asyncio.run(_inner())


def check_all_jan1():
    """No quote_date in the DB may be anything other than a Jan-1 date."""
    import asyncio

    from sqlalchemy import text

    from renovai.db.session import get_engine, get_session_maker

    async def _inner():
        eng = get_engine(DB_URL)
        sm = get_session_maker(eng)
        async with sm() as s:
            r = await s.execute(
                text(
                    "SELECT file_name, quote_date FROM quotes WHERE quote_date IS NULL OR quote_date NOT LIKE '%-01-01'"
                )
            )
            bad = r.fetchall()
            for fname, qd in bad:
                _report(False, f"non-Jan-1 date {qd} for {fname}")
            if not bad:
                _report(True, "all quote_dates are Jan-1 (YYYY-01-01)")
        await eng.dispose()

    asyncio.run(_inner())


def check_total_quotes():
    """DB count minus known orphans should match total XLSX count."""
    import asyncio

    from sqlalchemy import text

    from renovai.db.session import get_engine, get_session_maker

    async def _inner():
        total_xlsx = sum(
            len(list(d.glob("*.xlsx")))
            for d in QUOTES_DIR.iterdir()
            if d.is_dir() and d.name.isdigit()
        )
        eng = get_engine(DB_URL)
        sm = get_session_maker(eng)
        async with sm() as s:
            r = await s.execute(text("SELECT count(*) FROM quotes"))
            db_total = r.scalar()
        _report(
            db_total >= total_xlsx,
            f"DB total {db_total} >= folder total {total_xlsx} (orphans possible)",
        )
        await eng.dispose()

    asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("Ingestion health check\n")
    check_all_jan1()
    check_folder_counts()
    check_total_quotes()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
