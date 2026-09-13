import re
from typing import List

TIMELINE_PATTERNS = [
    r"(\d+[\.,]?\d*)[–\-](\d+[\.,]?\d*)\s*hét",  # "3.5-4.5 hét"
    r"(\d+[\.,]?\d*)\s*hét",  # "4 hét"
    r"(\d+[\.,]?\d*)[–\-](\d+[\.,]?\d*)\s*hónap",  # "3-4 hónap"
    r"kb\.\s*(\d+[\.,]?\d*)[–\-]?(\d+[\.,]?\d*)?\s*hónap",
]

PAYMENT_PATTERN = re.compile(
    r"(\d+)%.*?megkezdésekor.*?(\d+)%.*?(\d+)\s*hete", re.DOTALL | re.IGNORECASE
)

EUR_RATE_PATTERN = re.compile(r"(\d{3,4})\s*ft[–\-]os\s*euro", re.IGNORECASE)
FUEL_PRICE_PATTERN = re.compile(r"(\d{3})\s*ft[–\-]os.*?benzin", re.IGNORECASE)


def extract_metadata_from_notes(
    note_rows: List[str], grand_total_huf: int, full_text: str = ""
) -> dict:
    """Scans all general_notes and full text for structured metadata."""
    metadata = {
        "timeline_weeks_min": None,
        "timeline_weeks_max": None,
        "start_date_approx": None,
        "payment_schedule": None,
        "valid_eur_rate": None,
        "valid_fuel_price": None,
        "has_slag_complication": False,
        "labor_vat_included": True,
        "materials_brands": [],
    }

    combined_notes = "\n".join(note_rows)
    search_text = combined_notes + "\n" + full_text

    # Has slag?
    if "kohósalak" in search_text.lower():
        metadata["has_slag_complication"] = True

    # Labor VAT included? (if "nettó" is present near prices)
    if "nettó" in search_text.lower():
        metadata["labor_vat_included"] = False

    # Timeline extraction
    for pattern in TIMELINE_PATTERNS:
        match = re.search(pattern, combined_notes, re.IGNORECASE)
        if match:
            groups = match.groups()
            try:
                v1 = float(groups[0].replace(",", "."))
                v2 = (
                    float(groups[1].replace(",", "."))
                    if len(groups) > 1 and groups[1]
                    else v1
                )

                if "hónap" in match.group(0).lower():
                    v1 *= 4.33
                    v2 *= 4.33

                metadata["timeline_weeks_min"] = round(v1, 1)
                metadata["timeline_weeks_max"] = round(v2, 1)
                break
            except (ValueError, TypeError):
                continue

    # Payment schedule
    if "fizetési" in combined_notes.lower() or "%" in combined_notes:
        # Just grab the first few lines that look like a schedule
        lines = combined_notes.split("\n")
        schedule_lines = [
            l.strip() for l in lines if "%" in l or "fizetés" in l.lower()
        ]
        if schedule_lines:
            metadata["payment_schedule"] = " | ".join(schedule_lines[:3])

    # EUR Rate
    eur_match = EUR_RATE_PATTERN.search(combined_notes)
    if eur_match:
        metadata["valid_eur_rate"] = int(eur_match.group(1))

    # Fuel Price
    fuel_match = FUEL_PRICE_PATTERN.search(combined_notes)
    if fuel_match:
        metadata["valid_fuel_price"] = int(fuel_match.group(1))

    # Material brands
    brand_marker = "felhasznált anyagok márkái:"
    if brand_marker in combined_notes.lower():
        parts = combined_notes.lower().split(brand_marker)
        if len(parts) > 1:
            brand_text = parts[1].split("\n")[0]
            brands = [b.strip() for b in brand_text.split(",") if b.strip()]
            metadata["materials_brands"] = brands

    return metadata
