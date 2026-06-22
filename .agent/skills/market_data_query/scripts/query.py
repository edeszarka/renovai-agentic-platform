"""
Thin wrapper around renovai.db.text_to_sql.TextToSQLEngine for the market_data_query skill.

Called by the orchestrator when the buyer asks aggregate market questions.
Reuses the same underlying implementation as mcp_server/server.py: query_renovation_market.
"""

import sys
import json
import os
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from dotenv import load_dotenv

load_dotenv()

from renovai.db.text_to_sql import TextToSQLEngine
from renovai.db.session import get_engine, get_session_maker


async def run(question_hu: str) -> dict:
    api_key = os.getenv("GOOGLE_API_KEY", "")
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/renovai.db")

    engine = get_engine(database_url)
    session_maker = get_session_maker(engine)
    t2s = TextToSQLEngine(api_key=api_key)

    async with session_maker() as session:
        result = await t2s.query(question_hu, session)

    if "error" in result:
        return {"question": question_hu, "sql": result.get("sql"), "rows": [], "row_count": 0, "error": result["error"]}

    return {
        "question": question_hu,
        "sql": result["sql"],
        "rows": result["rows"],
        "row_count": result["row_count"],
    }


if __name__ == "__main__":
    data = json.loads(sys.stdin.read())
    result = asyncio.run(run(data["question_hu"]))
    print(json.dumps(result, ensure_ascii=False, default=str))
