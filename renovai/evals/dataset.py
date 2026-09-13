import json
from pathlib import Path
from typing import List

from .models import EvalExample

GOLDEN_DATASET = [
    EvalExample(
        id="slag_001",
        question="Milyen veszélyei vannak a kohósalaknak a bontás során?",
        expected_answer_keywords=[
            "nedvesség",
            "duzzadás",
            "repedés",
            "aljzatkiegyenlítő",
            "statikai",
        ],
        expected_sources=["Árajánlat betonozással 1011 Szalag utca 11. 1 per 8..md"],
        category="technical",
    ),
    EvalExample(
        id="pricing_001",
        question="Mennyibe kerül átlagosan egy négyzetméter burkolás munkadíja Budapesten?",
        expected_answer_keywords=["burkolás", "munkadíj", "négyzetméter", "huf", "ft"],
        expected_sources=["Árajánlat - 1092 Bakáts tér 5. 4. emelet.md"],
        category="pricing",
    ),
    EvalExample(
        id="conversion_001",
        question="Hogyan lehet egy konyhából szobát kialakítani?",
        expected_answer_keywords=[
            "gépészet",
            "vízvezeték",
            "elzárás",
            "amerikai konyha",
            "strang",
        ],
        expected_sources=["1135 Mór utca 19 - árajánlat.md"],
        category="technical",
    ),
    EvalExample(
        id="era_1970",
        question="Mire kell figyelni egy 1970 körül épült tégla lakás villanyszerelésénél?",
        expected_answer_keywords=[
            "alumínium",
            "vezeték",
            "csere",
            "földelés",
            "fi-relé",
        ],
        expected_sources=["Árajánlat - 1081 II. János Pál pápa tér 22. 1. em..md"],
        category="technical",
    ),
]


def load_golden_dataset(path: Optional[Path] = None) -> List[EvalExample]:
    if path and path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [EvalExample(**ex) for ex in data]
    return GOLDEN_DATASET


def save_golden_dataset(examples: List[EvalExample], path: Path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump([ex.model_dump() for ex in examples], f, ensure_ascii=False, indent=2)
