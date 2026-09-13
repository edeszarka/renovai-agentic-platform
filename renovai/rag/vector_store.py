import logging
from datetime import datetime
from typing import List, Optional, Tuple

import chromadb
from pydantic import BaseModel

from .chunker import Chunk

logger = logging.getLogger(__name__)


class VectorStoreConfig(BaseModel):
    persist_dir: str = "data/chroma_db"
    collection_name: str = "renovation_quotes"


class RenovAIVectorStore:
    def __init__(self, config: VectorStoreConfig):
        self.config = config
        self.client = chromadb.PersistentClient(path=config.persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=config.collection_name, metadata={"hnsw:space": "cosine"}
        )

    def build(self, chunks: List[Chunk], embeddings: List[List[float]]) -> None:
        """Upsert all chunks and their embeddings into the collection."""
        ids, documents, metadatas, embeddings_list = [], [], [], []

        for chunk, emb in zip(chunks, embeddings):
            # Clean and serialize metadata
            cleaned_meta = {}
            for k, v in chunk.metadata.items():
                if isinstance(v, list):
                    cleaned_meta[k] = ", ".join(v)
                elif isinstance(v, (dict, list)):
                    cleaned_meta[k] = str(v)
                elif v is None:
                    cleaned_meta[k] = ""
                else:
                    cleaned_meta[k] = v

            ids.append(chunk.chunk_id)
            documents.append(chunk.content)
            metadatas.append(cleaned_meta)
            embeddings_list.append(emb)

        if ids:
            logger.info(f"Upserting {len(ids)} chunks")
            self.collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings_list,
            )

    def query(
        self,
        query_text: str,
        query_embedding: List[float],
        n_results: int = 8,
        filter_metadata: Optional[dict] = None,
    ) -> List[Tuple[Chunk, float]]:
        """Quotes the collection and returns results sorted by relevance (cosine distance)."""
        results_list = []

        where_clause = {}
        if filter_metadata:
            for k, v in filter_metadata.items():
                if k == "source_type":
                    continue
                where_clause[k] = v

        query_args = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
        }
        if where_clause:
            query_args["where"] = where_clause

        res = self.collection.query(**query_args)

        if res and res.get("documents"):
            docs = res["documents"][0]
            metas = res["metadatas"][0]
            ids = res["ids"][0]
            distances = (
                res["distances"][0] if res.get("distances") else [0.0] * len(docs)
            )

            for doc, meta, cid, dist in zip(docs, metas, ids, distances):
                meta_copy = dict(meta)
                topics_val = meta_copy.get("topics", "")
                if isinstance(topics_val, str) and topics_val:
                    meta_copy["topics"] = [
                        t.strip() for t in topics_val.split(",") if t.strip()
                    ]

                chunk = Chunk(
                    chunk_id=cid,
                    source_file=meta_copy.get("source_file", ""),
                    source_type="quote",
                    section=meta_copy.get("section", ""),
                    content=doc,
                    metadata=meta_copy,
                )
                results_list.append((chunk, dist))

        # Sort by distance ascending (HNWS cosine distance: smaller is better)
        results_list.sort(key=lambda x: x[1])

        return results_list[:n_results]

    def get_collection_stats(self) -> dict:
        """Returns collection chunk statistics."""
        count = self.collection.count()
        return {"chunks_count": count, "last_updated": datetime.now().isoformat()}

    def get_all_chunks(self) -> List[Chunk]:
        """Retrieves all chunks from the collection."""
        all_chunks = []
        res = self.collection.get()
        if res and res.get("documents"):
            docs = res["documents"]
            metas = res["metadatas"]
            ids = res["ids"]

            for doc, meta, cid in zip(docs, metas, ids):
                meta_copy = dict(meta)
                topics_val = meta_copy.get("topics", "")
                if isinstance(topics_val, str) and topics_val:
                    meta_copy["topics"] = [
                        t.strip() for t in topics_val.split(",") if t.strip()
                    ]

                chunk = Chunk(
                    chunk_id=cid,
                    source_file=meta_copy.get("source_file", ""),
                    source_type="quote",
                    section=meta_copy.get("section", ""),
                    content=doc,
                    metadata=meta_copy,
                )
                all_chunks.append(chunk)
        return all_chunks
