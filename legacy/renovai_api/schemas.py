from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import date, datetime

class EstimateRequest(BaseModel):
    district: int
    area_sqm: float
    num_rooms: int
    building_era: Optional[int] = None
    needs_plumbing: bool = True
    needs_electrical: bool = False
    needs_flooring: bool = True
    needs_full_demolition: bool = True
    suspected_slag: bool = False
    target_date: Optional[date] = None

class EstimateResponse(BaseModel):
    estimate_low_huf: int
    estimate_mid_huf: int
    estimate_high_huf: int
    inflation_adjusted_to: date
    similar_cases: List[dict]
    model_confidence: str
    warning: Optional[str] = None

class AdvisoryRequest(BaseModel):
    district: int
    area_sqm: float
    num_rooms: int
    building_type: str
    building_era_approx: Optional[str] = None
    current_condition: str
    known_issues: List[str] = []
    has_seen_in_person: bool = False
    asking_price_million_huf: Optional[float] = None

class AdvisoryResponse(BaseModel):
    questions_for_seller: List[dict]
    inspection_checklist: List[dict]
    red_flags: List[dict]
    cost_estimate: dict
    overall_risk: str
    summary_hu: str
    report_markdown: str
    sources_used: List[str]
    generated_at: datetime

class QueryRequest(BaseModel):
    question: str
    district_filter: Optional[int] = None
    conversation_history: Optional[List[dict]] = []

class QueryResponse(BaseModel):
    answer: str
    sources: List[str]
    confidence: str

class HealthResponse(BaseModel):
    status: str
    vector_store_stats: dict
    model_loaded: bool
    uptime_seconds: float

class IngestRequest(BaseModel):
    quotes_dir: str = "data/raw/quotes/"
    adjust_inflation: bool = True

class IngestResponse(BaseModel):
    task_id: str
    status: str
