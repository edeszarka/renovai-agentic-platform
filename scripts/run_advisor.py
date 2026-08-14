import argparse
import logging
import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, FloatPrompt, Confirm

from renovai.advisor.pre_purchase import ApartmentProfile, generate_report
from renovai.advisor.report_renderer import render_report_md
from renovai.rag.vector_store import VectorStoreConfig, RenovAIVectorStore
from renovai.rag.embedder import EmbedderConfig
from renovai.rag.retriever import RetrievalConfig
from renovai.rag.gemini_client import GeminiConfig
from renovai.rag.pipeline import RAGPipeline
from renovai.ingestion.inflation_calc import load_price_index

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.ERROR)
console = Console()

def main():
    parser = argparse.ArgumentParser(description="RenovAI Pre-Purchase Advisor CLI — Module 7")
    parser.add_argument("--chroma-dir", type=str, default="data/chroma_db/", help="ChromaDB path")
    parser.add_argument("--model-dir", type=str, default="data/models/", help="Price model directory")
    parser.add_argument("--interactive", action="store_true", help="Run in interactive mode")
    args = parser.parse_args()
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        console.print("[bold red]Error: GOOGLE_API_KEY not found in .env[/bold red]")
        sys.exit(1)
        
    if not args.interactive:
        console.print("[yellow]Jelenleg csak az interaktív mód támogatott. Használja a --interactive kapcsolót.[/yellow]")
        return

    # 1. Initialize components
    with console.status("[bold green]Rendszer inicializálása...[/bold green]"):
        vs_config = VectorStoreConfig(persist_dir=args.chroma_dir)
        store = RenovAIVectorStore(vs_config)
        
        emb_config = EmbedderConfig(api_key=api_key)
        ret_config = RetrievalConfig()
        gem_config = GeminiConfig(api_key=api_key)
        
        rag_pipeline = RAGPipeline(store, emb_config, ret_config, gem_config)
        
        # Load price index for predictor
        price_index = load_price_index(
            Path("data/raw/inflation/materials_cpi.csv"),
            Path("data/raw/inflation/labor_cpi.csv")
        )
        
    console.print(Panel.fit(
        "[bold green]Üdvözli a RenovAI Vásárlási Tanácsadó![/bold green]\n"
        "Kérjük, adjon meg néhány adatot a vizsgált ingatlanról.",
        title="RenovAI Module 7"
    ))
    
    # 2. Collect user input
    district = IntPrompt.ask("Melyik kerületben van a lakás? (1-23)", default=13)
    area = FloatPrompt.ask("Hány négyzetméter a lakás?", default=50.0)
    rooms = IntPrompt.ask("Hány szoba van?", default=2)
    
    b_type = Prompt.ask(
        "Mi az épület típusa?",
        choices=["panel", "tégla", "újépítés", "ismeretlen"],
        default="tégla"
    )
    
    era = Prompt.ask(
        "Mikor épült az épület (kb.)?",
        choices=["1945 előtt", "1945–1970", "1970–1990", "1990–2010", "2010 után", "ismeretlen"],
        default="1945–1970"
    )
    era_val = None if era == "ismeretlen" else {
        "1945 előtt": 1930,
        "1945–1970": 1960,
        "1970–1990": 1980,
        "1990–2010": 2000,
        "2010 után": 2015,
    }[era]
    
    condition = Prompt.ask(
        "Milyen a lakás jelenlegi állapota?",
        choices=["nagyon_rossz", "közepes", "lakható"],
        default="közepes"
    )
    
    issues_str = Prompt.ask("Vannak ismert hibák? (vesszővel elválasztva, pl. beázás, penész)", default="")
    issues = [s.strip() for s in issues_str.split(",") if s.strip()]
    
    seen = Confirm.ask("Látta már személyesen a lakást?", default=False)
    
    price = FloatPrompt.ask("Mi az ingatlan irányára (millió Ft)?", default=None)
    
    profile = ApartmentProfile(
        address_district=district,
        floor_area_sqm=area,
        num_rooms=rooms,
        building_type=b_type,
        building_era_approx=era_val,
        current_condition=condition,
        known_issues=issues,
        has_seen_in_person=seen,
        asking_price_million_huf=price
    )
    
    # 3. Generate Report
    console.print("\n[bold yellow]Elemzés folyamatban (többszörös RAG lekérdezés és modell futtatás)...[/bold yellow]")
    try:
        with console.status("[bold green]Jelentés összeállítása...[/bold green]"):
            report = generate_report(
                profile=profile,
                rag_pipeline=rag_pipeline,
                price_predictor_model_dir=Path(args.model_dir),
                price_index=price_index,
                gemini_config=gem_config
            )
            
        md_content = render_report_md(report)
        
        # 4. Save and Print
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = Path(f"data/reports/report_{timestamp}_{district}ker_{int(area)}sqm.md")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(md_content, encoding="utf-8")
        
        console.print("\n" + "="*50)
        console.print(md_content)
        console.print("="*50)
        console.print(f"\n[bold green]Siker![/bold green] A jelentés elmentve ide: [cyan]{report_path}[/cyan]\n")
        
    except Exception as e:
        console.print(f"[bold red]Hiba történt a jelentés generálása közben: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())

if __name__ == "__main__":
    main()
