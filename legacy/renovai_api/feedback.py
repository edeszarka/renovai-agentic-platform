import os
import json
import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class BuyerFeedback(BaseModel):
    """Feedback from a buyer comparing actual vs. estimated costs.

    This data is used as a retraining signal to update the priors in the
    ConfidenceModel. If the estimate was significantly off, the
    confidence score for that profile type should be decreased.
    """
    trace_id: str = Field(..., description="Trace ID of the original estimate")
    estimated_low_huf: int | None = None
    estimated_mid_huf: int | None = None
    estimated_high_huf: int | None = None
    actual_cost_huf: int | None = Field(None, description="What the buyer actually paid")
    accuracy_rating: int | None = Field(
        None, ge=1, le=5,
        description="1 = way off, 5 = spot on",
    )
    comments_hu: str | None = None
    district: int | None = None
    area_sqm: float | None = None
    scope_flags: dict[str, bool] | None = None


class AdvisorFeedback(BaseModel):
    """Feedback on the correctness and helpfulness of due-diligence advice.

    Used to fine-tune the red-flag detection and inspection checklist
    generation in the Due-Diligence Advisor.
    """
    trace_id: str = Field(..., description="Trace ID of the advisory session")
    red_flags_accurate: bool | None = Field(
        None, description="Were the identified red flags correct?",
    )
    missed_red_flags: list[str] | None = Field(
        None, description="Red flags the advisor missed",
    )
    questions_for_seller_helpful: bool | None = None
    overall_rating: int | None = Field(None, ge=1, le=5)
    comments_hu: str | None = None


class FeedbackBatch(BaseModel):
    """Batch of feedback entries for efficient BigQuery ingestion."""
    entries: list[BuyerFeedback | AdvisorFeedback]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

FEEDBACK_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "feedback"


def _ensure_feedback_dir():
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)


def _write_feedback(record: dict[str, Any], feedback_type: str) -> str:
    """Append a feedback record to the JSONL file for the given type."""
    _ensure_feedback_dir()
    file_path = FEEDBACK_DIR / f"{feedback_type}.jsonl"
    record["_recorded_at"] = datetime.now(timezone.utc).isoformat()
    record["_feedback_id"] = f"fb-{uuid.uuid4().hex[:12]}"
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record["_feedback_id"]


def _read_feedback(feedback_type: str) -> list[dict[str, Any]]:
    """Read all feedback records of a given type."""
    file_path = FEEDBACK_DIR / f"{feedback_type}.jsonl"
    if not file_path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed feedback entry in %s", file_path)
    return records


def _feedback_to_bigquery_rows(feedback_type: str) -> list[dict[str, Any]]:
    """Convert JSONL feedback to BigQuery-compatible row dicts.

    BigQuery table schema (partitioned by _recorded_at date):
        _feedback_id: STRING
        trace_id: STRING
        feedback_type: STRING
        payload: JSON
        _recorded_at: TIMESTAMP
    """
    rows = []
    for record in _read_feedback(feedback_type):
        rows.append({
            "_feedback_id": record.get("_feedback_id"),
            "trace_id": record.get("trace_id", ""),
            "feedback_type": feedback_type,
            "payload": json.dumps(
                {k: v for k, v in record.items() if not k.startswith("_")},
                ensure_ascii=False,
            ),
            "_recorded_at": record.get("_recorded_at"),
        })
    return rows


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------


@router.post("/buyer")
async def submit_buyer_feedback(feedback: BuyerFeedback) -> dict[str, Any]:
    """Submit buyer feedback comparing actual vs. estimated costs."""
    try:
        fb_id = _write_feedback(feedback.model_dump(exclude_none=True), "buyer_feedback")
        logger.info("[%s] Buyer feedback recorded: %s", feedback.trace_id, fb_id)
        return {"status": "ok", "feedback_id": fb_id}
    except Exception as exc:
        logger.exception("Failed to record buyer feedback")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/advisor")
async def submit_advisor_feedback(feedback: AdvisorFeedback) -> dict[str, Any]:
    """Submit feedback on due-diligence advisor correctness."""
    try:
        fb_id = _write_feedback(feedback.model_dump(exclude_none=True), "advisor_feedback")
        logger.info("[%s] Advisor feedback recorded: %s", feedback.trace_id, fb_id)
        return {"status": "ok", "feedback_id": fb_id}
    except Exception as exc:
        logger.exception("Failed to record advisor feedback")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/batch")
async def submit_feedback_batch(batch: FeedbackBatch) -> dict[str, Any]:
    """Submit a batch of feedback entries."""
    ids: list[str] = []
    for entry in batch.entries:
        if isinstance(entry, BuyerFeedback):
            fb_id = _write_feedback(entry.model_dump(exclude_none=True), "buyer_feedback")
        elif isinstance(entry, AdvisorFeedback):
            fb_id = _write_feedback(entry.model_dump(exclude_none=True), "advisor_feedback")
        else:
            continue
        ids.append(fb_id)
    return {"status": "ok", "feedback_ids": ids, "count": len(ids)}


@router.get("/export/bigquery")
async def export_to_bigquery() -> dict[str, Any]:
    """Return all feedback as BigQuery-compatible row dicts.

    This endpoint is called periodically by a cron job to stream
    feedback data into BigQuery partitioned tables for retraining
    signal analysis.
    """
    return {
        "buyer_feedback": _feedback_to_bigquery_rows("buyer_feedback"),
        "advisor_feedback": _feedback_to_bigquery_rows("advisor_feedback"),
    }
