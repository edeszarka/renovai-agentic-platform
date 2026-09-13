import logging
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generator

logger = logging.getLogger(__name__)

# Regex patterns for PII sanitization in trace data.
_SANITIZE_PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "REDACTED_IP"),
    (r"\b[\w\.-]+@[\w\.-]+\.\w+\b", "REDACTED_EMAIL"),
    (r"\b\d{6,12}\b", "REDACTED_ID"),
    (r"\b(?:\+36|06)[-\s]?\d{1,2}[-\s]?\d{3,4}[-\s]?\d{3,4}\b", "REDACTED_PHONE"),
    (
        r"\b[1-9]\d{3}\s(?:[A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]+(?:\s[A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]+)*)\b",
        "REDACTED_ADDRESS",
    ),
]


def sanitize_pii(message: str) -> str:
    """Remove PII from a trace message or attribute value.

    Runs regex replacements for IPs, emails, IDs, phone numbers, and
    Hungarian address patterns.
    """
    result = message
    for pattern, replacement in _SANITIZE_PATTERNS:
        result = re.sub(pattern, replacement, result)
    return result


@dataclass
class Span:
    """A single span in the OpenTelemetry-compatible trace tree.

    Follows the semantic convention:
    - agent.think for reasoning steps
    - agent.tool for tool execution latencies
    """

    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    span_type: str  # "agent.think" | "agent.tool"
    start_time: str
    end_time: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # "ok" | "error"
    error_message: str | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        start = datetime.fromisoformat(self.start_time)
        end = datetime.fromisoformat(self.end_time)
        return (end - start).total_seconds() * 1000

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "span_type": self.span_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "status": self.status,
            "error_message": self.error_message,
        }


class RenovAITracer:
    """Lightweight OpenTelemetry-compatible tracer for RenovAI.

    Spans are stored in-memory and can be exported to OpenTelemetry
    backends (Jaeger, Cloud Trace, etc.) via the export() method.

    All span attributes are sanitized for PII before storage.

    Usage:
        tracer = RenovAITracer()
        with tracer.think_span(trace_id, "classify_intent", parent_id=None) as span:
            result = await gateway.classify(question)
            span.set_attribute("intent", result.primary_intent)
    """

    def __init__(self):
        self._spans: list[Span] = []
        self._exporters: list[Any] = []

    @contextmanager
    def think_span(
        self,
        trace_id: str,
        name: str,
        parent_span_id: str | None = None,
    ) -> Generator[Span, None, None]:
        """Create an agent.think span for a reasoning step.

        Usage:
            with tracer.think_span(trace_id, "classify_intent") as span:
                result = await classifier.classify(question)
                span.set_attribute("intent", result.primary_intent)
        """
        span = self._start_span(trace_id, name, "agent.think", parent_span_id)
        try:
            yield span
            span.end_time = datetime.now(timezone.utc).isoformat()
            span.status = "ok"
        except Exception as exc:
            span.end_time = datetime.now(timezone.utc).isoformat()
            span.status = "error"
            span.error_message = str(exc)
            raise
        finally:
            self._spans.append(span)

    @contextmanager
    def tool_span(
        self,
        trace_id: str,
        name: str,
        parent_span_id: str | None = None,
    ) -> Generator[Span, None, None]:
        """Create an agent.tool span for a tool execution step.

        Usage:
            with tracer.tool_span(trace_id, "policy.check_structural", parent_id) as span:
                result = policy_service.check_structural(role, action, trace_id)
                span.set_attribute("allowed", str(result.passed))
        """
        span = self._start_span(trace_id, name, "agent.tool", parent_span_id)
        try:
            yield span
            span.end_time = datetime.now(timezone.utc).isoformat()
            span.status = "ok"
        except Exception as exc:
            span.end_time = datetime.now(timezone.utc).isoformat()
            span.status = "error"
            span.error_message = str(exc)
            raise
        finally:
            self._spans.append(span)

    def set_attribute(self, span: Span, key: str, value: Any) -> None:
        """Set a sanitized attribute on a span."""
        sanitized = sanitize_pii(str(value)) if isinstance(value, str) else value
        span.attributes[key] = sanitized

    def get_spans(self, trace_id: str | None = None) -> list[Span]:
        """Return all spans, optionally filtered by trace_id."""
        if trace_id is None:
            return self._spans
        return [s for s in self._spans if s.trace_id == trace_id]

    def export(self) -> list[dict[str, Any]]:
        """Export all spans as dicts (for OpenTelemetry exporter or BigQuery)."""
        return [s.to_dict() for s in self._spans]

    def register_exporter(self, exporter: Any) -> None:
        """Register a callable exporter (e.g. OTLP exporter)."""
        self._exporters.append(exporter)

    def flush(self) -> None:
        """Push all spans to registered exporters and clear the buffer."""
        for span in self._spans:
            for exporter in self._exporters:
                try:
                    exporter(span.to_dict())
                except Exception as exc:
                    logger.warning("Span export failed: %s", exc)
        self._spans.clear()

    def _start_span(
        self,
        trace_id: str,
        name: str,
        span_type: str,
        parent_span_id: str | None,
    ) -> Span:
        return Span(
            span_id=f"sp-{uuid.uuid4().hex[:12]}",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            name=name,
            span_type=span_type,
            start_time=datetime.now(timezone.utc).isoformat(),
        )


# Module-level singleton for convenience.
_tracer: RenovAITracer | None = None


def get_tracer() -> RenovAITracer:
    """Return the module-level RenovAITracer singleton."""
    global _tracer
    if _tracer is None:
        _tracer = RenovAITracer()
    return _tracer
