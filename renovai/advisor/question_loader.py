import logging
import re
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


def load_questions_text(questions_dir: Path) -> str:
    path = questions_dir / "Kerdoiv_kerdesek.md"
    if not path.exists():
        logger.warning("Kerdoiv_kerdesek.md not found at %s", path)
        return ""
    return path.read_text(encoding="utf-8")


ERA_FILTERS = {
    "1945_elott": ["1950 el", "1970 el", "1920"],
    "1945_1970": ["1950 el", "1970 el", "1920", "1950-1970"],
    "1970_1990": ["1970-199", "1970 és 199"],
    "1990_2010": ["1990", "2010", "pan"],
    "2010_utan": ["2010", "pan"],
}

TYPE_FILTERS = {
    "tegla": ["tégla", "tégla"],
    "panel": ["panel", "csúszó", "csuszó"],
    "ujepites": ["pan", "újép", "ujep"],
    "ismeretlen": ["tégla"],
}


def get_questions_for_profile(
    building_type: str, era: str, condition: str, questions_dir: Path
) -> List[Dict[str, str]]:
    text = load_questions_text(questions_dir)
    if not text:
        return []

    era_kw = ERA_FILTERS.get(era, ["1970 el"])
    type_kw = TYPE_FILTERS.get(building_type, ["tégla"])

    lines = text.split("\n")
    general_qs = []
    relevant_lines = []
    in_relevant_section = False
    line_num = 0

    for line in lines:
        line_num += 1
        stripped = line.strip()
        if not stripped:
            continue

        if line_num <= 70:
            if re.match(r"^\d+\.", stripped):
                clean = re.sub(r"^\d+\.\s*\*{0,3}", "", stripped).rstrip("*")
                clean = re.sub(r"\*{2,}", "", clean).strip()
                if clean:
                    general_qs.append(
                        {
                            "section": "kerdesek",
                            "text": clean,
                        }
                    )
                    if "Melyik" in clean or "típusa" in clean or "építés" in clean:
                        for sub in range(line_num + 1, min(line_num + 8, len(lines))):
                            sub_line = lines[sub].strip()
                            if sub_line and re.match(r"^[a-z]\)|^-", sub_line):
                                sub_clean = re.sub(r"^[a-z]\)\s*|^-\s*", "", sub_line)
                                general_qs.append(
                                    {
                                        "section": "kerdesek",
                                        "text": f"  → {sub_clean}",
                                    }
                                )
            continue

        lower = stripped.lower()
        is_header = any(k in lower for k in era_kw) and any(k in lower for k in type_kw)
        if is_header and (
            "előtt" in lower
            or "között" in lower
            or "panel" in lower
            or "csúszó" in lower
            or "csuszó" in lower
        ):
            in_relevant_section = True
            continue
        if in_relevant_section and stripped.startswith("***"):
            break
        if in_relevant_section:
            relevant_lines.append(stripped)

    items = []
    for rl in relevant_lines:
        if rl.startswith("- ") or rl.startswith("• "):
            items.append(
                {
                    "section": "ellenorzes",
                    "text": rl.lstrip("- •").strip(),
                }
            )

    if condition in ("nagyon_rossz", "kozepes"):
        items.append(
            {
                "section": "piros_zaszlo",
                "text": (
                    f"A lakás állapota '{condition}' — ez jelentős rejtett "
                    f"költségekre utalhat. Mindenképp szakemberrel érdemes "
                    f"átvizsgáltatni a teljes elektromos hálózatot, "
                    f"a víz- és fűtésrendszert, valamint a nyílászárókat."
                ),
            }
        )

    return general_qs + items
