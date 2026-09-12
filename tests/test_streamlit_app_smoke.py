"""Smoke test for the Streamlit frontend.

Guards against the exact class of regression that previously shipped on this
file: a top-level, unconditional crash-on-load bug (undefined names in the
module-level bootstrap code) that no test exercised because nothing ever
imported or ran ``app/streamlit_app.py``.

The test loads the app with Streamlit's ``AppTest`` and asserts that it renders
without raising. Loading the page must not require live external dependencies:

* ``AppConfig`` only reads configuration (no network).
* ``load_price_index`` reads local CSV files and tolerates missing ones.
* ``get_engine`` / ``get_session_maker`` build a lazy SQLAlchemy engine and
  never open a connection at construction time.
* ``PolicyService`` / ``SkillRegistry`` are only constructed inside button
  handlers, which are not triggered by a bare ``.run()``.

To keep the test isolated we still point ``DATABASE_URL`` at a throwaway
SQLite path and use a placeholder API key, so the app never touches the real
application database or makes a live Gemini call even if construction paths
change in the future.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py"


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'smoke_test.db'}",
    )
    monkeypatch.setenv("GOOGLE_API_KEY", "smoke-test-placeholder")
    return tmp_path


def test_app_loads_without_exception(isolated_env):
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.run()

    assert len(at.exception) == 0
