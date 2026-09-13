"""Schema validation for renovai/evals/golden_dataset.json.

The dataset is the doc-03 calibration eval corpus (9 original cases + 12
doc-03 expansion cases added in Menet 1 Task 5). Nothing in the current
calculation pipeline can answer these cases yet — that logic lands in Menet 2
(CHAIN_RULES expansion). This test only guarantees the data is structurally
well-formed so the eval harness can consume it later.
"""

import json
from pathlib import Path

import pytest

GOLDEN_DATASET_PATH = (
    Path(__file__).resolve().parents[1] / "renovai" / "evals" / "golden_dataset.json"
)

REQUIRED_CASE_KEYS = {"case_id", "input", "expected_output", "rubric"}
REQUIRED_INPUT_KEYS = {"question_hu", "params"}

# 9 original cases + 12 doc-03 expansion cases.
EXPECTED_CASE_COUNT = 21

# Case ids that must be present after the T5 expansion (doc-03 items 4, 6, 8-13, 15, 16, 18, 19).
DOC03_EXPANSION_CASE_IDS = {
    "drywall_ceiling_010",
    "appliance_package_011",
    "screed_subfloor_concrete_012",
    "old_plaster_rewalling_013",
    "amperage_upgrade_32a_014",
    "multisplit_ac_installation_015",
    "water_outlet_016",
    "monosplit_ac_3_5kw_017",
    "interior_door_installation_018",
    "thermostat_valve_replacement_019",
    "laminate_flooring_installation_020",
    "paint_quantity_estimation_021",
}


@pytest.fixture(scope="module")
def golden_dataset() -> list:
    return json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))


def test_dataset_is_valid_json():
    # Parsing in the fixture already proves it; assert explicitly for clarity.
    data = json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)


def test_exact_case_count(golden_dataset):
    assert len(golden_dataset) == EXPECTED_CASE_COUNT


def test_no_duplicate_case_ids(golden_dataset):
    ids = [case["case_id"] for case in golden_dataset]
    assert len(ids) == len(set(ids)), (
        f"duplicate case_ids: {[i for i in set(ids) if ids.count(i) > 1]}"
    )


def test_all_doc03_expansion_cases_present(golden_dataset):
    ids = {case["case_id"] for case in golden_dataset}
    assert DOC03_EXPANSION_CASE_IDS <= ids


def test_all_cases_have_required_keys(golden_dataset):
    for case in golden_dataset:
        assert REQUIRED_CASE_KEYS <= set(case), (
            f"{case['case_id']}: missing {REQUIRED_CASE_KEYS - set(case)}"
        )
        assert REQUIRED_INPUT_KEYS <= set(case["input"]), (
            f"{case['case_id']}: missing input keys {REQUIRED_INPUT_KEYS - set(case['input'])}"
        )


def test_rubric_is_nonempty_list_of_strings(golden_dataset):
    for case in golden_dataset:
        rubric = case["rubric"]
        assert isinstance(rubric, list) and rubric, (
            f"{case['case_id']}: rubric must be a non-empty list"
        )
        assert all(isinstance(item, str) and item for item in rubric), (
            f"{case['case_id']}: rubric items must be non-empty strings"
        )


def test_params_is_dict(golden_dataset):
    for case in golden_dataset:
        assert isinstance(case["input"]["params"], dict), (
            f"{case['case_id']}: params must be a dict"
        )


def test_huf_ranges_are_well_formed(golden_dataset):
    """Any *_huf_min/_huf_max sibling pair must be ints with min <= max.

    This is a shallow structural check only (recursing into the expected_output
    dict) — it does not evaluate the figures themselves.
    """
    for case in golden_dataset:
        _assert_huf_pairs(case["expected_output"], path=case["case_id"])


def _assert_huf_pairs(node, path):
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        child_path = f"{path}.{key}"
        if key.endswith("_huf_min") or key.endswith("_huf_max"):
            assert isinstance(value, int), (
                f"{child_path}: must be an int, got {value!r}"
            )
        if key.endswith("_huf_min"):
            max_key = key[:-4] + "_max"
            if max_key in node:
                assert isinstance(node[max_key], int), (
                    f"{path}.{max_key}: must be an int"
                )
                assert value <= node[max_key], (
                    f"{path}: {key} ({value}) > {max_key} ({node[max_key]})"
                )
        else:
            _assert_huf_pairs(value, child_path)


def test_appliance_case_carries_pending_reconciliation_note(golden_dataset):
    """Item 6 is deliberately an interim reference — it must be flagged so the
    doc 03 vs doc 04 pricing conflict is not silently baked into eval data."""
    by_id = {case["case_id"]: case for case in golden_dataset}
    case = by_id["appliance_package_011"]
    notes_text = json.dumps(case.get("notes", ""), ensure_ascii=False)
    assert "PENDING RECONCILIATION" in notes_text, (
        "appliance_package_011 must flag the pending doc03/doc04 reconciliation"
    )
