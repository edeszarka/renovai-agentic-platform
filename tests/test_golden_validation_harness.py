"""Menet 2 Task 4 — guarded invariants for the golden-dataset validation pass.

Runs ``scripts.validate_golden_dataset`` against the 21-case golden set and
asserts the *honest* invariants that hold regardless of which specific cases
are implemented:

  * all 21 cases are processed (none dropped),
  * the ceiling-height case, the interior-door case and the two-input
    subfloor case PASS (implemented),
  * the appliance case stays flagged PENDING RECONCILIATION (never silently
    scored/resolved),
  * every case is reported under exactly one status.

These are intentionally not "100% pass" assertions — most golden cases are
prose/knowledge-base answers that Tasks 1-3 do not implement as callables, so
an honest report leaves them NOT-SCORABLE.
"""

from __future__ import annotations

from scripts.validate_golden_dataset import run_all


def test_all_21_cases_are_processed() -> None:
    results = run_all()
    assert len(results) == 21, f"expected 21 golden cases, got {len(results)}"
    ids = {r["case_id"] for r in results}
    assert len(ids) == 21, "duplicate case_id in results"


def test_each_case_has_exactly_one_status() -> None:
    results = run_all()
    valid = {"PASS", "FAIL", "NOT-SCORABLE", "PASS-FLAGGED", "FAIL-FLAG-DROPPED"}
    for r in results:
        assert r["status"] in valid, f"{r['case_id']}: bad status {r['status']}"


def test_ceiling_height_case_passes() -> None:
    by_id = {r["case_id"]: r for r in run_all()}
    assert by_id["high_ceiling_painting_plastering_009"]["status"] == "PASS"


def test_interior_door_case_passes_against_doc04() -> None:
    by_id = {r["case_id"]: r for r in run_all()}
    r = by_id["interior_door_installation_018"]
    assert r["status"] == "PASS"
    assert all(b["ok"] for b in r["bullets"])


def test_appliance_case_stays_pending_reconciliation() -> None:
    by_id = {r["case_id"]: r for r in run_all()}
    r = by_id["appliance_package_011"]
    # Must NOT be silently resolved into PASS outright; must be flagged.
    assert r["status"] in ("PASS-FLAGGED", "FAIL-FLAG-DROPPED")
    assert r["bullets"][0]["ok"] is True  # the PENDING flag is preserved


def test_subfloor_two_input_case_passes_both_scenarios() -> None:
    """003 is a two-input case: type1 (branch A) + type2 (branch B/C) are
    evaluated separately and both must pass under one case_id."""
    by_id = {r["case_id"]: r for r in run_all()}
    r = by_id["subfloor_leveling_slag_vs_compound_003"]
    assert r["status"] == "PASS"
    bullets = {b["bullet"].split(":")[0]: b for b in r["bullets"]}
    assert bullets["type1_slag_sand"]["ok"] is True
    assert bullets["type2_adhesive_misung"]["ok"] is True
    assert len(r["bullets"]) == 4  # 2 bullets per sub-scenario
