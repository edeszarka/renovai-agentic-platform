import pytest
import uuid
import asyncio
from datetime import date, datetime
from unittest.mock import MagicMock, patch, AsyncMock
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from renovai.db.models import Base, Quote, LineItemORM, WorkCategory, CPIRecord
from renovai.db.repository import QuoteRepository, CPIRepository
from renovai.db.session import init_db
from renovai.api.config import AppConfig
from renovai.db.text_to_sql import TextToSQLEngine
from renovai.ingestion.models import RenovationQuote, QuoteMetadata, LineItem

@pytest.fixture
async def test_session():
    # Use in-memory SQLite for tests
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    # Seed work categories manually for test
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        session.add(WorkCategory(key="bontás", label_hu="Bontás", label_en="Demolition", cost_type="labor"))
        session.add(WorkCategory(key="víz_fűtés", label_hu="Víz és fűtés", label_en="Plumbing", cost_type="mixed"))
        await session.commit()
        
    async with async_session() as session:
        yield session
        
    await engine.dispose()

@pytest.mark.asyncio
async def test_upsert_quote_full(test_session: AsyncSession):
    repo = QuoteRepository()
    
    quote_pydantic = RenovationQuote(
        metadata=QuoteMetadata(
            file_name="test_quote.xlsx",
            address_raw="Budapest, 12. kerület",
            district=12,
            quote_date=date(2024, 1, 1),
            total_labor=1000000,
            total_material=500000,
            grand_total=1500000,
            timeline_weeks_min=4,
            timeline_weeks_max=6
        ),
        quote_style="standard_5col",
        line_items=[
            LineItem(name="Bontás konyhában", labor_cost=100000, total_cost=100000, section="Konyha", version=1, notes="Kohósalak lehet")
        ],
        alternatives=[],
        not_included=["Festés"],
        buyer_purchases=["Csempe"],
        general_notes=["Sürgős"]
    )
    
    db_quote = await repo.upsert_quote(test_session, quote_pydantic)
    await test_session.commit()
    
    assert db_quote.file_name == "test_quote.xlsx"
    assert db_quote.has_slag_complication is True
    assert len(db_quote.line_items) == 1
    assert db_quote.line_items[0].category_key == "bontás"
    assert len(db_quote.not_included) == 1
    
    # Test Update
    quote_pydantic.metadata.grand_total = 1600000
    db_quote_2 = await repo.upsert_quote(test_session, quote_pydantic)
    await test_session.commit()
    
    assert db_quote_2.grand_total_huf == 1600000
    assert db_quote_2.id == db_quote.id # Same ID

@pytest.mark.asyncio
async def test_get_cost_stats(test_session: AsyncSession):
    repo = QuoteRepository()
    
    q1 = RenovationQuote(
        metadata=QuoteMetadata(file_name="q1.xlsx", address_raw="D12", district=12, total_labor=60, total_material=40, grand_total=100),
        quote_style="standard_5col",
        line_items=[], alternatives=[], not_included=[], buyer_purchases=[], general_notes=[]
    )
    q2 = RenovationQuote(
        metadata=QuoteMetadata(file_name="q2.xlsx", address_raw="D12", district=12, total_labor=80, total_material=20, grand_total=100),
        quote_style="standard_5col",
        line_items=[], alternatives=[], not_included=[], buyer_purchases=[], general_notes=[]
    )
    q3 = RenovationQuote(
        metadata=QuoteMetadata(file_name="q3.xlsx", address_raw="D13", district=13, total_labor=50, total_material=50, grand_total=200),
        quote_style="standard_5col",
        line_items=[], alternatives=[], not_included=[], buyer_purchases=[], general_notes=[]
    )
    
    await repo.upsert_quote(test_session, q1)
    await repo.upsert_quote(test_session, q2)
    await repo.upsert_quote(test_session, q3)
    await test_session.commit()
    
    stats = await repo.get_cost_stats(test_session, district=12)
    assert stats["count"] == 2
    assert stats["avg_total"] == 100
    assert stats["avg_labor_share"] == 0.7 # (0.6 + 0.8) / 2

@pytest.mark.asyncio
async def test_text_to_sql_safety():
    cfg = MagicMock()
    cfg.sql_provider = "groq"
    cfg.groq_api_key = "fake"
    cfg.groq_base_url = "https://api.groq.com/openai/v1"
    cfg.groq_sql_model = "llama-3.3-70b-versatile"
    engine = TextToSQLEngine(cfg)
    
    with pytest.raises(ValueError, match="Only SELECT statements are allowed"):
        await engine.execute_query("DELETE FROM quotes;", None)

@patch("renovai.db.text_to_sql.OpenAI")
@pytest.mark.asyncio
async def test_text_to_sql_execution(mock_openai_class, test_session: AsyncSession):
    # Mock OpenAI chat completion to return SQL
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_choice = MagicMock()
    mock_choice.message.content = "```sql\nSELECT COUNT(*) AS total FROM quotes\n```"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create = MagicMock(return_value=mock_response)
    
    cfg = MagicMock()
    cfg.sql_provider = "groq"
    cfg.groq_api_key = "fake"
    cfg.groq_base_url = "https://api.groq.com/openai/v1"
    cfg.groq_sql_model = "llama-3.3-70b-versatile"
    engine = TextToSQLEngine(cfg)
    
    repo = QuoteRepository()
    q = RenovationQuote(
        metadata=QuoteMetadata(file_name="test.xlsx", address_raw="D", total_labor=1, total_material=1, grand_total=2),
        quote_style="standard_5col",
        line_items=[], alternatives=[], not_included=[], buyer_purchases=[], general_notes=[]
    )
    await repo.upsert_quote(test_session, q)
    await test_session.commit()
    
    result = await engine.query("Hány árajánlat van?", test_session)
    assert result["row_count"] == 1
    assert result["rows"][0]["total"] == 1
    assert "SELECT COUNT(*)" in result["sql"]
