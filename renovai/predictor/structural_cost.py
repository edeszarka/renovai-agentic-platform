import logging
from datetime import date
from typing import Dict, List, Tuple, Optional, Any

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structural add-on inflation: these constants are dated to the 2025 expert
# reference. They get their OWN inflation factor (2025 -> target_date),
# separate from the per-quote corpus inflation, since they come from a
# single dated expert source rather than dated corpus quotes.
# ---------------------------------------------------------------------------

_STRUCTURAL_SOURCE_YEAR = 2025


def _structural_inflation_factor(target_date: date, price_index: Any) -> float:
    """Blended CPI factor from Jan 1 2025 to target_date for structural add-ons.

    Uses the same 55/45 labor/materials blend convention as the wider
    codebase (inflation_calc.inflate_quote_value), treating 2025-01-01 as
    the source date.
    """
    from ..ingestion.inflation_calc import compound_inflation_factor

    f_labor = compound_inflation_factor(
        _STRUCTURAL_SOURCE_YEAR, target_date, price_index, "labor"
    )
    f_materials = compound_inflation_factor(
        _STRUCTURAL_SOURCE_YEAR, target_date, price_index, "materials"
    )
    labor_share = 0.55
    return labor_share * f_labor + (1.0 - labor_share) * f_materials


def _inflate_structural_value(value_huf: float, target_date: date, price_index: Any) -> int:
    return int(round(value_huf * _structural_inflation_factor(target_date, price_index)))

# ---------------------------------------------------------------------------
# 1. Ceiling-height labor multiplier (painting, plastering, skimming only)
# ---------------------------------------------------------------------------
# Source: professional renovation engineer (Hungarian), 2025
# These trades scale by WALL SURFACE AREA (vertical extent), not floor area.

DEFAULT_CEILING_HEIGHT = 2.75

def ceiling_height_multiplier(height_m: float) -> float:
    if height_m <= 0:
        return 1.0
    if height_m <= 2.5:
        return 2.5
    if height_m <= 3.0:
        return 2.75  # default for standard range
    if height_m <= 3.2:
        return 3.0   # interpolate between 2.75 and 3.5
    if height_m <= 4.0:
        return 3.5
    return 4.0

def apply_height_surcharge(
    height_m: Optional[float],
    area_sqm: float,
    painting_plastering_base_labor: float,
) -> Tuple[float, float]:
    """Apply ceiling-height multiplier to painting/plastering labor.

    Returns (adjusted_labor, surcharge_amount).
    The multiplier is applied only to labor for painting, plastering, and skimming.
    Material costs for these trades are NOT affected by ceiling height.
    """
    h = height_m if (height_m and height_m > 0) else DEFAULT_CEILING_HEIGHT
    mult = ceiling_height_multiplier(h)
    ref_mult = ceiling_height_multiplier(2.75)  # baseline reference
    if mult <= ref_mult:
        return (painting_plastering_base_labor, 0.0)
    adjusted = painting_plastering_base_labor * (mult / ref_mult)
    surcharge = adjusted - painting_plastering_base_labor
    logger.debug(
        "height_surcharge: height=%s, mult=%.2f, base=%.0f, adj=%.0f, surcharge=%.0f",
        h, mult, painting_plastering_base_labor, adjusted, surcharge,
    )
    return (adjusted, surcharge)


# ---------------------------------------------------------------------------
# 2. Cascading cost chain rule table
# ---------------------------------------------------------------------------
# Source: professional renovation engineer (Hungarian), 2025
# Each chain: (trigger_condition, chain_steps, cost_low, cost_high, cost_point, description)

ChainEntry = Dict[str, Any]

