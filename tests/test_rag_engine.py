import pytest
from unittest.mock import MagicMock, patch
from renovai.rag.chunker import Chunk
from renovai.rag.retriever import RetrievalConfig, RetrievedContext, retrieve, assemble_context
from renovai.rag.gemini_client import GeminiConfig, call_gemini, RAGResponse
from renovai.rag.pipeline import RAGPipeline

@pytest.fixture
def mock_chunks():
    return [
        Chunk(
            chunk_id="chunk_1",
            source_file="file1.md",
            source_type="quote",
            section="Sec1",
            content="Content of chunk 1",
            metadata={"address": "Addr1"}
        ),
        Chunk(
            chunk_id="chunk_2",
            source_file="file2.md",
            source_type="transcript",
            section="Sec2",
            content="Content of chunk 2",
            metadata={"topics": ["topic1"]}
        ),
        Chunk(
            chunk_id="chunk_3",
            source_file="file3.md",
            source_type="quote",
            section="Sec3",
            content="Content of chunk 3 is very long for testing truncation logic. " * 10,
            metadata={"address": "Addr3"}
        )
    ]

def test_hybrid_retrieval_logic(mock_chunks):
    # Mock vector store and embedder
    mock_vs = MagicMock()
    mock_vs.query.return_value = [(mock_chunks[0], 0.2), (mock_chunks[1], 0.4)] # dist 0.2 -> score 0.8, dist 0.4 -> score 0.6
    mock_vs.get_all_chunks.return_value = mock_chunks
    
    mock_emb_config = MagicMock()
    ret_config = RetrievalConfig(n_semantic=2, n_keyword=2, min_score_threshold=0.1)
    
    with patch("renovai.rag.retriever.embed_query", return_value=[0.1]*768):
        # We want BM25 to find chunk_2 and chunk_3
        # BM25 uses tokenize_hu which is .lower().split()
        with patch("rank_bm25.BM25Okapi.get_scores") as mock_bm25:
            # chunk_1: "content of chunk 1"
            # chunk_2: "content of chunk 2"
            # chunk_3: "content of chunk 3..."
            # Let's say scores are: chunk_1=0.1, chunk_2=0.9, chunk_3=0.8
            mock_bm25.return_value = [0.1, 0.9, 0.8]
            
            res = retrieve("test query", mock_vs, mock_emb_config, ret_config)
            
            # Semantic: chunk_1 (0.8), chunk_2 (0.6)
            # Keyword (top 2): chunk_2 (1.0), chunk_3 (0.888...)
            # Both: chunk_2 gets +0.15 boost
            
            chunk_ids = [c.chunk_id for c in res.chunks]
            assert "chunk_2" in chunk_ids
            assert "chunk_1" in chunk_ids
            assert "chunk_3" in chunk_ids
            
            # chunk_2 should be first due to boost (0.6 semantic or 1.0 kw -> 1.0 + 0.15 = 1.15)
            assert res.chunks[0].chunk_id == "chunk_2"

def test_assemble_context_truncation(mock_chunks):
    retrieved = RetrievedContext(
        chunks=mock_chunks,
        query="query",
        retrieval_metadata={}
    )
    
    # Each chunk formatted is roughly 50-100 tokens
    # Let's force truncation by setting a small max_tokens
    # Chunk 3 is long.
    
    # 1. No truncation
    ctx = assemble_context(retrieved, max_context_tokens=10000)
    assert "[FORRÁS 1: file1.md | Sec1]" in ctx
    assert "[FORRÁS 2: file2.md | Sec2]" in ctx
    assert "[FORRÁS 3: file3.md | Sec3]" in ctx
    
    # 2. Truncate to first two chunks
    # We'll mock tiktoken to return specific lengths
    with patch("tiktoken.get_encoding") as mock_enc:
        mock_e = MagicMock()
        mock_enc.return_value = mock_e
        # Return 100 for each chunk
        mock_e.encode.side_effect = lambda x: [0] * 100
        
        ctx_small = assemble_context(retrieved, max_context_tokens=250)
        assert "[FORRÁS 1" in ctx_small
        assert "[FORRÁS 2" in ctx_small
        assert "[FORRÁS 3" not in ctx_small

def test_citation_and_confidence():
    mock_answer = "A fürdőszoba felújítás drága [FORRÁS 1]. De megéri [FORRÁS 2] és [FORRÁS 4]."
    
    # We need to test the logic inside pipeline since call_gemini doesn't map sources itself anymore
    # Actually call_gemini returns sources_cited=[]
    
    ret_metadata = {"sources": ["f1.md", "f2.md", "f3.md", "f4.md"]}
    
    with patch("renovai.rag.gemini_client.genai.Client") as mock_client_class:
        mock_client = mock_client_class.return_value
        mock_resp = MagicMock()
        mock_resp.text = mock_answer
        mock_resp.usage_metadata.total_token_count = 100
        mock_client.models.generate_content.return_value = mock_resp
        
        config = GeminiConfig(api_key="key")
        res = call_gemini("query", "context", config, ret_metadata)
        
        assert res.answer == mock_answer
        # Confidence: cited 1, 2, 4 -> 3 sources -> "medium"
        assert res.confidence == "medium"

def test_rag_pipeline_end_to_end(mock_chunks):
    mock_vs = MagicMock()
    mock_emb = MagicMock()
    mock_ret_cfg = RetrievalConfig()
    mock_gem_cfg = GeminiConfig(api_key="key")
    
    pipeline = RAGPipeline(mock_vs, mock_emb, mock_ret_cfg, mock_gem_cfg)
    
    # Mock retrieve
    ret_context = RetrievedContext(
        chunks=mock_chunks,
        query="kérdés",
        retrieval_metadata={"top_scores": [0.9, 0.8, 0.7]}
    )
    
    with patch("renovai.rag.pipeline.retrieve", return_value=ret_context):
        with patch("renovai.rag.pipeline.call_gemini") as mock_call:
            mock_call.return_value = RAGResponse(
                answer="Válasz [FORRÁS 1] alapján.",
                sources_cited=[],
                confidence="low",
                retrieval_metadata={},
                tokens_used=100
            )
            
            response = pipeline.query("kérdés")
            
            assert response.answer == "Válasz [FORRÁS 1] alapján."
            assert response.sources_cited == ["file1.md"]
            # Pipeline updates sources_cited correctly
