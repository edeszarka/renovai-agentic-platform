import logging
from typing import Dict, List, Tuple, Optional, Any

logger = logging.getLogger(__name__)

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

CHAIN_RULES: List[ChainEntry] = [
    {
        "id": "ajto_padlo_lanc",
        "trigger": lambda scope, era, floor, gas: (
            _era_pre_1970(era)
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
        # Calibrated so that at 30 m² the values match the original fixed costs.
        "base_cost_low": 1_210_000,
        "per_sqm_cost_low": 43_000,
        "base_cost_high": 1_770_000,
        "per_sqm_cost_high": 41_000,
        "base_cost_point": 1_490_000,
        "per_sqm_cost_point": 42_000,
        "description": (
            "Ajtó/Padló-lánc: régi épületben az ajtócsere vagy parketta felbontása "
            "során előkerülő kohósalak miatt szükséges teljes salakmentesítés, "
            "szigetelés és új aljzat kialakítása."
        ),
        "note": "Expert-sourced figure (2.5-3.0M HUF at 30 m²). Scales with area for EPS, concrete, flooring.",
    },
]

def detect_active_chains(
    scope: Dict[str, bool],
    building_era: Optional[str],
    floor_number: Optional[int] = None,
    gas_heating: Optional[bool] = None,
) -> List[ChainEntry]:
    """Return all chain rules whose trigger condition is met."""
    active = []
    for rule in CHAIN_RULES:
        if rule["trigger"](scope, building_era, floor_number, gas_heating):
            active.append(rule)
    return active

def chain_total_cost(active_chains: List[ChainEntry], area_sqm: float = 30.0) -> Dict[str, int]:
    """Compute total chain cost, area-scaled.

    Each chain's cost = base_cost + per_sqm_cost * area_sqm.
    At the reference area (30 m²) the result matches the original fixed figures.
    """
    low = sum(c["base_cost_low"] + c["per_sqm_cost_low"] * area_sqm for c in active_chains)
    high = sum(c["base_cost_high"] + c["per_sqm_cost_high"] * area_sqm for c in active_chains)
    point = sum(c["base_cost_point"] + c["per_sqm_cost_point"] * area_sqm for c in active_chains)
    return {"low": int(low), "high": int(high), "point": int(point)}


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
) -> Tuple[int, int, int, List[str]]:
    """Apply minimum overrides. Returns (adjusted_low, adjusted_mid, adjusted_high, log_entries)."""
    logs: List[str] = []
    low, mid, high = base_estimate_low, base_estimate_mid, base_estimate_high

    if not is_full_renovation:
        return (low, mid, high, logs)

    # Electrical standardization minimum
    electrical_share = int(mid * 0.12)
    if electrical_share < ELECTRICAL_STANDARDIZATION_MINIMUM:
        delta = ELECTRICAL_STANDARDIZATION_MINIMUM - electrical_share
        low += delta
        mid += delta
        high += delta
        logs.append(f"Electrical min override: {electrical_share:,} → {ELECTRICAL_STANDARDIZATION_MINIMUM:,} Ft")

    # Gas/heating infrastructure minimum — only if gas heating is present
    if has_gas_heating:
        heating_share = int(mid * 0.20)
        if heating_share < GAS_HEATING_INFRA_MINIMUM:
            delta = GAS_HEATING_INFRA_MINIMUM - heating_share
            low += delta
            mid += delta
            high += delta
            logs.append(f"Gas/heating min override: {heating_share:,} → {GAS_HEATING_INFRA_MINIMUM:,} Ft")

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

def chimney_technician_cost(floor_number: Optional[int]) -> int:
    floors = max(1, floor_number or 1)
    return CHIMNEY_TECHNICIAN_BASE + (floors - 1) * CHIMNEY_TECHNICIAN_PER_FLOOR