def _era_pre_1970(building_era: Optional[str]) -> bool:
    if not building_era:
        return False
    try:
        year = int(building_era)
        return year < 1970
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# 2a. Doc 02 A/B/C branch selection (era + building_type)
# ---------------------------------------------------------------------------
# Source: 02_felujitasi_agrendszer_epulettipus_szerint.md (doc 02)
#   A: 1970 előtt épült tégla                          (doc 02 §A)
#   B: 1970–1990/95 tégla + tégla falazatú csúszózsalus (doc 02 §B)
#   C: panel + 1960/70 után épült csúszózsalus (beton)  (doc 02 §C)
# The branch matters to cost chains because A carries a 12–15 cm slag bed
# under the floor, while B/C carry only 1–3 cm misung (doc 02 §A.3 vs §B.1/§C.1).
# Cross-ref: docs/development-log/FAZIS_A_FINDINGS.md §5 rows "Branch selection A/B/C by type+era".


def _normalize_type(building_type: Optional[str]) -> str:
    return (building_type or "").strip().lower()


def _is_branch_a(building_type: Optional[str], building_era: Optional[str]) -> bool:
    """Doc 02 branch A (pre-1970 tégla): full 12–15 cm slag chain applies."""
    if _normalize_type(building_type) in ("panel", "csuszozsalus", "csúszózsalus"):
        return False
    return _era_pre_1970(building_era)


def _is_branch_bc(building_type: Optional[str], building_era: Optional[str]) -> bool:
    """Doc 02 branch B or C: 1–3 cm misung / aljzatkiegyenlítés only, no full slag."""
    if _normalize_type(building_type) in ("panel", "csuszozsalus", "csúszózsalus"):
        return True
    if building_era is None or not str(building_era).strip().isdigit():
        return False
    try:
        return int(building_era) >= 1970
    except (ValueError, TypeError):
        return False


def _is_branch_c(building_type: Optional[str]) -> bool:
    """Doc 02 branch C (panel / beton csúszózsalus): panel-specific constraints."""
    return _normalize_type(building_type) in ("panel", "csuszozsalus", "csúszózsalus")


