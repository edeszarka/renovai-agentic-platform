import openpyxl
import logging
from pathlib import Path
from datetime import date
from typing import List, Optional

from .models import LineItem, QuoteMetadata, RenovationQuote
from .column_detector import detect_style_and_columns, ColumnMap
from .row_classifier import classify_rows, ClassifiedRow
from .number_parser import parse_huf, parse_text_summary_line
from .address_extractor import extract_address
from .metadata_extractor import extract_metadata_from_notes

logger = logging.getLogger(__name__)

def parse_quote(xlsx_path: Path) -> RenovationQuote:
    """Parses an XLSX renovation quote into a RenovationQuote model."""
    try:
        # Load workbook with openpyxl (read_only=True, data_only=True)
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            raise ValueError(f"Empty worksheet in {xlsx_path}")
    except Exception as e:
        logger.error(f"Failed to read {xlsx_path}: {e}")
        raise

    # 1. Detect style and map columns
    col_map = detect_style_and_columns(rows)
    
    # 2. Classify rows
    classified_rows = classify_rows(rows, col_map)
    
    # 3. Initialize components
    line_items = []
    alternatives = []
    not_included = []
    buyer_purchases = []
    general_notes = []
    warnings = []
    
    grand_total_from_text = {"material": None, "labor": None, "total": None}
    
    # 4. Extract data
    for cr in classified_rows:
        row = cr.raw
        
        if cr.row_type == "line_item":
            work_name = str(row[col_map.work_name]).strip() if row[col_map.work_name] is not None else ""
            if not work_name:
                continue
                
            labor = parse_huf(row[col_map.labor]) if col_map.labor is not None else None
            material = parse_huf(row[col_map.material]) if col_map.material is not None else None
            total = parse_huf(row[col_map.total]) if col_map.total is not None else None
            
            # Handle lump sum if total exists but labor/material are missing
            if total is not None and labor is None and material is None:
                pass # Already handled by parse_huf returning None
            
            notes = str(row[col_map.notes]).strip() if col_map.notes is not None and row[col_map.notes] is not None else None
            
            item = LineItem(
                name=work_name,
                labor_cost=labor,
                material_cost=material,
                total_cost=total or ((labor or 0) + (material or 0)) if (labor or material) else total,
                notes=notes,
                section=cr.section,
                version=cr.version,
                phase=cr.phase
            )
            
            if item.version == 1:
                line_items.append(item)
            else:
                alternatives.append(item)
                
        elif cr.row_type == "text_summary":
            work_text = str(row[col_map.work_name]).strip()
            summary = parse_text_summary_line(work_text)
            if summary:
                grand_total_from_text[summary["component"]] = summary["value"]
                
        elif cr.row_type == "section_header":
            # Some text might be in other columns too
            text = " ".join([str(c) for c in row if c is not None])
            if cr.section == "not_included":
                pass # Just a header
            elif cr.section == "buyer_purchases":
                pass
            elif cr.section == "general_notes":
                pass

        # Collect strings for non-item sections
        work_text = str(row[col_map.work_name]).strip() if row[col_map.work_name] is not None else ""
        if work_text:
            if cr.section == "not_included" and cr.row_type != "section_header":
                not_included.append(work_text)
            elif cr.section == "buyer_purchases" and cr.row_type != "section_header":
                buyer_purchases.append(work_text)
            elif cr.section == "general_notes" and cr.row_type != "section_header":
                general_notes.append(work_text)
            elif cr.row_type == "metadata_note":
                 general_notes.append(work_text)

    # 5. Calculate Totals
    total_labor = sum(item.labor_cost or 0 for item in line_items)
    total_material = sum(item.material_cost or 0 for item in line_items)
    grand_total = sum(item.total_cost or 0 for item in line_items)
    
    # Style A check: if grand_total is 0 but we have text summary, use that
    if grand_total == 0 and grand_total_from_text["total"]:
        grand_total = grand_total_from_text["total"]
        total_labor = grand_total_from_text["labor"] or 0
        total_material = grand_total_from_text["material"] or 0
    elif grand_total > 0 and grand_total_from_text["total"]:
        # Sanity check
        if abs(grand_total - grand_total_from_text["total"]) > 1000:
             warnings.append(f"Grand total mismatch: calculated {grand_total}, text summary {grand_total_from_text['total']}")

    # 6. Extract Address
    addr_info = extract_address(xlsx_path.stem)
    
    # 7. Extract Metadata from Notes
    full_text = "\n".join([" ".join([str(c) for c in r if c is not None]) for r in rows])
    meta_info = extract_metadata_from_notes(general_notes, grand_total, full_text)
    
    # 8. Assemble Quote
    metadata = QuoteMetadata(
        file_name=xlsx_path.name,
        address_raw=addr_info["address_raw"],
        district=addr_info["district"],
        postal_code=addr_info["postal_code"],
        street=addr_info["street"],
        house_number=addr_info["house_number"],
        floor=addr_info["floor"],
        quote_date=date.fromtimestamp(xlsx_path.stat().st_mtime),
        total_labor=total_labor,
        total_material=total_material,
        grand_total=grand_total,
        timeline_weeks_min=meta_info["timeline_weeks_min"],
        timeline_weeks_max=meta_info["timeline_weeks_max"],
        start_date_approx=meta_info["start_date_approx"],
        payment_schedule=meta_info["payment_schedule"],
        valid_eur_rate=meta_info["valid_eur_rate"],
        valid_fuel_price=meta_info["valid_fuel_price"],
        has_slag_complication=meta_info["has_slag_complication"],
        labor_vat_included=meta_info["labor_vat_included"],
        materials_brands=meta_info["materials_brands"]
    )
    
    return RenovationQuote(
        metadata=metadata,
        quote_style=col_map.style,
        line_items=line_items,
        alternatives=alternatives,
        not_included=not_included,
        buyer_purchases=buyer_purchases,
        general_notes=general_notes,
        warnings=warnings
    )

def parse_all_quotes(quotes_dir: Path) -> List[RenovationQuote]:
    """Parses all XLSX files in a directory."""
    quotes = []
    for file in quotes_dir.rglob("*.xlsx"):
        try:
            quotes.append(parse_quote(file))
        except Exception as e:
            logger.error(f"Failed to parse {file}: {e}")
    return quotes
