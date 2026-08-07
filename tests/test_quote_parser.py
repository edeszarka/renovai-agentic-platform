import pytest
from datetime import date
from pathlib import Path
from renovai.ingestion.quote_parser import parse_quote, infer_quote_year_from_path
from renovai.ingestion.number_parser import parse_huf
from renovai.ingestion.address_extractor import extract_address, postal_to_district

@pytest.mark.parametrize("value,expected", [
    ("963118", 963118),
    ("1,690,000", 1690000),
    ("963118 Ft", 963118),
    ("nettó 2835000 Ft", 2835000),
    ("3798118 Ft.", 3798118),
    (1000, 1000),
    (1000.5, 1000),
    (None, None),
    ("", None),
])
def test_parse_huf_formats(value, expected):
    assert parse_huf(value) == expected

@pytest.mark.parametrize("postal,expected", [
    (1126, 12),
    (1081, 8),
    (1024, 2),
    (1011, 1),
    (2000, None),
])
def test_postal_to_district(postal, expected):
    assert postal_to_district(postal) == expected

def test_address_extractor_standard():
    filename = "Árajánlat_-_1126_Hollósy_Simon_utca_30__2__emelet"
    addr = extract_address(filename)
    assert addr["district"] == 12
    assert addr["postal_code"] == 1126
    assert addr["street"] == "Hollósy Simon utca"
    assert addr["house_number"] == "30"
    assert addr["floor"] == "2. emelet"

def test_address_extractor_no_address():
    filename = "Árajánlat_-_komplett_munkák"
    addr = extract_address(filename)
    assert addr["address_raw"] is None
    assert addr["district"] is None

# Note: The following tests require real XLSX files in tests/fixtures/
# They are marked as skip if files are not present.

FIXTURE_DIR = Path("tests/fixtures")

@pytest.mark.skipif(not (FIXTURE_DIR / "Árajánlat_-_1081_Népszínház_utca_29__1__emelet.xlsx").exists(), reason="Fixture missing")
def test_nepszinhaz():
    path = FIXTURE_DIR / "Árajánlat_-_1081_Népszínház_utca_29__1__emelet.xlsx"
    quote = parse_quote(path)
    assert quote.quote_style == "text_only"
    assert quote.metadata.district == 8
    assert quote.metadata.grand_total > 0
    assert quote.metadata.total_labor > 0
    assert quote.metadata.total_material > 0

@pytest.mark.skipif(not (FIXTURE_DIR / "Árajánlat_-_1126_Hollósy_Simon_utca_30__2__emelet.xlsx").exists(), reason="Fixture missing")
def test_hollosy():
    path = FIXTURE_DIR / "Árajánlat_-_1126_Hollósy_Simon_utca_30__2__emelet.xlsx"
    quote = parse_quote(path)
    assert quote.quote_style == "standard_5col"
    assert quote.metadata.district == 12
    assert any(li.name == "Bontás" for li in quote.line_items)

@pytest.mark.skipif(not (FIXTURE_DIR / "Árajánlat__2_.xlsx").exists(), reason="Fixture missing")
def test_fazisok():
    path = FIXTURE_DIR / "Árajánlat__2_.xlsx"
    quote = parse_quote(path)
    assert quote.quote_style == "multi_phase"
    phases = set(li.phase for li in quote.line_items)
    assert 1 in phases
    assert 2 in phases

@pytest.mark.skipif(not (FIXTURE_DIR / "Árajánlat_-_komplett_munkák.xlsx").exists(), reason="Fixture missing")
def test_komplett():
    path = FIXTURE_DIR / "Árajánlat_-_komplett_munkák.xlsx"
    quote = parse_quote(path)
    assert quote.quote_style == "scope_only"
    assert quote.metadata.address_raw is None


class TestInferQuoteYearFromPath:
    def test_valid_year_folder(self):
        p = Path("data/raw/quotes/2022/foo.xlsx")
        assert infer_quote_year_from_path(p) == 2022

    def test_non_numeric_folder_raises(self):
        p = Path("data/raw/quotes/not_a_year/foo.xlsx")
        with pytest.raises(ValueError, match="not a numeric year"):
            infer_quote_year_from_path(p)

    def test_year_out_of_range_raises(self):
        p = Path("data/raw/quotes/2010/foo.xlsx")
        with pytest.raises(ValueError, match="outside the expected range"):
            infer_quote_year_from_path(p)

    def test_year_too_far_future_raises(self):
        p = Path("data/raw/quotes/2030/foo.xlsx")
        with pytest.raises(ValueError, match="outside the expected range"):
            infer_quote_year_from_path(p)

    def test_boundary_year_low(self):
        p = Path("data/raw/quotes/2015/foo.xlsx")
        assert infer_quote_year_from_path(p) == 2015

    def test_boundary_year_high(self):
        p = Path("data/raw/quotes/2026/foo.xlsx")
        assert infer_quote_year_from_path(p) == 2026


def test_parse_quote_uses_folder_year_not_mtime(tmp_path):
    """parse_quote() sets quote_date to Jan 1 of the parent folder year."""
    import openpyxl
    quotes_root = tmp_path / "2022"
    quotes_root.mkdir(parents=True)
    xlsx_path = quotes_root / "test_quote.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Munka megnevezése", "Munkadíj", "Anyag", "Összesen"])
    ws.append(["Bontás", 50000, None, 50000])
    wb.save(str(xlsx_path))

    quote = parse_quote(xlsx_path)
    assert quote.metadata.quote_date == date(2022, 1, 1)