CHAIN_RULES: List[ChainEntry] = [
    {
        "id": "ajto_padlo_lanc",
        # Doc 02 §A — branch A only. Guarded against B/C by type so the full-slag
        # chain and the misung path below are mutually exclusive (never both fire).
        "trigger": lambda scope, era, floor, gas, btype: (
            _is_branch_a(btype, era)
            and (scope.get("windows_doors", False) or scope.get("flooring", False))
        ),
        "steps": [
            "Ajtócsere/parketta felszedés → salak feltárul",
            "Salak kitermelés és elszállítás",
            "EPS szigetelés (hő- és hangszigetelés)",
            "Esztrich betonozás",
            "Szintkiegyenlítés",
            "Új padló előkészítése",
        ],
        # Area-scaled: base_cost + per_sqm_cost * area
        # Sourced from doc 03 item 3 type 1 (kohósalak/homok alatti egyenes
        # aljzat, 55 nm reference): anyag 0.70-0.85M + munkadíj 1.3-2.0M
        # = 2.0-2.85M HUF total. Calibrated so that at 55 m² the values
        # reproduce that total exactly.
        "base_cost_low": 900_000,
        "per_sqm_cost_low": 20_000,
        "base_cost_high": 1_475_000,
        "per_sqm_cost_high": 25_000,
        "base_cost_point": 1_215_000,
        "per_sqm_cost_point": 22_000,
        "description": (
            "Ajtó/Padló-lánc: régi épületben az ajtócsere vagy parketta felbontása "
            "során előkerülő kohósalak miatt szükséges teljes salakmentesítés, "
            "szigetelés és új aljzat kialakítása."
        ),
        "note": "Sourced from doc 03 item 3 type 1 (kohósalak/homok alatti egyenes aljzat, 55 nm reference): anyag 0.70-0.85M + munkadíj 1.3-2.0M = 2.0-2.85M HUF. Scales with area for EPS, concrete, flooring. Doc 02 §A; docs/development-log/FAZIS_A_FINDINGS.md §5 'Salak 12–15 cm (A) vs 1–3 cm misung (B/C)'.",
    },
    {
        "id": "misung_subfloor_leveling",
        # Doc 02 §B.2/§C.3 — branches B/C only: 1–3 cm aljzatkiegyenlítés with
        # misung, as an alternative to the full-slag ajto_padlo_lanc. Mutually
        # exclusive with that chain: _is_branch_bc() is the exact complement of
        # _is_branch_a() for the same era/type inputs.
        "trigger": lambda scope, era, floor, gas, btype: (
            _is_branch_bc(btype, era)
            and (scope.get("windows_doors", False) or scope.get("flooring", False))
        ),
        "steps": [
            "Padlóburkolat/metlaki felszedés → 1–3 cm misung feltárul",
            "Lecipelés és bontott anyag elszállítás",
            "Padló felcsiszolása (kátrány/szőnyegragasztó eltávolítása)",
            "Mélyalapozás",
            "Aljzatkiegyenlítő kiöntése",
        ],
        # Area-scaled, calibrated against doc 03 item 3 type 2 (parkettaragasztó
        # vagy 1–2 cm misung egyenes aljzat, 55 nm reference): anyag 0.65-0.75M
        # + munkadíj 0.6-0.85M = 1.25-1.6M HUF total. Calibrated so that at
        # 55 m² the values reproduce that total exactly.
        "base_cost_low": 150_000,
        "per_sqm_cost_low": 20_000,
        "base_cost_high": 225_000,
        "per_sqm_cost_high": 25_000,
        "base_cost_point": 187_500,
        "per_sqm_cost_point": 22_500,
        "description": (
            "Misung/aljzatkiegyenlítés-lánc: 1970 utáni tégla, tégla falazatú "
            "csúszózsalus és panel épületekben a padló alatt nincs 12–15 cm salak, "
            "csak 1–3 cm misung — így csiszolás + kiegyenlítés, nem teljes betonozás."
        ),
        "note": "Sourced from doc 03 item 3 type 2 (parkettaragasztó vagy 1–2 cm misung egyenes aljzat, 55 nm reference): anyag 0.65-0.75M + munkadíj 0.6-0.85M = 1.25-1.6M HUF. Scales with area. Doc 02 §B.2/§C.3; docs/development-log/FAZIS_A_FINDINGS.md §5 'Betonozás (A) vs 1–3 cm aljzatkiegyenlítés (B/C)'.",
    },
]

def detect_active_chains(
    scope: Dict[str, bool],
    building_era: Optional[str],
    floor_number: Optional[int] = None,
    gas_heating: Optional[bool] = None,
    building_type: Optional[str] = None,
) -> List[ChainEntry]:
    """Return all chain rules whose trigger condition is met."""
    active = []
    for rule in CHAIN_RULES:
        if rule["trigger"](scope, building_era, floor_number, gas_heating, building_type):
            active.append(rule)
    return active

def chain_total_cost(
    active_chains: List[ChainEntry],
    area_sqm: float = 55.0,
    target_date: Optional[date] = None,
    price_index: Any = None,
) -> Dict[str, int]:
    """Compute total chain cost, area-scaled.

    Each chain's cost = base_cost + per_sqm_cost * area_sqm.
    At the reference area (55 m²) the result matches the original fixed figures
    (doc 03 item 3 type 1 for the full-slag chain).

    Chain constants are dated to the 2025 expert reference; when target_date
    and price_index are supplied the result is inflated 2025 -> target_date.
    With either omitted the raw (undated) constant is returned unchanged.
    """
    low = sum(c["base_cost_low"] + c["per_sqm_cost_low"] * area_sqm for c in active_chains)
    high = sum(c["base_cost_high"] + c["per_sqm_cost_high"] * area_sqm for c in active_chains)
    point = sum(c["base_cost_point"] + c["per_sqm_cost_point"] * area_sqm for c in active_chains)
    if target_date is not None and price_index is not None:
        low = _inflate_structural_value(low, target_date, price_index)
        high = _inflate_structural_value(high, target_date, price_index)
        point = _inflate_structural_value(point, target_date, price_index)
    return {"low": int(low), "high": int(high), "point": int(point)}


