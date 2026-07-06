from pydantic_settings import BaseSettings, SettingsConfigDict
from enum import Enum
from typing import Optional

class LLMBackend(str, Enum):
    GEMINI = "gemini"
    OLLAMA = "ollama"

class AppConfig(BaseSettings):
    llm_backend: LLMBackend = LLMBackend.GEMINI

    # --- Gemini (free tier, for RAG + advisor) ---
    google_api_key: str = ""
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_embed_model: str = "models/gemini-embedding-001"

    # --- Groq (free forever, for Text-to-SQL only) ---
    groq_api_key: str = ""
    groq_sql_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # --- DeepSeek (paid fallback, disabled by default) ---
    deepseek_api_key: str = ""
    deepseek_sql_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # --- Routing ---
    sql_provider: str = "groq"  # "groq" | "gemini" | "deepseek"

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
