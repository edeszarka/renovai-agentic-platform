from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import date

class LineItem(BaseModel):
    name: str                      # Munkafolyamat / Munka megnevezése
    labor_cost: Optional[int] = None # Munkadíj
    material_cost: Optional[int] = None # Anyag
    total_cost: Optional[int] = None # Összesen
    notes: Optional[str] = None      # Megjegyzés
    section: str                   # which logical section this belongs to (main, alternatives, not_included, etc.)
    version: int                   # 1 = main scope, 2/3 = alternative versions
    phase: int = 1                 # 1 or 2 (for multi_phase style)

class QuoteMetadata(BaseModel):
    file_name: str
    address_raw: Optional[str] = None # extracted from filename
    district: Optional[int] = None # e.g. 1125 -> 12
    postal_code: Optional[int] = None
    street: Optional[str] = None
    house_number: Optional[str] = None
    floor: Optional[str] = None
    quote_date: Optional[date] = None # from file modified date or filename
    total_labor: int = 0
    total_material: int = 0
    grand_total: int = 0
    timeline_weeks_min: Optional[float] = None
    timeline_weeks_max: Optional[float] = None
    start_date_approx: Optional[str] = None # YYYY-MM
    payment_schedule: Optional[str] = None
    valid_eur_rate: Optional[int] = None
    valid_fuel_price: Optional[int] = None
    has_slag_complication: bool = False
    labor_vat_included: bool = True
    materials_brands: List[str] = []

class RenovationQuote(BaseModel):
    metadata: QuoteMetadata
    quote_style: Literal["text_only", "standard_5col", "multi_phase", "scope_only"]
    line_items: List[LineItem]      # version=1
    alternatives: List[LineItem]   # version > 1
    not_included: List[str]        # items explicitly excluded
    buyer_purchases: List[str]     # items buyer must buy separately
    general_notes: List[str]       # free-text notes
    warnings: List[str] = []
