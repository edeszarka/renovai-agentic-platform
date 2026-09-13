import logging
import re
from typing import Any, Dict, List

import google.generativeai as genai
from openai import OpenAI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from renovai.api.config import AppConfig

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
You are a SQL expert managing a database of Hungarian renovation
quotes (felújítási árajánlatok). The user may ask questions in
Hungarian or English. You must return ONLY a valid SQLite SELECT
statement, nothing else — no explanation, no preamble, no markdown
fences.

Rules:
- Return SELECT only. Never INSERT, UPDATE, DELETE, DROP.
- Always alias columns with descriptive names.
- For monetary amounts, divide by 1000000 and round to 1 decimal
  and alias as e.g. atlag_millio_huf or avg_million_huf
  depending on the question language.
- Always add LIMIT 100 unless the question explicitly asks for all.
- Use JOINs instead of subqueries where possible.

Schema:
{schema}

{language_instruction}
"""


class TextToSQLEngine:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.provider = cfg.sql_provider

        if self.provider in ("groq", "deepseek"):
            api_key = (
                cfg.groq_api_key if self.provider == "groq" else cfg.deepseek_api_key
            )
            base_url = (
                cfg.groq_base_url if self.provider == "groq" else cfg.deepseek_base_url
            )
            self.model = (
                cfg.groq_sql_model
                if self.provider == "groq"
                else cfg.deepseek_sql_model
            )
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
            )

        elif self.provider == "gemini":
            genai.configure(api_key=cfg.google_api_key)
            self.gemini = genai.GenerativeModel(cfg.gemini_chat_model)
            self.model = cfg.gemini_chat_model

    def _detect_language(self, text: str) -> str:
        HU_MARKERS = [
            "á",
            "é",
            "í",
            "ó",
            "ö",
            "ő",
            "ú",
            "ü",
            "ű",
            "mennyi",
            "melyik",
            "hány",
            "kerület",
            "felújítás",
            "átlag",
            "összesen",
            "legdrágább",
            "legolcsóbb",
            "milyen",
            "mikor",
            "hogyan",
        ]
        text_lower = text.lower()
        hu_score = sum(1 for marker in HU_MARKERS if marker in text_lower)
        return "hu" if hu_score >= 1 else "en"

    async def generate_sql(self, question: str) -> str:
        lang = self._detect_language(question)
        system = TEXT_TO_SQL_SYSTEM_PROMPT.format(
            schema=SCHEMA_DESCRIPTION,
            language_instruction=(
                "Respond in Hungarian (magyarul válaszolj)."
                if lang == "hu"
                else "Respond in English."
            ),
        )

        if self.provider in ("groq", "deepseek"):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                max_tokens=400,
                temperature=0.0,
            )
            sql = response.choices[0].message.content.strip()

        elif self.provider == "gemini":
            prompt = f"{system}\n\nKérdés / Question: {question}"
            response = self.gemini.generate_content(prompt)
            sql = response.text.strip()

        sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\s*```$", "", sql)
        return sql.strip()

    async def execute_query(
        self, sql: str, session: AsyncSession
    ) -> List[Dict[str, Any]]:
        if not sql.strip().upper().startswith("SELECT"):
            raise ValueError("Only SELECT statements are allowed for safety.")
        res = await session.execute(text(sql))
        keys = res.keys()
        return [dict(zip(keys, row)) for row in res.fetchall()]

    async def query(self, question: str, session: AsyncSession) -> Dict[str, Any]:
        sql = await self.generate_sql(question)
        try:
            rows = await self.execute_query(sql, session)
            return {
                "question": question,
                "sql": sql,
                "rows": rows,
                "row_count": len(rows),
            }
        except Exception as e:
            return {
                "question": question,
                "sql": sql,
                "error": str(e),
            }
