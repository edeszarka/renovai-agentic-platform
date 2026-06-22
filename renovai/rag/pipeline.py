import re
from typing import List, Optional, Tuple, Dict, Any
from .vector_store import RenovAIVectorStore
from .embedder import EmbedderConfig
from .retriever import RetrievalConfig, retrieve, assemble_context, RetrievedContext
from .gemini_client import GeminiConfig, RAGResponse, call_gemini, stream_gemini

class RAGPipeline:
    def __init__(
        self,
        vector_store: RenovAIVectorStore,
        embedder_config: EmbedderConfig,
        retrieval_config: RetrievalConfig,
        gemini_config: GeminiConfig
    ):
        self.vector_store = vector_store
        self.embedder_config = embedder_config
        self.retrieval_config = retrieval_config
        self.gemini_config = gemini_config

    def query(
        self,
        question: str,
        metadata_filter: Optional[dict] = None,
        conversation_history: Optional[List[dict]] = None
    ) -> RAGResponse:
        """Executes the full RAG pipeline."""
        response, _ = self.query_with_trace(
            question=question,
            metadata_filter=metadata_filter,
            conversation_history=conversation_history
        )
        return response

    def query_with_trace(
        self,
        question: str,
        metadata_filter: Optional[dict] = None,
        conversation_history: Optional[List[dict]] = None
    ) -> Tuple[RAGResponse, RetrievedContext]:
        """Executes the full RAG pipeline and returns retrieval trace."""
        # 1. Retrieve
        retrieved = retrieve(
            query=question,
            vector_store=self.vector_store,
            embedder_config=self.embedder_config,
            config=self.retrieval_config,
            metadata_filter=metadata_filter
        )
        
        # 2. Assemble context
        context_str = assemble_context(retrieved)
        
        # 3. Call Gemini
        response = call_gemini(
            query=question,
            context=context_str,
            config=self.gemini_config,
            retrieval_metadata=retrieved.retrieval_metadata,
            conversation_history=conversation_history
        )
        
        # 4. Post-process: Map citations back to source files
        # Extract [FORRÁS n] from answer
        citation_numbers = re.findall(r'\[FORRÁS (\d+)\]', response.answer)
        unique_indices = sorted(list(set(int(n) for n in citation_numbers)))
        
        cited_files = []
        for idx in unique_indices:
            if 1 <= idx <= len(retrieved.chunks):
                cited_files.append(retrieved.chunks[idx-1].source_file)
        
        response.sources_cited = sorted(list(set(cited_files)))
        
        return response, retrieved

    async def stream_query(
        self,
        question: str,
        metadata_filter: Optional[dict] = None,
        conversation_history: Optional[List[dict]] = None
    ):
        """Streams the RAG pipeline response."""
        # 1. Retrieve
        retrieved = retrieve(
            query=question,
            vector_store=self.vector_store,
            embedder_config=self.embedder_config,
            config=self.retrieval_config,
            metadata_filter=metadata_filter
        )
        
        # 2. Assemble context
        context_str = assemble_context(retrieved)
        
        # 3. Stream Gemini
        async for token in stream_gemini(
            query=question,
            context=context_str,
            config=self.gemini_config,
            conversation_history=conversation_history
        ):
            yield token
