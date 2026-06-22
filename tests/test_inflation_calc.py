import pytest
import logging
from datetime import date, datetime
from typing import List
from renovai.ingestion.inflation_models import CPIRecord, PriceIndex
from renovai.ingestion.models import RenovationQuote, QuoteMetadata, LineItem
from renovai.ingestion.inflation_calc import get_factor, adjust_quote, build_aggregated_index

@pytest.fixture
def sample_price_index() -> PriceIndex:
    # 2024 Q1 is base (1.000)
    materials = [
        CPIRecord(year=2023, quarter=1, index_value=0.900),
        CPIRecord(year=2023, quarter=2, index_value=0.920),
        CPIRecord(year=2023, quarter=3, index_value=0.940),
        CPIRecord(year=2023, quarter=4, index_value=0.960),
        CPIRecord(year=2024, quarter=1, index_value=1.000),
        CPIRecord(year=2024, quarter=2, index_value=1.020),
        CPIRecord(year=2024, quarter=3, index_value=1.040),
        CPIRecord(year=2024, quarter=4, index_value=1.060),
        CPIRecord(year=2025, quarter=1, index_value=1.100),
    ]
    labor = [
        CPIRecord(year=2023, quarter=1, index_value=0.880),
        CPIRecord(year=2023, quarter=2, index_value=0.900),
        CPIRecord(year=2023, quarter=3, index_value=0.920),
        CPIRecord(year=2023, quarter=4, index_value=0.950),
        CPIRecord(year=2024, quarter=1, index_value=1.000),
        CPIRecord(year=2024, quarter=2, index_value=1.050),
        CPIRecord(year=2024, quarter=3, index_value=1.100),
        CPIRecord(year=2024, quarter=4, index_value=1.150),
        CPIRecord(year=2025, quarter=1, index_value=1.200),
    ]
    return PriceIndex(
        materials=materials,
        labor=labor,
        generated_at=datetime.now()
    )

def test_get_factor_exact_midquarters(sample_price_index):
    from_date = date(2024, 2, 15)
    to_date = date(2024, 5, 16)
    
    factor_mat = get_factor(sample_price_index, "materials", from_date, to_date)
    factor_lab = get_factor(sample_price_index, "labor", from_date, to_date)
    
    # Assert close values
    assert pytest.approx(factor_mat, 0.01) == 1.020
    assert pytest.approx(factor_lab, 0.01) == 1.050

def test_interpolation_mid_quarter_date(sample_price_index):
    from_date = date(2024, 2, 15)
    to_date = date(2024, 4, 1)
    
    factor_mat = get_factor(sample_price_index, "materials", from_date, to_date)
    # Value should be between 1.000 and 1.020, close to 1.010
    assert 1.005 < factor_mat < 1.015

def test_graceful_handling_earliest_date(sample_price_index, caplog):
    # If date is before earliest available data, raise warning and use earliest value
    from_date = date(2022, 1, 1)
    to_date = date(2024, 2, 15)
    
    with caplog.at_level(logging.WARNING):
        factor_mat = get_factor(sample_price_index, "materials", from_date, to_date)
        
    # Earliest index value is 0.900.
    # We dynamically calculate what to_date's index value is:
    expected_to_val = get_factor(sample_price_index, "materials", date(2023, 2, 15), to_date) * 0.900
    expected_factor = expected_to_val / 0.900
    
    assert pytest.approx(factor_mat) == expected_factor
    assert any("before earliest materials record" in record.message for record in caplog.records)

def test_adjust_quote(sample_price_index):
    # Quote from 2024 Q1 (Mid-Q1 is 2024-02-15)
    metadata = QuoteMetadata(
        file_name="test.xlsx",
        address_raw="1125 Test utca 1",
        district=12,
        quote_date=date(2024, 2, 15),
        total_labor=200000,
        total_material=300000,
        grand_total=500000
    )
    
    line_items = [
        # 1. Labor only
        LineItem(name="Labor item", labor_cost=100000, material_cost=None, total_cost=100000, section="Main", version=1),
        # 2. Materials only
        LineItem(name="Material item", labor_cost=None, material_cost=200000, total_cost=200000, section="Main", version=1),
        # 3. Mixed item
        LineItem(name="Mixed item", labor_cost=100000, material_cost=100000, total_cost=200000, section="Main", version=1)
    ]
    
    # Options (version = 2)
    alternatives = [
        LineItem(name="Option labor", labor_cost=50000, material_cost=None, total_cost=50000, section="Alternatives", version=2)
    ]
    
    quote = RenovationQuote(
        metadata=metadata,
        quote_style="standard_5col",
        line_items=line_items,
        alternatives=alternatives,
        not_included=[],
        buyer_purchases=[],
        general_notes=[]
    )
    
    # Target date: Mid-Q3 2024 (approx 2024-08-15)
    target_date = date(2024, 8, 15)
    
    factor_labor = get_factor(sample_price_index, "labor", quote.metadata.quote_date, target_date)
    factor_materials = get_factor(sample_price_index, "materials", quote.metadata.quote_date, target_date)
    
    adjusted = adjust_quote(quote, sample_price_index, target_date)
    
    # Verify items:
    # 1. Labor item: 100000 * factor_labor
    expected_labor_1 = int(round(100000 * factor_labor))
    assert adjusted.line_items_adjusted[0].labor_cost_adjusted == expected_labor_1
    assert adjusted.line_items_adjusted[0].total_cost_adjusted == expected_labor_1
    
    # 2. Material item: 200000 * factor_materials
    expected_material_2 = int(round(200000 * factor_materials))
    assert adjusted.line_items_adjusted[1].material_cost_adjusted == expected_material_2
    assert adjusted.line_items_adjusted[1].total_cost_adjusted == expected_material_2
    
    # 3. Mixed item
    expected_labor_3 = int(round(100000 * factor_labor))
    expected_material_3 = int(round(100000 * factor_materials))
    expected_total_3 = expected_labor_3 + expected_material_3
    assert adjusted.line_items_adjusted[2].labor_cost_adjusted == expected_labor_3
    assert adjusted.line_items_adjusted[2].material_cost_adjusted == expected_material_3
    assert adjusted.line_items_adjusted[2].total_cost_adjusted == expected_total_3
    
    # 4. Version 2 option
    expected_labor_opt = int(round(50000 * factor_labor))
    assert adjusted.line_items_adjusted[3].total_cost_adjusted == expected_labor_opt
    
    # Verify grand totals:
    assert adjusted.grand_total_original == 500000
    expected_grand_total = expected_labor_1 + expected_material_2 + expected_total_3
    assert adjusted.grand_total_adjusted == expected_grand_total
    
    # Delta %: (expected_grand_total - 500000) / 500000 * 100
    expected_delta_pct = round((expected_grand_total - 500000) / 500000 * 100, 2)
    assert pytest.approx(adjusted.inflation_delta_pct) == expected_delta_pct
