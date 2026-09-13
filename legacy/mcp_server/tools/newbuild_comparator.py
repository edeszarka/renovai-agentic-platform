"""
New-build apartment comparator for Budapest.

Provides median new-build price per m² by district (KSH 2025 Q4 data)
and a buy-vs-new comparison function.
"""

import logging

logger = logging.getLogger(__name__)

# KSH lakásár-statisztika 2025 Q4 — új építésű lakások medián négyzetméterára
# Forrás: KSH táblázatok, manuálisan összeállítva.
# Districts not listed return None (insufficient data).
NEWBUILD_PRICE_TABLE: dict[int, int] = {
    1: 2_200_000,
    2: 1_900_000,
    3: 1_600_000,
    4: 1_200_000,
    5: 2_000_000,
    6: 1_800_000,
    7: 1_500_000,
    8: 1_400_000,
    9: 1_500_000,
    10: 1_100_000,
    11: 1_450_000,
    12: 1_300_000,
    13: 1_750_000,
    14: 1_350_000,
    15: 950_000,
    16: 900_000,
    17: 850_000,
    18: 800_000,
    19: 750_000,
    20: 700_000,
    21: 650_000,
    22: 600_000,
    23: 550_000,
}


def get_newbuild_median_price(
    district: int,
    area_sqm: float,
    num_rooms: int,
) -> dict:
    """Returns the median new-build price for a given district and size.

    Args:
        district: Budapest district number (1-23).
        area_sqm: Floor area in square metres.
        num_rooms: Number of rooms (informational only, not used in pricing).

    Returns:
        dict with keys:
          - price_per_sqm_huf: median Ft/m² for the district (or None)
          - median_newbuild_total_huf: price_per_sqm * area_sqm (or None)
          - district: the requested district
          - area_sqm: the requested area
          - num_rooms: the requested room count
          - source: data source description
          - error: None if data exists, else message string
    """
    price_per_sqm = NEWBUILD_PRICE_TABLE.get(district)
    if price_per_sqm is None:
        return {
            "price_per_sqm_huf": None,
            "median_newbuild_total_huf": None,
            "district": district,
            "area_sqm": area_sqm,
            "num_rooms": num_rooms,
            "source": "KSH lakásár-statisztika 2025 Q4",
            "error": f"Nincs adat a {district}. kerületre az adatbázisban.",
        }

    return {
        "price_per_sqm_huf": price_per_sqm,
        "median_newbuild_total_huf": int(price_per_sqm * area_sqm),
        "district": district,
        "area_sqm": area_sqm,
        "num_rooms": num_rooms,
        "source": "KSH lakásár-statisztika 2025 Q4",
        "error": None,
    }


def calculate_buy_vs_new(
    asking_price_huf: int,
    renovation_estimate_huf: int,
    newbuild_median_huf: int,
    area_sqm: float,
) -> dict:
    """Compares total cost of buying+renovating vs buying new-build.

    Args:
        asking_price_huf: The asking price of the existing apartment in HUF.
        renovation_estimate_huf: The estimated renovation cost (mid estimate) in HUF.
        newbuild_median_huf: Median price of a comparable new-build in HUF.
        area_sqm: Floor area in square metres.

    Returns:
        dict with keys:
          - total_cost_huf: asking_price_huf + renovation_estimate_huf
          - newbuild_total_huf: newbuild_median_huf
          - difference_huf: total_cost_huf - newbuild_total_huf
          - difference_pct: percentage difference
          - cost_per_sqm_existing: (asking_price_huf + renovation_estimate_huf) / area_sqm
          - cost_per_sqm_newbuild: newbuild_median_huf / area_sqm
          - verdict: short Hungarian verdict string
    """
    total_cost = asking_price_huf + renovation_estimate_huf
    diff_huf = total_cost - newbuild_median_huf
    diff_pct = (
        round((diff_huf / newbuild_median_huf) * 100, 1)
        if newbuild_median_huf > 0
        else 0
    )

    if diff_pct < -15:
        verdict = "Olcsóbb, mint egy új lakás!"
    elif diff_pct < 5:
        verdict = "Versenyképes az új lakások árával."
    elif diff_pct < 20:
        verdict = "Drágább, mint egy új lakás — érdemes alkudni."
    else:
        verdict = "Sokkal drágább, mint egy új lakás — alaposan gondold át!"

    return {
        "total_cost_huf": total_cost,
        "newbuild_total_huf": newbuild_median_huf,
        "difference_huf": diff_huf,
        "difference_pct": diff_pct,
        "cost_per_sqm_existing": int(total_cost / area_sqm) if area_sqm > 0 else 0,
        "cost_per_sqm_newbuild": int(newbuild_median_huf / area_sqm)
        if area_sqm > 0
        else 0,
        "verdict": verdict,
    }
