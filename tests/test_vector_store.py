import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from renovai.rag.chunker import parse_front_matter, chunk_markdown_file, Chunk
from renovai.rag.embedder import EmbedderConfig, embed_chunks, embed_query
from renovai.rag.vector_store import VectorStoreConfig, RenovAIVectorStore

def test_yaml_front_matter_parsing():
    content = """---
source: Árajánlat_test.xlsx
address: Budapest, Fő utca 1
district: 1
grand_total_huf: 12500000
topics: [bontás, villany, ár]
---
# Quote Title
## Főbb munkák
Tégla bontás és sitt szállítás."""
    
    meta, body = parse_front_matter(content)
    assert meta["source"] == "Árajánlat_test.xlsx"
    assert meta["address"] == "Budapest, Fő utca 1"
    assert meta["district"] == 1
    assert meta["grand_total_huf"] == 12500000
    assert meta["topics"] == ["bontás", "villany", "ár"]
    assert "Főbb munkák" in body

def test_chunk_markdown_file_quote(tmp_path):
    md_content = """---
source: Árajánlat_test.xlsx
address: Budapest, Fő utca 1
district: 12
grand_total_huf: 5000000
---
# Árajánlat
## Főbb munkák
Tégla fal bontás 20nm.
Sitt elszállítás.
## Részletes megjegyzések
Minden sitt a konténerbe megy."""
    
    # Needs to be in a directory containing "quotes_md" in path to trigger quote behavior
    quotes_dir = tmp_path / "quotes_md"
    quotes_dir.mkdir()
    md_file = quotes_dir / "test_quote.md"
    md_file.write_text(md_content, encoding="utf-8")
    
    chunks = chunk_markdown_file(md_file, max_tokens=100)
    assert len(chunks) == 2
    
    # Check that Helyszín and Összesen are prepended
    assert "Helyszín: Budapest, Fő utca 1 | Összesen: 5 000 000 Ft" in chunks[0].content
    assert "Tégla fal bontás" in chunks[0].content
    
    # Metadata check
    assert chunks[0].metadata["source"] == "Árajánlat_test.xlsx"
    assert chunks[0].metadata["district"] == 12
    assert chunks[0].metadata["source_type"] == "quote"
    assert chunks[0].metadata["section"] == "Főbb munkák"

def test_chunk_splitting_exceeding_tokens(tmp_path):
    # Long text to force sentence splitting
    md_content = """---
source: long.xlsx
address: Test
grand_total_huf: 1000
---
## Hosszú
Valami hosszú mondat ami sok tokenből áll. És még egy másik hosszú mondat ami szintén sok token. És egy harmadik mondat."""
    
    quotes_dir = tmp_path / "quotes_md"
    quotes_dir.mkdir()
    md_file = quotes_dir / "long.md"
    md_file.write_text(md_content, encoding="utf-8")
    
    # Force splitting by passing a very small max_tokens limit
    chunks = chunk_markdown_file(md_file, max_tokens=40)
    assert len(chunks) > 1

def test_embed_chunks_mocked():
    chunks = [
        Chunk(
            chunk_id="test_id_1",
            source_file="test.md",
            source_type="quote",
            section="Sec1",
            content="Sample text",
            metadata={"address": "Budapest"}
        )
    ]
    config = EmbedderConfig(api_key="mock_key")
    
    with patch("renovai.rag.embedder.genai.Client") as mock_client_class:
        mock_client = mock_client_class.return_value
        # Mock response object with .embeddings[0].values
        mock_embedding = MagicMock()
        mock_embedding.values = [0.1, 0.2, 0.3]
        mock_response = MagicMock()
        mock_response.embeddings = [mock_embedding]
        mock_client.models.embed_content.return_value = mock_response
        
        embedded = embed_chunks(chunks, config)
        assert len(embedded) == 1
        assert embedded[0][1] == [0.1, 0.2, 0.3]
        assert embedded[0][0].chunk_id == "test_id_1"

def test_vector_store_config():
    vs_config = VectorStoreConfig()
    assert vs_config.persist_dir == "data/chroma_db"
    assert vs_config.collection_name == "renovation_quotes"

@patch("chromadb.PersistentClient")
def test_vector_store_build_and_query(mock_chroma_client):
    # Set up mocks for client and collections
    mock_client = MagicMock()
    mock_chroma_client.return_value = mock_client
    
    mock_collection = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_collection
    
    vs_config = VectorStoreConfig(persist_dir="mock_db")
    store = RenovAIVectorStore(vs_config)
    
    chunks = [
        Chunk(
            chunk_id="quote_chunk_1",
            source_file="quote1.md",
            source_type="quote",
            section="Főbb munkák",
            content="Quote content",
            metadata={"address": "Fő u.", "topics": ["bontás"]}
        )
    ]
    embeddings = [[0.1] * 768]
    
    # 1. Test build (upserts to single collection)
    store.build(chunks, embeddings)
    mock_collection.upsert.assert_called_once()
    
    # 2. Test query
    mock_collection.query.return_value = {
        "ids": [["quote_chunk_1"]],
        "documents": [["Quote content"]],
        "metadatas": [[{"source_file": "quote1.md", "section": "Főbb munkák", "topics": "bontás"}]],
        "distances": [[0.15]]
    }
    
    query_results = store.query("Sample query", [0.15] * 768, n_results=5)
    assert len(query_results) == 1
    assert query_results[0][0].chunk_id == "quote_chunk_1"
    assert query_results[0][1] == 0.15

