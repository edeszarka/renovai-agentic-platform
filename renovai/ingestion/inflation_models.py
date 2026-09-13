from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel

from .models import LineItem, QuoteMetadata


class CPIRecord(BaseModel):
    year: int
    quarter: int  # 1–4
    index_value: float  # relative to base 2024 Q1 = 1.000


class PriceIndex(BaseModel):
    base_year: int = 2024
    base_quarter: int = 1
    materials: List[CPIRecord]
    labor: List[CPIRecord]
    generated_at: datetime


class AdjustedLineItem(BaseModel):
    original: LineItem  # from Module 1 models
    labor_cost_adjusted: Optional[int] = None
    material_cost_adjusted: Optional[int] = None
    total_cost_adjusted: int
    adjustment_factor_labor: float
    adjustment_factor_materials: float
    target_date: date


class AdjustedQuote(BaseModel):
    original_metadata: QuoteMetadata
    target_date: date
    line_items_adjusted: List[AdjustedLineItem]
    grand_total_original: int
    grand_total_adjusted: int
    inflation_delta_pct: float  # e.g. +12.3 means 12.3% more expensive today