# ---------------------------------------------------------------------------
# 2b. Pydantic guard for the chain cost-breakdown output
# ---------------------------------------------------------------------------

class CostBreakdownOutput(BaseModel):
    """Validated cost-breakdown output for the cascading chain rules.

    Guards against the silent-0 bug: if the input scope matches a known
    CHAIN_RULES trigger (e.g. building_era < 1970 AND flooring/windows_doors
    in scope) but ``chain_ids_applied`` is empty, the model refuses to build
    instead of silently returning a 0 cost.
    """

    chain_ids_applied: list[str] = Field(default_factory=list)
    chain_cost_huf: int = Field(default=0)
    # Input context the guard matches triggers against
    building_era: str | None = None
    scope: dict[str, bool] = Field(default_factory=dict)
    floor_number: int | None = None
    gas_heating: bool | None = None
    building_type: str | None = None

    @model_validator(mode="after")
    def _require_triggered_chains_applied(self) -> "CostBreakdownOutput":
        """Raise a clear ValidationError if a matching chain rule is missing."""
        for rule in CHAIN_RULES:
            if rule["trigger"](
                self.scope, self.building_era, self.floor_number,
                self.gas_heating, self.building_type,
            ):
                if rule["id"] not in self.chain_ids_applied:
                    raise ValueError(
                        f"Chain '{rule['id']}' must be applied for the given scope: "
                        f"building_era={self.building_era!r}, scope={self.scope}, "
                        f"floor_number={self.floor_number}, gas_heating={self.gas_heating}, "
                        f"building_type={self.building_type!r}. "
                        "chain_ids_applied is empty/missing it, so the estimate would "
                        "silently under-report structural cost. "
                        f"Applicable chain steps: {' → '.join(rule['steps'])}"
                    )
        return self


def build_chain_breakdown(
    scope: Dict[str, bool],
    building_era: Optional[str],
    floor_number: Optional[int] = None,
    gas_heating: Optional[bool] = None,
    building_type: Optional[str] = None,
    area_sqm: float = 30.0,
    target_date: Optional[date] = None,
    price_index: Any = None,
) -> CostBreakdownOutput:
    """Detect active chains, compute their cost, and return a validated output.

    Uses the exact same trigger evaluation as detect_active_chains(); the
    Pydantic validator re-checks the result so an omission fails loudly.
    """
    active_chains = detect_active_chains(
        scope, building_era, floor_number, gas_heating, building_type,
    )
    costs = chain_total_cost(
        active_chains, area_sqm=area_sqm,
        target_date=target_date, price_index=price_index,
    )
    return CostBreakdownOutput(
        chain_ids_applied=[c["id"] for c in active_chains],
        chain_cost_huf=costs["point"],
        building_era=building_era,
        scope={k: bool(v) for k, v in scope.items()},
        floor_number=floor_number,
        gas_heating=bool(gas_heating) if gas_heating is not None else None,
        building_type=building_type,
    )


# ---------------------------------------------------------------------------
# 3. Infrastructure minimums
# ---------------------------------------------------------------------------
# Source: professional renovation engineer (Hungarian), 2025
# These are MINIMUMS — if the corpus-based estimate comes in below, override.

ELECTRICAL_STANDARDIZATION_MINIMUM = 300_000
GAS_HEATING_INFRA_MINIMUM = 800_000

