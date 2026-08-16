# Development Log

Session-by-session development notes. Live reference docs (README.md, docs/ARCHITECTURE.md, BACKLOG.md) stay at the repo root; this folder holds historical session logs only.

- PHASE_0_SUMMARY.md = canonical Streamlit + orchestrator.handlers path established, legacy entry points quarantined under legacy/.
- NOTES_BASELINE.md = pre-refactor test baseline (1 failed + 2 errors) recorded before the Phase 0 cleanup.
- FAZIS_A_FINDINGS.md = read-only investigation: building era/type/material live only in quote filenames + notes text, not structured columns.
- NOTES_BASELINE_MENET1.md = pre-Menet-1 test baseline (137 passed, 4 skipped).
- MENET1_SUMMARY.md = building_type/era data layer: DB schema, backfill, UI wiring, golden dataset expanded 9 → 21 cases.
- MENET2_SUMMARY.md = CHAIN_RULES branch logic, type × era weighted similarity matching, product pricing catalog, golden-dataset validation (4/21 scorable, rest future work).
- MENET2_TASK2_VERIFICATION.md = self-review of the Task 2 type × era weighting change before Task 3.
- MENET2_TASK2B_SUMMARY.md = backfill building_type fix (5/47 → 47/47 corpus coverage) + VÁLYOG taxonomy removal.
- MENET2_TASK2C_SUMMARY.md = floor-number regex fix, elevator presence investigation, renovation_completeness capture.