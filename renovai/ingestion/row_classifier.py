import re
from typing import List, Literal, Optional
from pydantic import BaseModel
from .column_detector import ColumnMap
from .number_parser import parse_huf, parse_text_summary_line

SECTION_KEYWORDS = {
    "alternatives": [
        "javasolt", "opcionális", "kiegészítések",
        "helyettesítő", "i. eset", "ii. eset",
        "cserélitek", "maradnak",
    ],
    "not_included": ["ami nincs benne", "nincs benne az árban"],
    "buyer_purchases": [
        "tulajdonos vásárolja", "amit a tulajdonos vásárol",
        "megrendelő vásárolja",
    ],
    "general_notes": [
        "egyéb megjegyzések", "megjegyzések", "megjegyzések / észrevételek",
    ],
    "phase_label": ["felújítás i.", "felújítás ii.", "i. fázis", "ii. fázis"],
}

RowType = Literal[
    "header", "phase_label", "line_item", "section_header",
    "total_row", "text_summary", "empty", "metadata_note"
]

class ClassifiedRow(BaseModel):
    row_index: int
    row_type: RowType
    section: str              # current active section name
    version: int              # 1=main, 2=alternative version 2, 3=version 3
    phase: int                # 1 or 2 (for multi_phase style)
    raw: tuple                # original cell values

def classify_rows(
    rows: List[tuple],
    col_map: ColumnMap
) -> List[ClassifiedRow]:
    """Walks rows in order and classifies each based on content and state."""
    classified = []
    current_section = "main"
    current_version = 1
    current_phase = 1
    
    header_found = False

    for idx, row in enumerate(rows):
        if idx == col_map.header_row_index:
            header_found = True
            classified.append(ClassifiedRow(
                row_index=idx, row_type="header", section=current_section,
                version=current_version, phase=current_phase, raw=row
            ))
            continue

        # Check for empty
        if all(cell is None or str(cell).strip() == "" for cell in row):
            classified.append(ClassifiedRow(
                row_index=idx, row_type="empty", section=current_section,
                version=current_version, phase=current_phase, raw=row
            ))
            continue

        # Helper to get cell value by index if it exists
        def get_val(col_idx):
            if col_idx is not None and col_idx < len(row):
                return row[col_idx]
            return None

        work_val = get_val(col_map.work_name)
        work_text = str(work_val).strip() if work_val is not None else ""
        
        labor_val = parse_huf(get_val(col_map.labor))
        material_val = parse_huf(get_val(col_map.material))
        total_val = parse_huf(get_val(col_map.total))

        # Check for Phase Label
        work_text_lower = work_text.lower()
        is_phase_label = any(kw in work_text_lower for kw in SECTION_KEYWORDS["phase_label"])
        if is_phase_label:
            if "ii" in work_text_lower or "2" in work_text_lower:
                current_phase = 2
            else:
                current_phase = 1
            classified.append(ClassifiedRow(
                row_index=idx, row_type="phase_label", section=current_section,
                version=current_version, phase=current_phase, raw=row
            ))
            continue

        # Check for Total Row (Subtotal)
        if not work_text and total_val is not None:
            classified.append(ClassifiedRow(
                row_index=idx, row_type="total_row", section=current_section,
                version=current_version, phase=current_phase, raw=row
            ))
            continue

        # Check for Section Header
        if work_text and labor_val is None and material_val is None and total_val is None:
            matched_section = None
            for sec_name, keywords in SECTION_KEYWORDS.items():
                if any(kw in work_text_lower for kw in keywords):
                    matched_section = sec_name
                    break
            
            if matched_section:
                if matched_section == "alternatives":
                    current_version = 2
                    if "helyettesítő" in work_text_lower:
                        current_version = 3
                    elif "ii. eset" in work_text_lower:
                        current_version = 3
                    elif "i. eset" in work_text_lower:
                        current_version = 2
                elif matched_section in ["not_included", "buyer_purchases", "general_notes"]:
                    current_section = matched_section
                
                classified.append(ClassifiedRow(
                    row_index=idx, row_type="section_header", section=current_section,
                    version=current_version, phase=current_phase, raw=row
                ))
                continue
            
            # Check for Text Summary (Style A)
            summary = parse_text_summary_line(work_text)
            if summary:
                classified.append(ClassifiedRow(
                    row_index=idx, row_type="text_summary", section=current_section,
                    version=current_version, phase=current_phase, raw=row
                ))
                continue
            
            # If Style A, everything after header is a line item
            if col_map.style == "text_only" and header_found:
                classified.append(ClassifiedRow(
                    row_index=idx, row_type="line_item", section=current_section,
                    version=current_version, phase=current_phase, raw=row
                ))
                continue

            # Default to metadata_note if it's text but not a known keyword
            classified.append(ClassifiedRow(
                row_index=idx, row_type="metadata_note", section=current_section,
                version=current_version, phase=current_phase, raw=row
            ))
            continue

        # Check for Line Item
        if work_text:
            classified.append(ClassifiedRow(
                row_index=idx, row_type="line_item", section=current_section,
                version=current_version, phase=current_phase, raw=row
            ))
            continue

        # Catch-all
        classified.append(ClassifiedRow(
            row_index=idx, row_type="empty", section=current_section,
            version=current_version, phase=current_phase, raw=row
        ))

    return classified
