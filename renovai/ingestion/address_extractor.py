import re
from typing import Optional

ADDRESS_REGEX = re.compile(
    r"(\d{4})[_\s]+"        # postal code: 4 digits
    r"(.+?)"                 # street name
    r"[_\s]+(\d+[a-zA-Z]?)" # house number
    # Optional floor: tolerate any separator run (period, comma, whitespace,
    # underscore) both before and between digit and "emelet". Real filenames
    # use "2 emelet", "2. emelet" and ", 3. emelet" (digit-period-space).
    r"(?:[_\s,.]*(\d+)[_\s.]*emelet)?",
    re.IGNORECASE
)

def postal_to_district(postal: int) -> Optional[int]:
    """Budapest districts: postal code = 1XY Z where XY is the district (1-23)."""
    s = str(postal)
    if len(s) == 4 and s.startswith("1"):
        try:
            d = int(s[1:3])
            if 1 <= d <= 23:
                return d
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
    floor = match.group(4)
    
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
