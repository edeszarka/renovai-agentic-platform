from typing import List, Optional, Literal
from pydantic import BaseModel

CANONICAL_COLUMNS = {
    "work_name":   ["Munkafolyamat", "Munka megnevezése"],
    "material":    ["Anyag"],
    "labor":       ["Munkadíj"],
    "total":       ["Összesen", "Összsen"],       # handle typo
    "notes":       ["Megjegyzés", "Megjegyzések", "Egyéb megjegyzések", "Egyéb megjegyzés"],
}

class ColumnMap(BaseModel):
    work_name: int       # column index
    material:  Optional[int] = None
    labor:     Optional[int] = None
    total:     Optional[int] = None
    notes:     Optional[int] = None
    style:     Literal["text_only", "standard_5col", "multi_phase", "scope_only"]
    header_row_index: int

def detect_style_and_columns(rows: List[tuple]) -> ColumnMap:
    """Detects style and maps columns from XLSX rows."""
    style: Literal["text_only", "standard_5col", "multi_phase", "scope_only"] = "standard_5col"
    header_row_index = 0
    
    # 1. Find the first non-empty row
    first_row = None
    for i, row in enumerate(rows):
        if any(cell is not None and str(cell).strip() != "" for cell in row):
            first_row = row
            header_row_index = i
            break
    
    if first_row is None:
        raise ValueError("Workbook is empty")

    # 2. Check for multi_phase (Row 0 is a label)
    if first_row[0] and isinstance(first_row[0], str) and first_row[0].startswith("Felújítás") and (len(first_row) < 2 or first_row[1] is None):
        style = "multi_phase"
        # Real header should be in the next non-empty rows
        for i in range(header_row_index + 1, len(rows)):
            if any(cell is not None and str(cell).strip() != "" for cell in rows[i]):
                first_row = rows[i]
                header_row_index = i
                break
    
    # 3. Map columns
    col_mapping = {}
    found_cols = 0
    for idx, cell in enumerate(first_row):
        if cell is None:
            continue
        cell_text = str(cell).strip().lower()
        for key, aliases in CANONICAL_COLUMNS.items():
            if any(alias.lower() == cell_text for alias in aliases):
                col_mapping[key] = idx
                found_cols += 1
                break

    # 4. Refine style
    if found_cols <= 2:
        style = "text_only"
    elif col_mapping.get("work_name") is not None:
        work_name_text = str(first_row[col_mapping["work_name"]]).strip()
        if work_name_text == "Munka megnevezése":
            style = "scope_only"
        elif "material" in col_mapping and "labor" in col_mapping:
            if col_mapping["material"] < col_mapping["labor"]:
                if style != "multi_phase":
                    style = "standard_5col"
            else:
                style = "scope_only" # handles reversed columns
        else:
            style = "scope_only"

    return ColumnMap(
        work_name=col_mapping.get("work_name", 0),
        material=col_mapping.get("material"),
        labor=col_mapping.get("labor"),
        total=col_mapping.get("total"),
        notes=col_mapping.get("notes"),
        style=style,
        header_row_index=header_row_index
    )
