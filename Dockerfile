FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first (leverage Docker layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy project code
COPY renovai/ ./renovai/
COPY mcp_server/ ./mcp_server/
COPY scripts/ ./scripts/

# Copy baked-in data (model, vector store, quotes, inflation CSVs)
COPY data/models/ ./data/models/
COPY data/chroma_db/ ./data/chroma_db/
COPY data/renovai.db ./data/renovai.db
COPY data/raw/inflation/ ./data/raw/inflation/
COPY data/processed/quotes_json/ ./data/processed/quotes_json/
COPY data/processed/quotes_md/ ./data/processed/quotes_md/

# Cloud Run env var signals the server to use SSE transport
ENV CLOUD_RUN=true
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "-m", "mcp_server.server"]
