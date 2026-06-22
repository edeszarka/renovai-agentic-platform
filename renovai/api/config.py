from pydantic_settings import BaseSettings, SettingsConfigDict
from enum import Enum
from typing import Optional

class LLMBackend(str, Enum):
    GEMINI = "gemini"
    OLLAMA = "ollama"

class AppConfig(BaseSettings):
    llm_backend: LLMBackend = LLMBackend.GEMINI

    # Gemini (free tier)
    google_api_key: str = ""
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_fast_model: str = "gemini-2.5-flash-lite"   # for Text-to-SQL
    gemini_embed_model: str = "models/gemini-embedding-2"

    # Ollama (local)
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "jobautomation/OpenEuroLLM-Hungarian"
    ollama_embed_model: str = "nomic-embed-text"

    # Infrastructure
    hf_token: str = ""
    chroma_dir: str = "data/chroma_db"
    models_dir: str = "data/models"
    quotes_json_dir: str = "data/processed/quotes_json"
    inflation_materials_csv: str = "data/raw/inflation/materials_cpi.csv"
    inflation_labor_csv: str = "data/raw/inflation/labor_cpi.csv"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
