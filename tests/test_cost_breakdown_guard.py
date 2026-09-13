"""
Task 5 — Pydantic guard for the cascading chain cost breakdown.

Verifies that a pre-1970 + flooring scope correctly applies the
"ajto_padlo_lanc" chain, and that tampering with the trigger condition (an
empty chain_ids_applied for a scope that should trigger a known CHAIN_RULES
entry) is caught by a loud ValidationError instead of silently returning 0.
"""

import pytest
from pydantic import ValidationError

from renovai.predictor.structural_cost import (
    CostBreakdownOutput,
    build_chain_breakdown,
)


def test_pre_1970_flooring_scope_applies_chain(tmp_path):
    """Happy path: a pre-1970 building with flooring in scope must apply the
    known chain and report a positive chain cost."""
    out = build_chain_breakdown(
        scope={"flooring": True},
        building_era="1960",
        floor_number=1,
        gas_heating=False,
        area_sqm=55.0,
    )
    assert "ajto_padlo_lanc" in out.chain_ids_applied
    assert out.chain_cost_huf > 0


def test_pre_1970_windows_doors_scope_applies_chain():
    """windows_doors alone also triggers the chain per CHAIN_RULES."""
    out = build_chain_breakdown(
        scope={"windows_doors": True},
        building_era="1955",
        area_sqm=40.0,
    )
    assert "ajto_padlo_lanc" in out.chain_ids_applied
    assert out.chain_cost_huf > 0


def test_modern_no_floor_work_no_chain():
    """A modern building without floor work must not trigger the chain and
    the output is a clean zero-cost breakdown."""
    out = build_chain_breakdown(
        scope={"plumbing": True},
        building_era="2005",
        area_sqm=55.0,
    )
    assert out.chain_ids_applied == []
    assert out.chain_cost_huf == 0


def test_validator_catches_omitted_chain():
    """Tamper test: scope would trigger ajto_padlo_lanc but chain_ids_applied
    is empty (as if the chain application were bypassed) -> ValidationError."""
    with pytest.raises(ValidationError, match="ajto_padlo_lanc"):
        CostBreakdownOutput(
            chain_ids_applied=[],
            chain_cost_huf=0,
            building_era="1960",
            scope={"flooring": True},
        )


def test_validator_passes_when_chain_present():
    """The same scope with the chain correctly listed must build cleanly."""
    out = CostBreakdownOutput(
        chain_ids_applied=["ajto_padlo_lanc"],
        chain_cost_huf=2_500_000,
        building_era="1960",
        scope={"flooring": True},
    )
    assert out.chain_ids_applied == ["ajto_padlo_lanc"]
    assert out.chain_cost_huf == 2_500_000
