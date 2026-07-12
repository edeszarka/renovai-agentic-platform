import pandas as pd
import logging
from pathlib import Path
from datetime import date, datetime
from typing import List, Optional, Literal, Dict
from .models import RenovationQuote, LineItem
from .inflation_models import CPIRecord, PriceIndex, AdjustedLineItem, AdjustedQuote

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CPI DATA SOURCE
# ---------------------------------------------------------------------------
# Index values come from KSH STADAT Table 1.2.1.25:
#   "Az építőipar termelőiár-indexei, negyedévente, évkezdettől kumulált"
#   (Construction producer price indices, quarterly, cumulative from year start)
#
# Source URL: https://www.ksh.hu/stadat_files/ara/hu/ara0061.html
# Last KSH update: 2026-05-13.  Data covers 2021 Q1 – 2026 Q1.
#
# IMPORTANT: This is an OUTPUT-based producer price index for the
# construction sector (TEÁOR'08 section F).  It is the closest publicly
# available proxy for renovation cost inflation but is NOT a direct
# "cost to renovate" index — it measures the price at which construction
# output is sold, which includes margin, overhead, and productivity effects
# that a pure cost index would not.
#
# LABOR vs MATERIALS SPLIT: KSH Table 1.8.1.1 ("Építőipari költség alapú
# árindexek") publishes separate indices for building services/mechanical
# installation (45.3, labor-heavy proxy) and finishing construction (45.4,
# materials-heavy proxy).  However, that table is ARCHIVED (last data 2007)
# and no longer updated.  No current KSH table provides a quarterly
# labor/materials split.  Both CSV files therefore use the same overall
# construction index.  This is a known limitation — revisit if ÉVOSZ or MNB
# publishes component-level data.
#
# 2021 Q2–Q4 values are back-calculated from 2022 YoY ratios (KSH table
# provides YoY for 2022 Q1–Q4 = 120.6, 125.0, 126.2, 126.2).
#
# Data should be periodically refreshed from the same STADAT table.
# ---------------------------------------------------------------------------

def load_price_index(materials_path: Path, labor_path: Path) -> PriceIndex:
    """Loads KSH construction producer price index data from CSV files."""
    materials = []
    if materials_path.exists():
        df = pd.read_csv(materials_path)
        for _, row in df.iterrows():
            materials.append(CPIRecord(
                year=int(row["year"]),
                quarter=int(row["quarter"]),
                index_value=float(row["index_value"])
            ))
    
    labor = []
    if labor_path.exists():
        df = pd.read_csv(labor_path)
        for _, row in df.iterrows():
            labor.append(CPIRecord(
                year=int(row["year"]),
                quarter=int(row["quarter"]),
                index_value=float(row["index_value"])
            ))
            
    return PriceIndex(
        materials=materials,
        labor=labor,
        generated_at=datetime.now()
    )

def _get_date_score(d: date) -> float:
    """Converts a date to a fractional year+quarter score for interpolation.
    Each quarter is 0.25 units.
    Mid-Q1 is year + 0.125
    Mid-Q2 is year + 0.375
    Mid-Q3 is year + 0.625
    Mid-Q4 is year + 0.875
    """
    # Days from start of year / days in year * 1.0
    day_of_year = d.timetuple().tm_yday
    days_in_year = 366 if (d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0)) else 365
    return d.year + (day_of_year - 1) / days_in_year

def _get_q_score(year: int, quarter: int) -> float:
    """Mid-quarter score."""
    return year + (quarter - 1) * 0.25 + 0.125

def get_factor(
    index: PriceIndex,
    component: Literal["materials", "labor"],
    from_date: date,
    to_date: date
) -> float:
    """Returns the multiplier to apply, with linear interpolation between quarters."""
    records = index.materials if component == "materials" else index.labor
    if not records:
        return 1.0
        
    # Sort records by date
    sorted_records = sorted(records, key=lambda x: (x.year, x.quarter))
    
    def get_interpolated_value(target_date: date) -> float:
        target_score = _get_date_score(target_date)
        
        # Boundary checks
        first_score = _get_q_score(sorted_records[0].year, sorted_records[0].quarter)
        if target_score <= first_score:
            if target_score < first_score:
                logger.warning(f"Date {target_date} is before earliest {component} record ({sorted_records[0].year} Q{sorted_records[0].quarter}). Using earliest value.")
            return sorted_records[0].index_value
            
        last_score = _get_q_score(sorted_records[-1].year, sorted_records[-1].quarter)
        if target_score >= last_score:
            if target_score > last_score:
                logger.warning(
                    f"Date {target_date} is after latest {component} record "
                    f"({sorted_records[-1].year} Q{sorted_records[-1].quarter}). "
                    f"Extrapolating from trend."
                )
            # Trend-based extrapolation: compute the average quarterly growth
            # rate from the last 4 available data points and project forward.
            # ASSUMPTION: recent trend continues at the same pace.  This should
            # be replaced with real data once more recent KSH/ÉVOSZ quarters are
            # sourced.  Falls back to flat clamping if fewer than 2 transitions
            # are available.
            last_val = sorted_records[-1].index_value
            if len(sorted_records) >= 3:
                # Use last 4 records (3 transitions) to compute avg multiplier
                recent = sorted_records[-4:]
                multipliers = [
                    recent[i + 1].index_value / recent[i].index_value
                    for i in range(len(recent) - 1)
                    if recent[i].index_value > 0
                ]
                if multipliers:
                    avg_multiplier = sum(multipliers) / len(multipliers)
                    quarters_ahead = (target_score - last_score) / 0.25
                    return last_val * (avg_multiplier ** quarters_ahead)
            return last_val
            
        # Find the two quarters to interpolate between
        for i in range(len(sorted_records) - 1):
            s1 = _get_q_score(sorted_records[i].year, sorted_records[i].quarter)
            s2 = _get_q_score(sorted_records[i+1].year, sorted_records[i+1].quarter)
            
            if s1 <= target_score <= s2:
                v1 = sorted_records[i].index_value
                v2 = sorted_records[i+1].index_value
                # Linear interpolation: y = y1 + (x - x1) * (y2 - y1) / (x2 - x1)
                return v1 + (target_score - s1) * (v2 - v1) / (s2 - s1)
                
        return sorted_records[-1].index_value

    val_from = get_interpolated_value(from_date)
    val_to = get_interpolated_value(to_date)
    
    return val_to / val_from

