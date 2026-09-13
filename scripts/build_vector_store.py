import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler

from renovai.rag.chunker import chunk_all, chunk_all_transcripts, count_tokens
from renovai.rag.embedder import EmbedderConfig, embed_chunks, embed_query
from renovai.rag.vector_store import RenovAIVectorStore, VectorStoreConfig

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)],
)
logger = logging.getLogger("build_vector_store")
console = Console()


def main():
    parser = argparse.ArgumentParser(
        description="RenovAI Vector Store Builder — Module 4"
    )
    parser.add_argument(
        "--quotes-md-dir",
        type=str,
        default="data/processed/quotes_md/",
        help="Quotes MD directory",
    )
    parser.add_argument(
        "--transcripts-dir",
        type=str,
        default="data/raw/video_transcripts/",
        help="Video transcript TXT directory",
    )
    parser.add_argument(
        "--chroma-dir", type=str, default="data/chroma_db/", help="ChromaDB path"
    )
    parser.add_argument(
        "--rebuild", action="store_true", help="Drop and recreate collection"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.error("GOOGLE_API_KEY is not set. Please set it in your .env file.")
        sys.exit(1)

    quotes_dir = Path(args.quotes_md_dir)
    transcripts_dir = Path(args.transcripts_dir)
    chroma_dir = Path(args.chroma_dir)

    # 1. Chunking quotes
    console.print("\n[bold yellow]Step 1: Chunking Markdown Quotes...[/bold yellow]")
    chunks = chunk_all(quotes_dir)
    console.print(f"Quote chunks generated: [green]{len(chunks)}[/green]")

    # 2. Chunking transcripts
    console.print("\n[bold yellow]Step 2: Chunking Video Transcripts...[/bold yellow]")
    transcript_chunks = chunk_all_transcripts(transcripts_dir)
    if transcript_chunks:
        console.print(
            f"Transcript chunks generated: [green]{len(transcript_chunks)}[/green]"
        )
        chunks.extend(transcript_chunks)
    else:
        console.print("[yellow]No transcript files found.[/yellow]")

    if not chunks:
        logger.warning("No chunks generated from any source.")
        sys.exit(0)

    total_chunks = len(chunks)
    total_tokens = sum(count_tokens(c.content) for c in chunks)
    avg_tokens = total_tokens / total_chunks if total_chunks > 0 else 0

    console.print(f"Total chunks generated: [green]{total_chunks}[/green]")
    console.print(f"Average tokens per chunk: [green]{avg_tokens:.1f}[/green]")

    # 3. Embedding
    console.print(
        "\n[bold yellow]Step 3: Embedding Chunks via Google GenAI...[/bold yellow]"
    )
    embed_config = EmbedderConfig(api_key=api_key)

    try:
        chunk_embeddings = embed_chunks(chunks, embed_config)
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        sys.exit(1)

    # 4. Build Vector Store
    console.print("\n[bold yellow]Step 4: Storing Chunks in ChromaDB...[/bold yellow]")
    vs_config = VectorStoreConfig(persist_dir=str(chroma_dir))
    store = RenovAIVectorStore(vs_config)

    if args.rebuild:
        console.print("[bold red]Rebuild flag set. Dropping collection...[/bold red]")
        try:
            store.client.delete_collection(vs_config.collection_name)
        except Exception:
            pass
        store = RenovAIVectorStore(vs_config)

    chunks_list = [item[0] for item in chunk_embeddings]
    embs_list = [item[1] for item in chunk_embeddings]

    store.build(chunks_list, embs_list)
    console.print("[green]Vector store successfully built/updated.[/green]")

    # 5. Running sample queries to validate
    console.print("\n[bold yellow]Step 5: Running Validation Queries...[/bold yellow]")
    sample_queries = [
        "Mennyibe kerül egy fürdőszoba felújítás?",
        "Mit kell megkérdezni az eladótól vásárlás előtt?",
        "Kohósalak probléma mit jelent a felújításnál?",
    ]

    for idx, q_text in enumerate(sample_queries):
        console.print(f"\n[bold]Query {idx + 1}: '{q_text}'[/bold]")
        try:
            q_emb = embed_query(q_text, embed_config)
            results = store.query(q_text, q_emb, n_results=3)

            for rank, (chunk, dist) in enumerate(results):
                console.print(
                    f"  [bold]Rank {rank + 1}:[/bold] {chunk.source_file} | Section: {chunk.section} | Dist: {dist:.3f}"
                )
                preview = chunk.content[:200].replace("\n", " ")
                console.print(f"    [dim]Preview: {preview}...[/dim]")
        except Exception as e:
            logger.error(f"Failed query validation for '{q_text}': {e}")

    # 6. Get statistics
    stats = store.get_collection_stats()
    console.print("\n[bold yellow]Step 6: Final Vector Store Statistics[/bold yellow]")
    console.print(f"Chunks count: [green]{stats['chunks_count']}[/green]")
    console.print(f"Last updated: [green]{stats['last_updated']}[/green]\n")


if __name__ == "__main__":
    main()
