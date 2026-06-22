import re
from typing import Optional

ADDRESS_REGEX = re.compile(
    r"(\d{4})[_\s]+"        # postal code: 4 digits
    r"(.+?)"                 # street name
    r"[_\s]+(\d+[a-zA-Z]?)" # house number
    r"(?:[_\s]+(\d+)[_\s]+emelet)?"  # optional floor
    r"(?:[_\s]+(\d+)[_\s]+em(?:elet)?)?",  # alternative floor format
    re.IGNORECASE
)

def postal_to_district(postal: int) -> Optional[int]:
    """Budapest districts: postal code = 1XYZ where X is the district."""
    s = str(postal)
    if len(s) == 4 and s.startswith("1"):
        try:
            return int(s[1:3])
        except (ValueError, IndexError):
            return None
    return None

def extract_address(filename: str) -> dict:
    """Input: raw filename without path, without extension."""
    match = ADDRESS_REGEX.search(filename)
    if not match:
        return {
            "address_raw": None,
            "district": None,
            "postal_code": None,
            "street": None,
            "house_number": None,
            "floor": None
        }

    postal_code = int(match.group(1))
    street = match.group(2).replace("_", " ").strip()
    house_number = match.group(3)
    floor = match.group(4) or match.group(5)
    
    address_parts = [str(postal_code), street, house_number]
    if floor:
        address_raw = f"{postal_code} {street} {house_number}, {floor}. emelet"
    else:
        address_raw = f"{postal_code} {street} {house_number}"

    return {
        "address_raw": address_raw,
        "district": postal_to_district(postal_code),
        "postal_code": postal_code,
        "street": street,
        "house_number": house_number,
        "floor": f"{floor}. emelet" if floor else None
    }
