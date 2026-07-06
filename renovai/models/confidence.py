"""
Confidence model for RenovAI's Bayesian confidence signaling.

Each estimate or advisory response carries a ConfidenceModel object that
quantifies how certain the system is about the output. The confidence score
is derived from the number of supporting sources found in the corpus, the
inflation-adjustment recency, and the similarity of the closest historical
match.

Usage:
    from renovai.models.confidence import ConfidenceModel

    confidence = ConfidenceModel(
        score=0.85,
        reasoning="3 similar quotes found in the corpus, all within 1 year",
        sources_count=3,
    )
"""

from pydantic import BaseModel, Field


class ConfidenceModel(BaseModel):
    """
    Bayesian confidence signal for a single estimate or advisory response.

    Attributes:
        score: Float between 0.0 and 1.0 representing the system's confidence.
            0.0 = no supporting evidence, 1.0 = fully corroborated by multiple
            high-similarity sources with recent inflation data.
        reasoning: Human-readable explanation of how the confidence score was
            derived. Includes the number of similar quotes found, their date
            range, and any data limitations that affected the score.
        sources_count: Integer count of distinct source quotes or documents
            that support this output. A value of 0 means no supporting sources
            were found in the corpus.
    """

    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence score between 0.0 and 1.0. Derived from the number "
            "and similarity of supporting sources and inflation recency."
        ),
    )
    reasoning: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable explanation of how score was computed. "
            "Examples: '3 similar quotes in corpus, all <6 months old' or "
            "'No similar quotes found, estimate is corpus-wide average only'."
        ),
    )
    sources_count: int = Field(
        ...,
        ge=0,
        description="Number of distinct source quotes or chunks supporting the output.",
    )

    def is_reliable(self) -> bool:
        """Return True if confidence indicates a reliable estimate (>= 0.7)."""
        return self.score >= 0.7

    def is_speculative(self) -> bool:
        """Return True if confidence indicates a speculative estimate (< 0.4)."""
        return self.score < 0.4

    def describe(self) -> str:
        """Return a short Hungarian-language confidence label."""
        if self.is_reliable():
            return f"Megbízható becslés ({self.sources_count} forrás alapján)"
        if self.is_speculative():
            return (
                f"Tájékoztató jellegű becslés (csak {self.sources_count} "
                f"forrás áll rendelkezésre)"
            )
        return (
            f"Közepes biztonságú becslés ({self.sources_count} forrás alapján)"
        )
