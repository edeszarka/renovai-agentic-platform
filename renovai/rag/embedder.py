import hashlib
import logging
import time
from pathlib import Path
from typing import List, Tuple

from diskcache import Cache
from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .chunker import Chunk

logger = logging.getLogger(__name__)


class EmbedderConfig(BaseModel):
    model_name: str = "gemini-embedding-001"
    task_type: str = "RETRIEVAL_DOCUMENT"
    batch_size: int = 20  # Reduced from 50 for stability
    api_key: str
    cache_dir: str = "data/cache/embeddings"


def get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _get_content_hash(text: str, model: str) -> str:
    """Generate a unique hash for a text content and model combination."""
    return hashlib.md5(f"{model}:{text}".encode("utf-8")).hexdigest()


@retry(
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda retry_state: logger.warning(
        f"Rate limit or API error. Retrying in {retry_state.next_action.sleep}s... "
        f"Attempt {retry_state.attempt_number}/10"
    ),
)
def _embed_with_retry(
    client: genai.Client, model: str, content: List[str], task_type: str
):
    # Ensure model name has 'models/' prefix
    model_id = model
    if not (model_id.startswith("models/") or model_id.startswith("tunedModels/")):
        model_id = f"models/{model_id}"

    return client.models.embed_content(
        model=model_id,
        contents=content,
        config=types.EmbedContentConfig(task_type=task_type),
    )


def embed_chunks(
    chunks: List[Chunk], config: EmbedderConfig
) -> List[Tuple[Chunk, List[float]]]:
    """Embeds a list of chunks using cache and Google GenAI embeddings."""
    client = get_client(config.api_key)

    # Initialize cache
    cache_path = Path(config.cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    cache = Cache(str(cache_path))

    results = []
    to_embed_indices = []

    # 1. Check cache first
    for idx, chunk in enumerate(chunks):
        c_hash = _get_content_hash(chunk.content, config.model_name)
        cached_embedding = cache.get(c_hash)

        if cached_embedding:
            results.append((chunk, cached_embedding))
        else:
            to_embed_indices.append(idx)

    if not to_embed_indices:
        logger.info("All chunks retrieved from cache.")
        cache.close()
        return results

    logger.info(
        f"Retrieved {len(results)} chunks from cache. Need to embed {len(to_embed_indices)} new chunks."
    )

    # 2. Embed missing chunks in batches
    chunks_to_embed = [chunks[i] for i in to_embed_indices]
    total_new = len(chunks_to_embed)

    for i in range(0, total_new, config.batch_size):
        batch = chunks_to_embed[i : i + config.batch_size]
        batch_texts = [c.content for c in batch]

        batch_num = (i // config.batch_size) + 1
        total_batches = (total_new + config.batch_size - 1) // config.batch_size
        logger.info(f"Embedding new batch {batch_num}/{total_batches}...")

        try:
            response = _embed_with_retry(
                client=client,
                model=config.model_name,
                content=batch_texts,
                task_type=config.task_type,
            )

            embeddings = [emb.values for emb in response.embeddings]
            logger.info(f"Received {len(embeddings)} embeddings for batch.")

            for chunk, emb in zip(batch, embeddings):
                # Save to cache
                c_hash = _get_content_hash(chunk.content, config.model_name)
                cache.set(c_hash, emb)
                results.append((chunk, emb))

            # Respect RPM
            if i + config.batch_size < total_new:
                logger.info("Coalescing for 3s to respect API quota...")
                time.sleep(3.0)

        except Exception as e:
            logger.error(f"Failed to embed batch after retries: {e}")
            cache.close()
            raise e

    cache.close()
    return results


def embed_query(query_text: str, config: EmbedderConfig) -> List[float]:
    """Embeds a single query string for retrieval with task_type='RETRIEVAL_QUERY'."""
    client = get_client(config.api_key)

    try:
        response = _embed_with_retry(
            client=client,
            model=config.model_name,
            content=[query_text],
            task_type="RETRIEVAL_QUERY",
        )
        if response.embeddings:
            return response.embeddings[0].values
    except Exception as e:
        logger.error(f"Failed query embedding: {e}")
        raise e

    return []
