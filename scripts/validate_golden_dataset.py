"""Menet 2 Task 4 — golden-dataset validation pass (21 cases).

Runs the 21 cases from ``renovai/evals/golden_dataset.json`` against the
engine logic built in Menet 2 Tasks 1-3:

  * CHAIN_RULES branch A/B/C      (renovai.predictor.structural_cost)
  * ceiling-height multiplier     (structural_cost.ceiling_height_multiplier)
  * owner-purchased product prices(renovai.predictor.product_pricing)
  * infrastructure minimums       (structural_cost.apply_infrastructure_minimums)

This harness is deliberately honest: it only claims PASS/FAIL for rubric
bullets that are machine-checkable against an *implemented* callable. Bullets
that require a knowledge-base / prose answer (decision-tree narratives that
are NOT implemented as a function in Tasks 1-3) are reported as
"NOT-SCORABLE" with the reason, rather than fabricated as pass/fail.

Item ``appliance_package_011`` is expected to remain flagged
PENDING RECONCILIATION — this script asserts that flag is still present and
does NOT silently score it.

Two-input cases: ``subfloor_leveling_slag_vs_compound_003`` carries TWO named
sub-scenarios (type1 branch A / type2 branch B/C), each with its own input and
expected output, evaluated and reported separately under the same case_id.

Run:
    python -m scripts.validate_golden_dataset
    python -m scripts.validate_golden_dataset --json   (machine-readable)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from renovai.predictor.product_pricing import (
    LAMINATE_PRICES,
    door_install,
    lookup,
)
from renovai.predictor.structural_cost import (
    ceiling_height_multiplier,
    chain_total_cost,
    detect_active_chains,
)

GOLDEN_JSON = Path("renovai/evals/golden_dataset.json")

REF_AREA = 55.0  # CHAIN_RULES calibration reference area (m²) — doc 03 item 3 type 1


def _load_cases() -> list[dict]:
    with open(GOLDEN_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Case-specific checkers.  Each returns (rubric_bullet, ok|fail, detail).
# ---------------------------------------------------------------------------


def check_008(case: dict) -> list[dict]:
    """ajto_padlo_lanc_pre1970_008 — pre-1970 tégla + door/floor triggers the
    full-slag chain. The rubric pins the chain to 2.0-2.85M point 2.425M at the
    55 m² reference (doc 03 item 3 type 1: anyag 0.70-0.85M + munkadíj 1.3-2.0M)."""
    p = case["input"]["params"]
    scope = {k: bool(v) for k, v in p.get("scope_flags", {}).items()}
    era = str(p.get("building_era", ""))
    btype = p.get("building_type")
    active = detect_active_chains(
        scope, era, floor_number=1, gas_heating=False, building_type=btype
    )
    ids = [c["id"] for c in active]
    cost_ref = chain_total_cost(active, area_sqm=REF_AREA)["point"]
    cost_case = chain_total_cost(active, area_sqm=float(p.get("area_sqm", 55)))["point"]

    return [
        {
            "bullet": "chain_active / must_identify_slag_chain_is_triggered",
            "ok": "ajto_padlo_lanc" in ids,
            "detail": f"active chains={ids}",
        },
        {
            "bullet": "chain_id == ajto_padlo_lanc",
            "ok": "ajto_padlo_lanc" in ids,
            "detail": f"active={ids}",
        },
        {
            "bullet": "chain_cost 2.0-2.85M point 2.425M (at 55 m² reference)",
            "ok": 2_000_000 <= cost_ref <= 2_850_000,
            "detail": f"point@{REF_AREA}m²={cost_ref:,}",
        },
        {
            "bullet": "chain figures consistent with stated area (55 m²)",
            "ok": 2_000_000 <= cost_case <= 2_850_000,
            "detail": f"point@{p.get('area_sqm', 55)}m²={cost_case:,} (area-scaled)",
        },
    ]


def check_003(case: dict) -> list[dict]:
    """subfloor_leveling_slag_vs_compound_003 — TWO sub-scenarios under one
    case_id (branch A: full-slag ajto_padlo_lanc; branch B/C: misung). Each
    sub-scenario is its own input/expected output, evaluated separately.

    type1 (kohósalak/homok)  → branch A pre-1970 tégla → ajto_padlo_lanc,
        doc 03 item 3 type 1: 2.0-2.85M at 55 m².
    type2 (ragasztó/misung)  → branch B/C post-1970 tégla → misung_subfloor,
        doc 03 item 3 type 2: 1.25-1.6M at 55 m².
    """
    bullets = []
    sub = {
        "type1_slag_sand": {
            "era": "1960",
            "btype": "tégla",
            "scope": {"flooring": True, "demolition": True},
            "chain": "ajto_padlo_lanc",
            "lo": 2_000_000,
            "hi": 2_850_000,
            "src": "doc 03 item 3 type 1",
        },
        "type2_adhesive_misung": {
            "era": "1985",
            "btype": "tégla",
            "scope": {"flooring": True, "demolition": True},
            "chain": "misung_subfloor_leveling",
            "lo": 1_250_000,
            "hi": 1_600_000,
            "src": "doc 03 item 3 type 2",
        },
    }
    for name, cfg in sub.items():
        active = detect_active_chains(
            cfg["scope"],
            cfg["era"],
            floor_number=1,
            gas_heating=False,
            building_type=cfg["btype"],
        )
        ids = [c["id"] for c in active]
        cost = chain_total_cost(active, area_sqm=REF_AREA)["point"]
        bullets.append(
            {
                "bullet": f"{name}: chain_active == {cfg['chain']}",
                "ok": cfg["chain"] in ids,
                "detail": f"active={ids} (branch {'A' if cfg['chain'] == 'ajto_padlo_lanc' else 'B/C'})",
            }
        )
        bullets.append(
            {
                "bullet": f"{name}: chain cost {cfg['lo'] / 1e6:.2f}-{cfg['hi'] / 1e6:.2f}M at {REF_AREA:.0f} m² ({cfg['src']})",
                "ok": cfg["lo"] <= cost <= cfg["hi"],
                "detail": f"point@{REF_AREA:.0f}m²={cost:,}",
            }
        )
    return bullets


def check_009(case: dict) -> list[dict]:
    """high_ceiling_painting_plastering_009 — height multiplier 3.5/2.75 ≈ 1.27."""
    p = case["input"]["params"]
    h = float(p.get("ceiling_height", 3.5))
    mult = ceiling_height_multiplier(h)
    ref = ceiling_height_multiplier(2.75)
    factor = mult / ref
    return [
        {
            "bullet": "height_multiplier == 3.5",
            "ok": abs(mult - 3.5) < 1e-9,
            "detail": f"multiplier({h})={mult}",
        },
        {
            "bullet": "reference_multiplier == 2.75",
            "ok": abs(ref - 2.75) < 1e-9,
            "detail": f"ref(2.75)={ref}",
        },
        {
            "bullet": "labor_increase_factor ≈ 1.27",
            "ok": 1.25 <= factor <= 1.29,
            "detail": f"factor={factor:.3f}",
        },
    ]


def check_018(case: dict) -> list[dict]:
    """interior_door_installation_018 — CPL foil door + install, all variants.
    Mapped to product_pricing.DOOR_TYPES (doc 04 §4 canonical)."""
    exp = case["expected_output"]
    bullets = []
    table = {
        "exterior_hinged_1m": ("kulso_zsaneros_1m", exp["exterior_hinged_1m"]),
        "interior_hinged_2m": ("belso_zsaneros_2m", exp["interior_hinged_2m"]),
        "single_leaf_sliding_1m": (
            "egyszarnyu_toloajto_1m",
            exp["single_leaf_sliding_1m"],
        ),
        "double_leaf_sliding_2m": (
            "ketszarnyu_toloajto_2m",
            exp["double_leaf_sliding_2m"],
        ),
    }
    for name, (dkey, e) in table.items():
        d, i = (
            lookup("door", "also", door_type=dkey, glass="without_glass"),
            door_install(dkey, "without_glass"),
        )
        gd = lookup("door", "also", door_type=dkey, glass="with_glass")
        gi = door_install(dkey, "with_glass")
        ok = (
            d == (e["no_glass_door_huf_min"], e["no_glass_door_huf_max"])
            and i == (e["no_glass_install_huf_min"], e["no_glass_install_huf_max"])
            and gd == (e["with_glass_door_huf_min"], e["with_glass_door_huf_max"])
            and gi == (e["with_glass_install_huf_min"], e["with_glass_install_huf_max"])
        )
        bullets.append(
            {
                "bullet": f"must_return_exact_{name}_ranges",
                "ok": ok,
                "detail": f"{dkey} catalog matches doc-04 golden figures",
            }
        )
    return bullets


def check_020(case: dict) -> list[dict]:
    """laminate_flooring_installation_020 — install labor + adhesive.
    These are CONTRACTOR labor figures in pricing.md, not a callable; the
    owner-side laminate material price is implemented in product_pricing."""
    bullets = [
        {
            "bullet": "material price exists for 7-12mm owner-purchased laminate",
            "ok": all(k in LAMINATE_PRICES for k in ("7mm", "8mm", "10mm", "12mm")),
            "detail": "product_pricing.LAMINATE_PRICES present (owner side)",
        },
        {
            "bullet": "install labor 5.5k-7.5k /sqm + adhesive 9k-14k (contractor)",
            "ok": False,
            "detail": "NOT-SCORABLE: install labor lives in pricing.md text only; "
            "no callable implements it in Tasks 1-3.",
        },
    ]
    return bullets


def check_011(case: dict) -> list[dict]:
    """appliance_package_011 — must stay PENDING RECONCILIATION, NOT resolved."""
    notes = case.get("notes", [])
    pending = any("PENDING RECONCILIATION" in n for n in notes)
    return [
        {
            "bullet": "case still flagged PENDING RECONCILIATION",
            "ok": pending,
            "detail": "note present, not silently resolved. Doc 04 tiered figures are "
            "canonical in product_pricing; appliance case uses doc 03 flat "
            "interim reference — NOT scored.",
        },
        {
            "bullet": "appliance figures match product_pricing tiers",
            "ok": False,
            "detail": "NOT-SCORABLE by design: doc03-vs-doc04 conflict stays pending; "
            "no single tier contains the doc 03 flat ranges.",
        },
    ]


def check_generic(case: dict) -> list[dict]:
    """Default for cases with no implemented callable — honest NOT-SCORABLE."""
    ruby = case.get("rubric", [])
    return [
        {
            "bullet": f"all {len(ruby)} rubric bullets machine-checkable",
            "ok": False,
            "detail": "NOT-SCORABLE: requires knowledge-base/prose answer not implemented "
            "as a callable in Tasks 1-3. Logged as not-scored, not as a pass/fail.",
        },
    ]


# case_id -> checker
_CHECKERS = {
    "subfloor_leveling_slag_vs_compound_003": check_003,
    "ajto_padlo_lanc_pre1970_008": check_008,
    "high_ceiling_painting_plastering_009": check_009,
    "interior_door_installation_018": check_018,
    "laminate_flooring_installation_020": check_020,
    "appliance_package_011": check_011,
}

KNOWN_NOT_SCOPED = {
    "wall_floor_reinforcement_001": "prose decision-tree; no callable (masonry pre/post-1920 figures in pricing.md)",
    "sawdust_wallpaper_vs_lime_002": "prose; wall-prep extra costs in pricing.md, no callable",
    "tile_size_cost_comparison_004": "tile labor-by-size in pricing.md; no callable",
    "cement_vs_dispersion_waterproofing_005": "prose; figures in pricing.md, no callable",
    "window_spaletta_restoration_006": "prose; spaletta figures in pricing.md, no callable",
    "wall_debris_weight_estimation_007": "prose; debris figures in pricing.md, no callable",
    "drywall_ceiling_010": "drywall table in pricing.md; no callable",
    "screed_subfloor_concrete_012": "screed table in pricing.md; no callable",
    "old_plaster_rewalling_013": "TODO — no HUF figures (doc 03 #9), plus prose",
    "amperage_upgrade_32a_014": "flat constant 300k ≠ doc 03 260-320k range; no callable",
    "multisplit_ac_installation_015": "prose; no callable",
    "water_outlet_016": "prose; no callable",
    "monosplit_ac_3_5kw_017": "prose; no callable",
    "thermostat_valve_replacement_019": "prose; no callable",
    "paint_quantity_estimation_021": "paint-qty in pricing.md; no callable",
}


def run_all() -> list[dict]:
    cases = _load_cases()
    results = []
    for case in cases:
        cid = case["case_id"]
        checker = _CHECKERS.get(cid)
        if checker is not None:
            bullets = checker(case)
            status = "PASS" if all(b["ok"] for b in bullets) else "FAIL"
            if cid == "appliance_package_011":
                # pending reconciliation: only the flag bullet is meaningful.
                flag_ok = bullets[0]["ok"]
                status = "PASS-FLAGGED" if flag_ok else "FAIL-FLAG-DROPPED"
            results.append(
                {
                    "case_id": cid,
                    "status": status,
                    "bullets": bullets,
                    "reason": "engine-mapped check",
                }
            )
            continue
        # Generic not-scoped case.
        bullets = check_generic(case)
        results.append(
            {
                "case_id": cid,
                "status": "NOT-SCORABLE",
                "bullets": bullets,
                "reason": KNOWN_NOT_SCOPED.get(cid, "no checker mapped"),
            }
        )
    return results


def _fmt_table(results: list[dict]) -> str:
    lines = ["case_id | status | reason"]
    lines.append("--------|--------|-------")
    for r in results:
        lines.append(f"{r['case_id']} | {r['status']} | {r['reason']}")
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines.append("")
    lines.append("SUMMARY: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()
    results = run_all()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(_fmt_table(results))
        print("\n-- bullet detail for engine-mapped cases --")
        for r in results:
            if r["bullets"] and r["reason"] == "engine-mapped check":
                for b in r["bullets"]:
                    print(
                        f"  [{r['case_id']}] {'PASS' if b['ok'] else 'FAIL'} "
                        f"· {b['bullet']} · {b['detail']}"
                    )


if __name__ == "__main__":
    main()
