import argparse
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console

from renovai.rag.vector_store import VectorStoreConfig, RenovAIVectorStore
from renovai.rag.embedder import EmbedderConfig
from renovai.rag.retriever import RetrievalConfig
from renovai.rag.gemini_client import GeminiConfig
from renovai.rag.pipeline import RAGPipeline
from renovai.evals.dataset import load_golden_dataset
from renovai.evals.runner import EvalRunner, display_report

# Load environment variables
load_dotenv()
console = Console()

def main():
    parser = argparse.ArgumentParser(description="RenovAI RAG Evaluation CLI")
    parser.add_argument("--chroma-dir", type=str, default="data/chroma_db/", help="ChromaDB path")
    parser.add_argument("--output", type=str, default="data/eval_results.json", help="Path to save results")
    args = parser.parse_args()
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        console.print("[bold red]Error: GOOGLE_API_KEY not found[/bold red]")
        sys.exit(1)
        
    # 1. Initialize Pipeline
    vs_config = VectorStoreConfig(persist_dir=args.chroma_dir)
    store = RenovAIVectorStore(vs_config)
    emb_config = EmbedderConfig(api_key=api_key)
    ret_config = RetrievalConfig()
    gem_config = GeminiConfig(api_key=api_key)
    pipeline = RAGPipeline(store, emb_config, ret_config, gem_config)
    
    # 2. Load Dataset
    dataset = load_golden_dataset()
    console.print(f"Loaded [green]{len(dataset)}[/green] evaluation examples.")
    
    # 3. Run Evals
    runner = EvalRunner(pipeline)
    report = runner.run_suite(dataset)
    
    # 4. Display Results
    display_report(report)
    
    # 5. Save to file
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2))
    console.print(f"\n[dim]Detailed report saved to {output_path}[/dim]")

if __name__ == "__main__":
    main()
