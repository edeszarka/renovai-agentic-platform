import argparse
import logging
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from renovai.rag.embedder import EmbedderConfig
from renovai.rag.gemini_client import GeminiConfig
from renovai.rag.pipeline import RAGPipeline
from renovai.rag.retriever import RetrievalConfig
from renovai.rag.vector_store import RenovAIVectorStore, VectorStoreConfig

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.ERROR)
console = Console()


def main():
    parser = argparse.ArgumentParser(
        description="RenovAI RAG Interactive CLI — Module 5"
    )
    parser.add_argument(
        "--chroma-dir", type=str, default="data/chroma_db/", help="ChromaDB path"
    )
    args = parser.parse_args()

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        console.print("[bold red]Error: GOOGLE_API_KEY not found in .env[/bold red]")
        sys.exit(1)

    # Initialize components
    vs_config = VectorStoreConfig(persist_dir=args.chroma_dir)
    store = RenovAIVectorStore(vs_config)

    emb_config = EmbedderConfig(api_key=api_key)
    ret_config = RetrievalConfig()
    gem_config = GeminiConfig(api_key=api_key)

    pipeline = RAGPipeline(store, emb_config, ret_config, gem_config)

    history = []
    debug_mode = False

    console.print(
        Panel.fit(
            "[bold green]RenovAI RAG Engine Ready[/bold green]\n"
            "Tegyen fel kérdéseket magyarul a felújításokkal kapcsolatban.\n"
            "Parancsok: 'quit' (kilépés), 'reset' (történet törlése), 'debug' (debug mód ki/be)",
            title="RenovAI Module 5",
        )
    )

    while True:
        try:
            user_input = console.input("\n[bold cyan]RenovAI > [/bold cyan]").strip()

            if not user_input:
                continue

            if user_input.lower() in ["quit", "exit", "q"]:
                break

            if user_input.lower() == "reset":
                history = []
                console.print("[yellow]Beszélgetési előzmények törölve.[/yellow]")
                continue

            if user_input.lower() == "debug":
                debug_mode = not debug_mode
                status = "BE" if debug_mode else "KI"
                console.print(f"[yellow]Debug mód: {status}[/yellow]")
                continue

            # Special 'debug' prefix support
            current_debug = debug_mode
            if user_input.lower().startswith("debug "):
                current_debug = True
                user_input = user_input[6:].strip()

            with console.status("[bold green]Gondolkodom...[/bold green]"):
                if current_debug:
                    response, trace = pipeline.query_with_trace(
                        user_input, conversation_history=history
                    )
                    # Print debug info immediately if possible
                    console.print(
                        "\n[bold yellow]--- DEBUG: Lekért dokumentumok ---[/bold yellow]"
                    )
                    for i, chunk in enumerate(trace.chunks):
                        score = (
                            trace.retrieval_metadata["top_scores"][i]
                            if i < len(trace.retrieval_metadata["top_scores"])
                            else 0
                        )
                        console.print(
                            f"[bold]{i + 1}. {chunk.source_file} | {chunk.section}[/bold] (Score: {score:.3f})"
                        )
                        console.print(f"[dim]{chunk.content[:200]}...[/dim]\n")
                else:
                    response = pipeline.query(user_input, conversation_history=history)

            # Update history
            history.append({"role": "user", "content": user_input})
            history.append({"role": "model", "content": response.answer})

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[bold red]Hiba történt: {e}[/bold red]")

    console.print("\n[green]Viszlát![/green]")


if __name__ == "__main__":
    main()
