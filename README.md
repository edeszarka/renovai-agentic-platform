# RenovAI — Renoválási Tanácsadó és Költségbecslő

A proof-of-concept tool that helps Hungarian apartment buyers who are considering purchasing flats that need renovation. It combines machine learning for cost estimation with a RAG-based advisory system using Gemini.

## Core Modules
1.  **Pre-purchase Advisor (RAG-powered):** Generates questions for the seller, an inspection checklist, and identifies red flags.
2.  **Renovation Cost Estimator (ML):** Predicts renovation costs based on apartment parameters (district, area, rooms, etc.), adjusted for current inflation.
3.  **FastAPI REST API:** Exposes all functionality via a modern web API.
4.  **Inflation Calculator:** Adjusts historical quote prices to current values using KSH CPI data.

## Tech Stack
- **Backend:** Python 3.11+
- **API:** FastAPI
- **LLM/Embeddings:** Google Gemini (Generative AI)
- **Vector DB:** ChromaDB
- **ML:** Scikit-learn, Joblib
- **Data:** Pandas, Openpyxl
- **Containerization:** Docker & Docker Compose

## Installation & Setup

1.  **Clone the repository:**
    ```bash
    git clone <your-new-repo-url>
    cd renovai-capstone
    ```

2.  **Install dependencies:**
    Using `uv` (recommended):
    ```bash
    uv sync
    ```
    Or using `pip`:
    ```bash
    pip install .
    ```

3.  **Configure environment:**
    Copy `.env.example` to `.env` and fill in your API keys:
    ```bash
    cp .env.example .env
    ```

4.  **Run Ingestion & Training (Initial Setup):**
    ```bash
    python scripts/run_ingestion.py --adjust-inflation
    python scripts/build_vector_store.py
    python scripts/train_model.py
    ```

## Running the API

### Local Development
```bash
python scripts/run_api.py --reload
```
The API documentation will be available at [http://localhost:8000/docs](http://localhost:8000/docs).

### Using Docker
```bash
docker-compose up --build
```

## API Endpoints

-   `GET /health`: Health check and system statistics.
-   `POST /estimate`: Predict renovation cost for an apartment.
-   `POST /advise`: Generate a comprehensive pre-purchase advisory report.
-   `POST /query`: Ask free-text questions about renovation (Hungarian).
-   `GET /query/stream`: Stream RAG answers token-by-token.
-   `POST /ingest/quotes`: Trigger background ingestion of new quotes.

## License
MIT