def apply_infrastructure_minimums(
    base_estimate_low: int,
    base_estimate_mid: int,
    base_estimate_high: int,
    is_full_renovation: bool,
    has_gas_heating: Optional[bool],
    target_date: Optional[date] = None,
    price_index: Any = None,
) -> Tuple[int, int, int, List[str]]:
    """Apply minimum overrides. Returns (adjusted_low, adjusted_mid, adjusted_high, log_entries).

    The minimum constants are dated to the 2025 expert reference; when
    target_date and price_index are supplied they are inflated 2025 ->
    target_date at the point of consumption. With either omitted the raw
    (undated) constants are used.
    """
    logs: List[str] = []
    low, mid, high = base_estimate_low, base_estimate_mid, base_estimate_high

    if target_date is not None and price_index is not None:
        elec_min = _inflate_structural_value(ELECTRICAL_STANDARDIZATION_MINIMUM, target_date, price_index)
        gas_min = _inflate_structural_value(GAS_HEATING_INFRA_MINIMUM, target_date, price_index)
    else:
        elec_min = ELECTRICAL_STANDARDIZATION_MINIMUM
        gas_min = GAS_HEATING_INFRA_MINIMUM

    if not is_full_renovation:
        return (low, mid, high, logs)

    # Electrical standardization minimum
    electrical_share = int(mid * 0.12)
    if electrical_share < elec_min:
        delta = elec_min - electrical_share
        low += delta
        mid += delta
        high += delta
        logs.append(f"Electrical min override: {electrical_share:,} → {elec_min:,} Ft")

    # Gas/heating infrastructure minimum — only if gas heating is present
    if has_gas_heating:
        heating_share = int(mid * 0.20)
        if heating_share < gas_min:
            delta = gas_min - heating_share
            low += delta
            mid += delta
            high += delta
            logs.append(f"Gas/heating min override: {heating_share:,} → {gas_min:,} Ft")

    return (low, mid, high, logs)


# ---------------------------------------------------------------------------
# 4. Logistics surcharge
# ---------------------------------------------------------------------------
LOGISTICS_SURCHARGE_PCT = 0.15  # 15% of total labor

def compute_logistics_surcharge(total_labor_huf: float) -> float:
    return total_labor_huf * LOGISTICS_SURCHARGE_PCT


# ---------------------------------------------------------------------------
# 5. Elevator-based logistics adjustment (placeholder config constant)
# ---------------------------------------------------------------------------
# Source: professional renovation engineer identifies this as a cost driver,
# but no exact figure was given. This is a placeholder config constant so it
# can be tuned without touching logic.
ELEVATOR_SURCHARGE_PER_FLOOR = 50_000  # placeholder — needs expert confirmation

def elevator_surcharge(
    elevator_type: Optional[str],
    floor_number: Optional[int],
) -> float:
    if elevator_type == "none":
        floors = max(0, (floor_number or 1) - 1)
        return floors * ELEVATOR_SURCHARGE_PER_FLOOR
    if elevator_type == "small":
        return 0  # small elevator still helps, no surcharge
    if elevator_type == "large":
        return 0  # freight-capable elevator, no surcharge
    return 0  # unknown defaults to no surcharge


# ---------------------------------------------------------------------------
# 6. Chimney technician — conditional inclusion
# ---------------------------------------------------------------------------
# Source: professional renovation engineer (Hungarian), 2025
# Required when individual gas heating is present. Cost scales with floor count.

CHIMNEY_TECHNICIAN_BASE = 80_000  # base cost
CHIMNEY_TECHNICIAN_PER_FLOOR = 20_000  # per floor above ground

def chimney_technician_cost(
    floor_number: Optional[int],
    target_date: Optional[date] = None,
    price_index: Any = None,
) -> int:
    floors = max(1, floor_number or 1)
    base = CHIMNEY_TECHNICIAN_BASE
    per_floor = CHIMNEY_TECHNICIAN_PER_FLOOR
    if target_date is not None and price_index is not None:
        base = _inflate_structural_value(base, target_date, price_index)
        per_floor = _inflate_structural_value(per_floor, target_date, price_index)
    return base + (floors - 1) * per_floor
