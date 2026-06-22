import time
import tiktoken
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional, Tuple, Dict, Any
from rank_bm25 import BM25Okapi

from .chunker import Chunk
from .vector_store import RenovAIVectorStore
from .embedder import EmbedderConfig, embed_query

class RetrievalConfig(BaseModel):
    n_semantic: int = 5         # Reduced from 8
    n_keyword: int = 3          # Reduced from 4
    rerank_top_k: int = 4       # Reduced from 6
    min_score_threshold: float = 0.4  # Increased from 0.3 for tighter relevance

class RetrievedContext(BaseModel):
    chunks: List[Chunk]
    query: str
    retrieval_metadata: dict    # timing, scores, sources used

def tokenize_hu(text: str) -> List[str]:
    """Simple whitespace-based tokenizer for Hungarian text."""
    return text.lower().split()

def retrieve(
    query: str,
    vector_store: RenovAIVectorStore,
    embedder_config: EmbedderConfig,
    config: RetrievalConfig,
    metadata_filter: Optional[dict] = None
) -> RetrievedContext:
    """Performs hybrid (vector + BM25) retrieval."""
    start_time = time.time()
    
    # 1. Semantic Search
    query_emb = embed_query(query, embedder_config)
    semantic_tuples = vector_store.query(
        query_text=query,
        query_embedding=query_emb,
        n_results=config.n_semantic,
        filter_metadata=metadata_filter
    )
    
    # ChromaDB returns distance. Convert distance to a similarity-like score [0, 1]
    # For cosine distance, score = 1 - distance
    semantic_results = {c.chunk_id: (c, max(0.0, 1.0 - dist)) 
                        for c, dist in semantic_tuples}
    
    # 2. BM25 Search
    all_chunks = vector_store.get_all_chunks()
    if metadata_filter:
        # Filter all_chunks manually if filter provided
        filtered_all = []
        for c in all_chunks:
            match = True
            for k, v in metadata_filter.items():
                if k == "source_type":
                    if c.source_type != v:
                        match = False
                elif c.metadata.get(k) != v:
                    match = False
            if match:
                filtered_all.append(c)
        all_chunks = filtered_all

    corpus = [c.content for c in all_chunks]
    tokenized_corpus = [tokenize_hu(doc) for doc in corpus]
    bm25 = BM25Okapi(tokenized_corpus)
    
    tokenized_query = tokenize_hu(query)
    bm25_scores = bm25.get_scores(tokenized_query)
    
    # Sort and take top n_keyword
    bm25_ranked_indices = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)
    top_bm25_indices = bm25_ranked_indices[:config.n_keyword]
    
    # Normalize BM25 scores to [0, 1] range approximately
    max_bm25 = max(bm25_scores) if any(bm25_scores) else 1.0
    
    keyword_results = {}
    for i in top_bm25_indices:
        c = all_chunks[i]
        score = bm25_scores[i] / max_bm25 if max_bm25 > 0 else 0
        keyword_results[c.chunk_id] = (c, score)
        
    # 3. Merge results
    merged_results = {}
    all_ids = set(semantic_results.keys()) | set(keyword_results.keys())
    
    for cid in all_ids:
        chunk = None
        score = 0.0
        in_both = cid in semantic_results and cid in keyword_results
        
        if cid in semantic_results:
            chunk, score = semantic_results[cid]
        if cid in keyword_results:
            c_kw, s_kw = keyword_results[cid]
            chunk = c_kw
            score = max(score, s_kw)
            
        if in_both:
            score += 0.15
            
        merged_results[cid] = (chunk, score)
        
    # 4. Filter and Sort
    final_list = [v for v in merged_results.values() if v[1] >= config.min_score_threshold]
    final_list.sort(key=lambda x: x[1], reverse=True)
    
    top_chunks = [item[0] for item in final_list[:config.rerank_top_k]]
    
    end_time = time.time()
    
    retrieval_metadata = {
        "duration_sec": end_time - start_time,
        "n_semantic": len(semantic_results),
        "n_keyword": len(keyword_results),
        "total_merged": len(merged_results),
        "top_scores": [round(item[1], 3) for item in final_list[:config.rerank_top_k]],
        "sources": list(set(c.source_file for c in top_chunks))
    }
    
    return RetrievedContext(
        chunks=top_chunks,
        query=query,
        retrieval_metadata=retrieval_metadata
    )

def assemble_context(
    retrieved: RetrievedContext,
    max_context_tokens: int = 3000   # Reduced from 6000 to save quota
) -> str:
    """Assembles context documents into a single string for the LLM."""
    encoding = tiktoken.get_encoding("cl100k_base")
    
    context_parts = []
    current_tokens = 0
    
    for i, chunk in enumerate(retrieved.chunks):
        part = f"[FORRÁS {i+1}: {chunk.source_file} | {chunk.section}]\n{chunk.content}\n---\n\n"
        part_tokens = len(encoding.encode(part))
        
        if current_tokens + part_tokens > max_context_tokens:
            # If even the first chunk is too big (unlikely with 512 token chunks), 
            # we'd need to truncate it, but here we just stop.
            break
            
        context_parts.append(part)
        current_tokens += part_tokens
        
    return "".join(context_parts).strip()
