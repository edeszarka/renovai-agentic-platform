import re
import logging
from google import genai
from google.genai import types
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

SCHEMA_DESCRIPTION = """
Database schema for Hungarian renovation quotes:

TABLE quotes: one row per renovation quote file.
  Columns: id, file_name, address_raw, district (int, Budapest district number),
  quote_date, total_labor_huf, total_material_huf, grand_total_huf,
  grand_total_adjusted_huf (inflation-adjusted to 2024 Q1), 
  timeline_weeks_min, timeline_weeks_max, has_slag_complication (bool),
  num_line_items, created_at.

TABLE line_items: one row per work item inside a quote.
  Columns: id, quote_id (FK), name_hu (Hungarian work name), category_key (FK),
  labor_cost_huf, material_cost_huf, total_cost_huf, notes_hu, section, 
  version (1=main scope, 2/3=alternatives), is_included.

TABLE work_categories: lookup table for work types.
  Columns: key, label_hu, label_en, cost_type (labor/material/mixed).
  Keys: bontás, víz_fűtés, villany, klíma, vakolás, burkolás, 
  glettelés_festés, parketta, egyéb_köműves, szállítás, egyéb.

TABLE not_included: items explicitly excluded from quotes.
  Columns: id, quote_id (FK), item_hu.

TABLE cpi_records: KSH inflation index time series.
  Columns: id, year, quarter, component (materials/labor), index_value.
  Base: 2024 Q1 = 1.000.

All monetary values are in Hungarian Forints (HUF), stored as integers.
All text fields are in Hungarian.
"""

TEXT_TO_SQL_SYSTEM_PROMPT = """
Te egy SQL szakértő vagy, aki a magyar felújítási árajánlatok adatbázisát 
kezeli. A felhasználó magyarul tesz fel kérdéseket, neked SQLite-kompatibilis 
SELECT lekérdezést kell visszaadnod.

Szabályok:
- Csak SELECT utasítást adj vissza, semmi mást.
- Ne adj magyarázatot, csak a SQL kódot.
- Mindig add meg az oszlopneveket AS aliasokkal, amelyek leíró magyar neveket 
  kapnak (pl. AVG(grand_total_huf) AS atlag_osszeg_huf).
- Ha az összeg millió forintban kérdezhető, oszd el 1000000.0-val és kerekítsd 
  1 tizedesre.
- Kerüld a subquery-ket, ha JOIN-nal is megoldható.
- Mindig adj hozzá LIMIT 100-at, hacsak a kérdés explicit "összes"-t nem kér.

Schema:
{schema}
"""

class TextToSQLEngine:
    def __init__(self, gemini_api_key: str, model: str = "gemini-1.5-flash"):
        self.client = genai.Client(api_key=gemini_api_key)
        self.model_name = model

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(
            f"Gemini API error in Text-to-SQL. Retrying in {retry_state.next_action.sleep}s... "
            f"Attempt {retry_state.attempt_number}/5"
        )
    )
    async def generate_sql(self, question_hu: str) -> str:
        """Translates a Hungarian question into an SQL SELECT statement."""
        config = types.GenerateContentConfig(
            system_instruction=TEXT_TO_SQL_SYSTEM_PROMPT.format(schema=SCHEMA_DESCRIPTION)
        )
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=question_hu,
            config=config
        )
        sql = response.text.strip()
        
        # Strip markdown fences
        sql = re.sub(r'```sql\s*', '', sql)
        sql = re.sub(r'```', '', sql)
        return sql.strip()

    async def execute_query(self, sql: str, session: AsyncSession) -> List[Dict[str, Any]]:
        """Executes the generated SQL safely."""
        # Basic safety check
        if not sql.strip().upper().startswith("SELECT"):
            raise ValueError("Only SELECT statements are allowed for safety.")
            
        res = await session.execute(text(sql))
        # Convert rows to dicts
        keys = res.keys()
        return [dict(zip(keys, row)) for row in res.fetchall()]

    async def query(self, question_hu: str, session: AsyncSession) -> Dict[str, Any]:
        """Full Text-to-SQL pipeline."""
        sql = await self.generate_sql(question_hu)
        try:
            rows = await self.execute_query(sql, session)
            return {
                "question": question_hu,
                "sql": sql,
                "rows": rows,
                "row_count": len(rows)
            }
        except Exception as e:
            return {
                "question": question_hu,
                "sql": sql,
                "error": str(e)
            }