def adjust_quote(
    quote: RenovationQuote,
    price_index: PriceIndex,
    target_date: date
) -> AdjustedQuote:
    """Produces an inflation-adjusted version of a quote."""
    from_date = quote.metadata.quote_date or date(2024, 1, 1) # Fallback
    
    factor_labor = get_factor(price_index, "labor", from_date, target_date)
    factor_materials = get_factor(price_index, "materials", from_date, target_date)
    
    adjusted_items = []
    
    for item in quote.line_items + quote.alternatives:
        labor_adj = None
        material_adj = None
        
        # Rules for applying inflation:
        if item.labor_cost is not None and item.material_cost is None:
            # 100% labor index applied to total (which is labor_cost in this case)
            labor_adj = int(round(item.labor_cost * factor_labor))
            total_adj = labor_adj
        elif item.material_cost is not None and item.labor_cost is None:
            # 100% materials index applied to total
            material_adj = int(round(item.material_cost * factor_materials))
            total_adj = material_adj
        elif item.labor_cost is not None and item.material_cost is not None:
            # Both present
            labor_adj = int(round(item.labor_cost * factor_labor))
            material_adj = int(round(item.material_cost * factor_materials))
            total_adj = labor_adj + material_adj
        else:
            # Both are None (section header or lump-sum)
            # apply labor index to total (conservative assumption for lump sums)
            # If total_cost is None, we use 0
            original_total = item.total_cost or 0
            total_adj = int(round(original_total * factor_labor))
            if original_total > 0:
                 labor_adj = total_adj # Assume it's labor
        
        adj_item = AdjustedLineItem(
            original=item,
            labor_cost_adjusted=labor_adj,
            material_cost_adjusted=material_adj,
            total_cost_adjusted=total_adj,
            adjustment_factor_labor=factor_labor,
            adjustment_factor_materials=factor_materials,
            target_date=target_date
        )
        adjusted_items.append(adj_item)

    # Filter for version 1 items for grand total
    v1_adjusted = [it for it in adjusted_items if it.original.version == 1]
    grand_total_adjusted = sum(it.total_cost_adjusted for it in v1_adjusted)
    
    original_grand_total = quote.metadata.grand_total
    inflation_delta_pct = 0.0
    if original_grand_total > 0:
        inflation_delta_pct = (grand_total_adjusted - original_grand_total) / original_grand_total * 100
        
    return AdjustedQuote(
        original_metadata=quote.metadata,
        target_date=target_date,
        line_items_adjusted=adjusted_items,
        grand_total_original=original_grand_total,
        grand_total_adjusted=grand_total_adjusted,
        inflation_delta_pct=round(inflation_delta_pct, 2)
    )

def build_aggregated_index(
    all_quotes: List[RenovationQuote],
    price_index: PriceIndex,
    target_date: Optional[date] = None
) -> dict:
    """Builds the aggregated price index JSON structure. Uses target_date (default: today) for adjustments."""
    if target_date is None:
        target_date = date.today()
        
    summary = []
    for q in all_quotes:
        adj = adjust_quote(q, price_index, target_date)
        m = adj.original_metadata
        summary.append({
            "file": m.file_name,
            "address": m.address_raw,
            "district": m.district,
            "quote_date": str(m.quote_date) if m.quote_date else None,
            "grand_total_original": adj.grand_total_original,
            "grand_total_adjusted_to_today": adj.grand_total_adjusted,
            "inflation_delta_pct": adj.inflation_delta_pct
        })
        
    # Standardize KSH list outputs as requested
    return {
        "generated_at": datetime.now().isoformat(),
        "base": {"year": price_index.base_year, "quarter": price_index.base_quarter},
        "materials_index": [
            {"year": r.year, "quarter": r.quarter, "value": r.index_value}
            for r in sorted(price_index.materials, key=lambda x: (x.year, x.quarter))
        ],
        "labor_index": [
            {"year": r.year, "quarter": r.quarter, "value": r.index_value}
            for r in sorted(price_index.labor, key=lambda x: (x.year, x.quarter))
        ],
        "quote_summary": summary
    }

