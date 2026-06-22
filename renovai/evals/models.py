from pydantic import BaseModel
from typing import List, Optional, Dict

class EvalExample(BaseModel):
    """A single 'Golden' example for evaluation."""
    id: str
    question: str
    expected_answer_keywords: List[str]  # Key technical terms that must be in the answer
    expected_sources: List[str]          # Files that should be retrieved/cited
    category: str                        # e.g., "technical", "pricing", "legal"

class EvalResult(BaseModel):
    """Result of running a single example through the RAG pipeline."""
    example_id: str
    question: str
    actual_answer: str
    actual_sources: List[str]
    
    # Simple Metrics
    keyword_match_score: float           # % of expected keywords found in actual answer
    source_recall: float                 # % of expected sources found in actual sources
    
    # LLM Metrics (optional for later)
    faithfulness_score: Optional[float] = None 
    model_used: str
    duration_sec: float

class EvalSuiteReport(BaseModel):
    """Summary of an entire evaluation run."""
    timestamp: str
    total_examples: int
    avg_keyword_score: float
    avg_source_recall: float
    results: List[EvalResult]
