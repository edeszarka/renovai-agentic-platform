"""Owner-purchased product pricing catalog (doc 04).

Structured lookup table for items the *tulajdonos* (owner) buys himself rather
than the contractor pricing into the labor quote. This is deliberately
separate from contractor labor-category pricing.

Source: ``04_tulajdonosi_beszerzesu_termekek_arkategoriak.md`` (doc 04). Each
product category is priced by a six-quality-tier ladder:

    alsó / alsó kozep / kozep kozep / felso kozep / premium / luxus

Convention: every price is a (low, high) HUF range for the category's
reference unit, matching how the rest of the codebase stores cost ranges
(min/max pairs). ``None`` values mean the tier does not exist for that item
(e.g. a luxury tile size with no lower-tier entry).

Per the pending doc 03 #6 vs doc 04 §8 appliance conflict
(docs/development-log/FAZIS_A_FINDINGS.md §3), doc 04's tiered figures are treated as canonical
for this catalog; a reconciliation note is carried on the appliance section
rather than silently picking a number.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

PriceRange = Tuple[Optional[int], Optional[int]]

# Canonical tier ladder (stable order, Hungarian labels used across the corpus).
TIERS: List[str] = [
    "also",
    "also_kozep",
    "kozep_kozep",
    "felso_kozep",
    "premium",
    "luxus",
]

TIER_LABELS_HU: Dict[str, str] = {
    "also": "Alsó",
    "also_kozep": "Alsó közép",
    "kozep_kozep": "Közép közép",
    "felso_kozep": "Felső közép",
    "premium": "Prémium",
    "luxus": "Luxus",
}

# ---------------------------------------------------------------------------
# Tile (csempe / járólap) — doc 04 §1.
# Reference: ~45 nm tiled area (4 nm bathroom + 1 nm wc + 7 nm kitchen + pult).
# Rows: quality tier. Columns: tile size in cm. Unit: Ft/nm (100x280 is Ft/lap).
# ---------------------------------------------------------------------------
TILE_SIZES = ["60x30", "60x60", "60x120", "80x80", "90x90", "120x120", "100x280"]

TILE_PRICES: Dict[str, Dict[str, PriceRange]] = {
    "60x30": {
        "also": (4_000, 6_000),
        "also_kozep": (5_000, 7_000),
        "kozep_kozep": (7_000, 9_000),
        "felso_kozep": (10_000, 12_000),
        "premium": (12_000, 13_000),
        "luxus": (None, None),
    },
    "60x60": {
        "also": (5_000, 7_000),
        "also_kozep": (5_000, 7_000),
        "kozep_kozep": (7_000, 9_000),
        "felso_kozep": (10_000, 13_000),
        "premium": (11_000, 15_000),
        "luxus": (18_000, None),
    },
    "60x120": {
        "also": (7_000, 8_000),
        "also_kozep": (7_000, 8_000),
        "kozep_kozep": (10_000, 11_000),
        "felso_kozep": (12_000, 14_000),
        "premium": (15_000, 20_000),
        "luxus": (20_000, 25_000),
    },
    "80x80": {
        "also": (None, None),
        "also_kozep": (None, None),
        "kozep_kozep": (None, None),
        "felso_kozep": (None, None),
        "premium": (15_000, 18_000),
        "luxus": (20_000, None),
    },
    "90x90": {
        "also": (None, None),
        "also_kozep": (None, None),
        "kozep_kozep": (None, None),
        "felso_kozep": (None, None),
        "premium": (15_000, 20_000),
        "luxus": (22_000, 25_000),
    },
    "120x120": {
        "also": (None, None),
        "also_kozep": (None, None),
        "kozep_kozep": (None, None),
        "felso_kozep": (None, None),
        "premium": (20_000, 35_000),
        "luxus": (30_000, 35_000),
    },
    "100x280": {
        "also": (None, None),
        "also_kozep": (None, None),
        "kozep_kozep": (None, None),
        "felso_kozep": (None, None),
        "premium": (120_000, 180_000),
        "luxus": (180_000, 200_000),
    },
}

# ---------------------------------------------------------------------------
# Laminate floor (laminált padló) + XPS underlay — doc 04 §2.
# Unit: Ft/nm by plank thickness. XPS underlay listed separately.
# ---------------------------------------------------------------------------
LAMINATE_PRICES: Dict[str, PriceRange] = {
    "7mm": (4_000, 5_000),
    "8mm": (4_500, 5_500),
    "10mm": (6_000, 7_500),
    "12mm": (6_500, 9_500),
}

XPS_UNDERLAY_PRICES: Dict[str, PriceRange] = {
    "3mm": (550, 700),
    "5mm": (750, 1_000),
}

# ---------------------------------------------------------------------------
# Sanitary ware (szaniterek) — doc 04 §3. Reference: chrome fixtures package.
# Unit: total package HUF by tier. Non-chrome fixtures = +20-60%.
# ---------------------------------------------------------------------------
SANITARY_PRICES: Dict[str, PriceRange] = {
    "also": (350_000, 450_000),  # rozsdamentes acél mosogató
    "also_kozep": (400_000, 500_000),  # gránit mosogató
    "kozep_kozep": (450_000, 550_000),
    "felso_kozep": (550_000, 700_000),
    "premium": (700_000, 1_000_000),
    "luxus": (1_000_000, None),  # open-ended
}

# Non-chrome fixture surcharge (+20-60%).
SANITARY_NON_CHROME_SURCHARGE_PCT = (0.20, 0.60)

# ---------------------------------------------------------------------------
# Interior doors (beltéri ajtók) — doc 04 §4. Unit: Ft/door (+ install).
# Keyed by (hinge_type, opening_m, has_glass). Price per door; installation
# is listed separately because doors can be owner-purchased while the fitter's
# labor is still contractor work.
# ---------------------------------------------------------------------------
DOOR_TYPES: Dict[str, Dict[str, dict]] = {
    "kulso_zsaneros_1m": {
        "label": "Külső zsanéros, 1 m nyílásig",
        "without_glass": {"door": (90_000, 130_000), "install": (23_000, 27_000)},
        "with_glass": {"door": (130_000, 180_000), "install": (25_000, 30_000)},
    },
    "belso_zsaneros_2m": {
        "label": "Belső zsanéros, 2 m nyílásig",
        "without_glass": {"door": (140_000, 180_000), "install": (30_000, 35_000)},
        "with_glass": {"door": (170_000, 230_000), "install": (32_000, 37_000)},
    },
    "egyszarnyu_toloajto_1m": {
        "label": "Egyszárnyú tolóajtó, 1 m nyílásig",
        "without_glass": {"door": (120_000, 150_000), "install": (25_000, 28_000)},
        "with_glass": {"door": (140_000, 180_000), "install": (25_000, 30_000)},
    },
    "ketszarnyu_toloajto_2m": {
        "label": "Kétszárnyú tolóajtó, 2 m nyílásig",
        "without_glass": {"door": (180_000, 240_000), "install": (35_000, 45_000)},
        "with_glass": {"door": (200_000, 280_000), "install": (34_000, 45_000)},
    },
}

# ---------------------------------------------------------------------------
# Interior paint (beltéri festékek), mixed dispersion water-based — doc 04 §5.
# Unit: Ft per 15 l bucket.
# ---------------------------------------------------------------------------
PAINT_PRICES: Dict[str, PriceRange] = {
    "non_washable_premium_color": (18_000, 25_000),
    "premium_washable_color": (38_000, 50_000),
    "white_interior": (11_000, 17_000),
}

# ---------------------------------------------------------------------------
# Lamps (lámpák) — doc 04 §6. Reference: 8-9 lights. Unit: total package HUF.
# ---------------------------------------------------------------------------
LAMP_PRICES: Dict[str, PriceRange] = {
    "also": (100_000, 120_000),
    "also_kozep": (120_000, 150_000),
    "kozep_kozep": (140_000, 180_000),
    "felso_kozep": (180_000, 250_000),
    "premium": (250_000, 500_000),
    "luxus": (500_000, None),
}

# ---------------------------------------------------------------------------
# Kitchen cabinet (konyhabútor) — doc 04 §7. Reference: 3x2 m, carpenter-made,
# built-in appliances, no fridge. Unit: total package HUF by tier + features.
# ---------------------------------------------------------------------------
KITCHEN_FEATURES: Dict[str, str] = {
    "also": "Nem finoman záródó, 3-6 fiók, oldalra nyíló felső szekrény, MDF fiókok, matt lap 1mm élzárás",
    "also_kozep": "Finoman záródó, egyéb mint alsó, esetleg olcsóbb Egger",
    "kozep_kozep": "Finoman záródó, fiókon belüli fiók, pultvilágítás, Egger 2mm élzárás",
    "felso_kozep": "Fiókon belüli fiókok, hangulat- és pultvilágítás, push-to-open, fém fiókok",
    "premium": "Mint felső közép + fújt frontlapok, technikai kő pult, integrált mosogatómedence, üveges vitrin",
    "luxus": "Mint prémium + valódi kő pult, elektromos nyíló ajtók",
}

KITCHEN_PRICES: Dict[str, PriceRange] = {
    "also": (500_000, 700_000),
    "also_kozep": (600_000, 900_000),
    "kozep_kozep": (1_000_000, 1_500_000),
    "felso_kozep": (1_500_000, 2_500_000),
    "premium": (3_000_000, 4_500_000),
    "luxus": (5_500_000, None),
}

# ---------------------------------------------------------------------------
# Household appliances (háztartási gépek) — doc 04 §8.
# Unit: Ft per appliance by tier. Six tiers, seven appliances.
# NOTE (pending reconciliation): doc 03 #6 gives flat single ranges that do not
# map cleanly onto these tiers (see docs/development-log/FAZIS_A_FINDINGS.md §3). Doc 04's tiered
# figures are used as canonical here; no number is silently "chosen" to merge
# with doc 03. Update once the conflict is resolved by the user.
# ---------------------------------------------------------------------------
APPLIANCE_NAMES = [
    "hob_fozolap",
    "oven_suto",
    "dishwasher_mosogatogep",
    "range_hood_szagelszivo",
    "refrigerator_huto",
    "washing_machine_mosogep",
    "microwave_mikro",
]

APPLIANCE_PRICES: Dict[str, Dict[str, PriceRange]] = {
    "hob_fozolap": {
        "also": (45_000, 55_000),
        "also_kozep": (55_000, 65_000),
        "kozep_kozep": (65_000, 85_000),
        "felso_kozep": (90_000, 120_000),
        "premium": (120_000, 180_000),
        "luxus": (250_000, None),
    },
    "oven_suto": {
        "also": (60_000, 90_000),
        "also_kozep": (90_000, 110_000),
        "kozep_kozep": (110_000, 130_000),
        "felso_kozep": (130_000, 170_000),
        "premium": (180_000, 220_000),
        "luxus": (250_000, None),
    },
    "dishwasher_mosogatogep": {
        "also": (100_000, 120_000),
        "also_kozep": (100_000, 120_000),
        "kozep_kozep": (120_000, 140_000),
        "felso_kozep": (140_000, 180_000),
        "premium": (180_000, 250_000),
        "luxus": (250_000, None),
    },
    "range_hood_szagelszivo": {
        "also": (25_000, 35_000),
        "also_kozep": (30_000, 40_000),
        "kozep_kozep": (35_000, 45_000),
        "felso_kozep": (40_000, 55_000),
        "premium": (80_000, 150_000),
        "luxus": (200_000, None),
    },
    "refrigerator_huto": {
        "also": (120_000, 150_000),
        "also_kozep": (120_000, 150_000),
        "kozep_kozep": (140_000, 180_000),
        "felso_kozep": (160_000, 220_000),
        "premium": (200_000, 260_000),
        "luxus": (250_000, None),
    },
    "washing_machine_mosogep": {
        "also": (110_000, 130_000),
        "also_kozep": (120_000, 140_000),
        "kozep_kozep": (130_000, 160_000),
        "felso_kozep": (150_000, 190_000),
        "premium": (200_000, 300_000),
        "luxus": (250_000, None),
    },
    "microwave_mikro": {
        "also": (18_000, 25_000),
        "also_kozep": (25_000, 30_000),
        "kozep_kozep": (30_000, 40_000),
        "felso_kozep": (70_000, 100_000),
        "premium": (80_000, 120_000),
        "luxus": (150_000, None),
    },
}

# ---------------------------------------------------------------------------
# Lookup API
# ---------------------------------------------------------------------------

_TIER_ALIASES: Dict[str, str] = {
    # lowercase, space-normalized Hungarian labels (with/without accents)
    "alsó": "also",
    "also": "also",
    "alsó közép": "also_kozep",
    "alsó közepe": "also_kozep",
    "also kozep": "also_kozep",
    "also_kozep": "also_kozep",
    "közép közép": "kozep_kozep",
    "kozep kozep": "kozep_kozep",
    "kozep_kozep": "kozep_kozep",
    "közép": "kozep_kozep",
    "felső közép": "felso_kozep",
    "felso kozep": "felso_kozep",
    "felso_kozep": "felso_kozep",
    "felső": "felso_kozep",
    "prémium": "premium",
    "premium": "premium",
    "luxus": "luxus",
}


def normalize_tier(tier: str) -> str:
    """Coerce a user-supplied tier label to a canonical ladder key.

    Accepts Hungarian labels with/without accents, underscores, and casing
    (e.g. ``"Alsó közép"`` -> ``"also_kozep"``). Raises KeyError on unknown
    values rather than silently defaulting.
    """
    key = " ".join(tier.strip().lower().replace("_", " ").split())
    normalized = _TIER_ALIASES.get(key)
    if normalized is None:
        raise KeyError(f"Unrecognized quality tier: {tier!r}. Valid: {TIERS}")
    return normalized


def lookup(category: str, tier: str, **kwargs: Optional[str]) -> PriceRange:
    """Return the (low, high) HUF price range for a category at a quality tier.

    Categories: ``tile``, ``laminate``, ``sanitary``, ``door``, ``lamp``,
    ``kitchen``, ``appliance``.

    - ``tile``: requires ``size`` (one of TILE_SIZES).
    - ``laminate``: requires ``thickness`` (7mm/8mm/10mm/12mm).
    - ``door``: requires ``door_type`` (DOOR_TYPES key) and ``glass``
      (bool / "without_glass" / "with_glass").
    - ``appliance``: requires ``appliance`` (APPLIANCE_NAMES key).
    - ``sanitary`` / ``lamp`` / ``kitchen``: tier only.

    Returns ``(low, high)``; ``None`` marks an unbounded or absent value.
    """
    cat = category.strip().lower()
    tier = normalize_tier(tier)

    if cat == "tile":
        size = kwargs.get("size", "60x60")
        row = TILE_PRICES.get(size)
        if row is None:
            raise KeyError(f"Unknown tile size: {size!r}")
        return row[tier]

    if cat == "laminate":
        thickness = kwargs.get("thickness", "8mm")
        price = LAMINATE_PRICES.get(thickness)
        if price is None:
            raise KeyError(f"Unknown laminate thickness: {thickness!r}")
        return price

    if cat == "sanitary":
        return SANITARY_PRICES[tier]

    if cat == "door":
        door_type = kwargs.get("door_type")
        glass = kwargs.get("glass", "without_glass")
        if door_type not in DOOR_TYPES:
            raise KeyError(
                f"Unknown door type: {door_type!r}. Valid: {list(DOOR_TYPES)}"
            )
        variant = (
            "with_glass"
            if glass in ("with_glass", True, "true", "with")
            else "without_glass"
        )
        return DOOR_TYPES[door_type][variant]["door"]

    if cat == "lamp":
        return LAMP_PRICES[tier]

    if cat == "kitchen":
        return KITCHEN_PRICES[tier]

    if cat == "appliance":
        appliance = kwargs.get("appliance")
        if appliance not in APPLIANCE_PRICES:
            raise KeyError(
                f"Unknown appliance: {appliance!r}. Valid: {APPLIANCE_NAMES}"
            )
        return APPLIANCE_PRICES[appliance][tier]

    raise KeyError(
        f"Unknown product category: {category!r}. Valid: tile, laminate, sanitary, door, lamp, kitchen, appliance"
    )


def xps_underlay(thickness: str = "3mm") -> PriceRange:
    """XPS padló alátét ár (Ft/nm)."""
    price = XPS_UNDERLAY_PRICES.get(thickness)
    if price is None:
        raise KeyError(f"Unknown XPS thickness: {thickness!r}")
    return price


def door_install(door_type: str, glass: str = "without_glass") -> PriceRange:
    """Beltéri ajtó beszerelés munkadíja (Ft/db), a választott típushoz.

    Used when the door itself is owner-purchased but the fitting labor is a
    contractor item. ``glass`` may be ``"with_glass"`` / ``"without_glass"``.
    """
    if door_type not in DOOR_TYPES:
        raise KeyError(f"Unknown door type: {door_type!r}. Valid: {list(DOOR_TYPES)}")
    variant = (
        "with_glass"
        if glass in ("with_glass", True, "true", "with")
        else "without_glass"
    )
    return DOOR_TYPES[door_type][variant]["install"]
