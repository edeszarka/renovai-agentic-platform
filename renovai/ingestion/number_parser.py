import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

TOTAL_SUMMARY_PATTERNS = {
    "material": r"anyag\s*:\s*([\d\s,\.]+)\s*ft",
    "labor": r"munkadíj\s*:.*?([\d\s,\.]+)\s*ft",
    "total": r"összesen\s*:\s*([\d\s,\.]+)\s*ft",
}


def parse_huf(value) -> Optional[int]:
    """Handles all formats found in production files."""
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)):
        return int(value)

    s = str(value).strip().lower()
    if not s:
        return None

    # Handle formats like "1,690,000", "963118 Ft", "nettó 2835000 Ft", "3798118 Ft."
    # Remove common non-numeric parts
    s = (
        s.replace("ft", "")
        .replace(".", "")
        .replace(",", "")
        .replace("\xa0", "")
        .strip()
    )

    # Use regex to extract the first sequence of digits if it's still messy
    match = re.search(r"(\d+)", s)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass

    logger.warning(f"Could not parse HUF value: {value}")
    return None


def parse_text_summary_line(text: str) -> dict:
    """For Style A total rows like 'Anyag: 963118 Ft'."""
    text_lower = text.lower()
    for component, pattern in TOTAL_SUMMARY_PATTERNS.items():
        match = re.search(pattern, text_lower)
        if match:
            val_str = match.group(1)
            val = parse_huf(val_str)
            if val is not None:
                return {"component": component, "value": val}
    return {}
