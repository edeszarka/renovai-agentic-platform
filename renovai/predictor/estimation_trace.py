"""Lightweight structured tracing for the cost estimation pipeline.

Writes one JSON object per estimation run to ``logs/estimation_trace.jsonl``
(appended).  Enabled by default; disable with ``ESTIMATION_TRACE_ENABLED=0``
or ``--no-trace`` env-var / flag.

Zero external dependencies — stdlib ``json`` + ``pathlib`` only.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

_ENABLED = os.getenv("ESTIMATION_TRACE_ENABLED", "1") != "0"
_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "estimation_trace.jsonl"

_IVW_EPS = 1.0


def is_enabled() -> bool:
    return _ENABLED


def _ensure_log_dir() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# EstimationTrace — accumulator that lives for one estimation run
# ---------------------------------------------------------------------------

class EstimationTrace:
    """Collects structured trace data for a single cost-estimation run.

    Usage::

        trace = EstimationTrace(pipeline="scope_matched_estimate")
        trace.record_input(...)
        trace.record_category(name, raw_values, quote_ids, weighted_avg, count)
        trace.record_subtotal("base_per_sqm_sum", value)
        ...
        trace.write()  # appends to estimation_trace.jsonl
    """

    def __init__(self, pipeline: str) -> None:
        self.run_id: str = uuid.uuid4().hex[:12]
        self.timestamp: str = datetime.now(timezone.utc).isoformat()
        self.pipeline: str = pipeline
        self._record: Dict[str, Any] = {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "pipeline": pipeline,
            "input": {},
            "category_breakdown": {},
            "subtotals": {},
            "output": {},
        }

    # -- input params --------------------------------------------------------

    def record_input(self, **kwargs: Any) -> None:
        self._record["input"].update(kwargs)

    # -- per-category breakdown ----------------------------------------------

    def record_category(
        self,
        name: str,
        raw_values: List[float],
        quote_ids: List[str],
        weighted_avg: float,
        count: int,
        data_quality: str = "sufficient",
    ) -> None:
        """Record per-category IVW breakdown.

        Parameters
        ----------
        name : str
            Scope name (e.g. ``"needs_plumbing"``).
        raw_values : list[float]
            Per-sqm cost for each matched quote.
        quote_ids : list[str]
            Identifier for each matched quote (file name or DB id).
        weighted_avg : float
            The inverse-variance-weighted average that feeds into the estimate.
        count : int
            Number of matched quotes (``len(raw_values)``).
        data_quality : str
            ``"sufficient"`` (>=2 quotes), ``"single_quote"``, or
            ``"no_corpus_data"``.
        """
        arr = np.array(raw_values, dtype=float) if raw_values else np.array([], dtype=float)
        weights: List[float] = []
        if len(arr) >= 2:
            mean = arr.mean()
            variances = (arr - mean) ** 2 + _IVW_EPS
            w = 1.0 / variances
            weights = [round(float(x), 6) for x in w / w.sum()]
        elif len(arr) == 1:
            weights = [1.0]
        else:
            weights = []

        self._record["category_breakdown"][name] = {
            "quote_count": count,
            "data_quality": data_quality,
            "matched_quotes": [
                {"id": qid, "raw_per_sqm": round(v, 2), "weight": wt}
                for qid, v, wt in zip(quote_ids, raw_values, weights)
            ],
            "weighted_avg_per_sqm": round(weighted_avg, 2),
        }

    # -- running subtotals ---------------------------------------------------

    def record_subtotal(self, step: str, value: Any) -> None:
        self._record["subtotals"][step] = value

    def record_subtotal_bool(self, step: str, value: bool) -> None:
        self._record["subtotals"][step] = value

    # -- final output --------------------------------------------------------

    def record_output(self, **kwargs: Any) -> None:
        self._record["output"].update(kwargs)

    # -- serialise & write ---------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Return a deep-ish copy of the record (for inspection)."""
        return json.loads(json.dumps(self._record))

    def write(self) -> None:
        """Append the trace record to ``logs/estimation_trace.jsonl``."""
        if not _ENABLED:
            return
        _ensure_log_dir()
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(self._record, ensure_ascii=False) + "\n")
