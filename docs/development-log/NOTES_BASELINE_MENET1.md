# NOTES_BASELINE_MENET1.md — Pre-Menet1 test baseline

Captured on branch `feature/building-taxonomy-data-layer` (created from
`refactor/phase-0-streamlit-canonical` @ `9a45dd2`). **No files modified yet**
(other than this file and `FAZIS_A_FINDINGS.md`, which is the Fázis A report and
is untracked).

## Branch provenance (explicit, per session instructions)

`main` is at `3d84872` and does **not** yet contain
`refactor/phase-0-streamlit-canonical` (merge-base of the two branches is
`3d84872` = main HEAD; the refactor is 8 commits ahead). Therefore, per the
mandatory git workflow, this session branches from
`refactor/phase-0-streamlit-canonical` instead of `main`, and that fact is
noted here.

## Command

```
& .\.venv\Scripts\python.exe -m pytest --tb=short -q
```

## Result

```
137 passed, 4 skipped, 8 warnings in 11.91s
```

## Skips (pre-existing, non-fatal)

| Test | Reason |
|---|---|
| `tests/test_quote_parser.py:52` | Fixture missing |
| `tests/test_quote_parser.py:62` | Fixture missing |
| `tests/test_quote_parser.py:70` | Fixture missing |
| `tests/test_quote_parser.py:79` | Fixture missing |

## Notes

- This baseline is **cleaner** than the pre-refactor baseline recorded in
  `NOTES_BASELINE.md` (which had 1 failed + 2 errors + 127 passed): the
  refactor branch resolved the legacy-path ADK/Vertex failures and the stale
  `tests/test_price_model.py` collection error is gone.
- Warnings are dependency deprecation warnings only (google-generativeai,
  chromadb, sqlalchemy utcnow) — not related to this session's work.
- No changes are made to any "DO NOT TOUCH" files
  (`structural_cost.py`, `price_model.py` weighting logic) in this session.
