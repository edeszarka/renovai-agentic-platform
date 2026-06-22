import argparse
import logging
import sys
import pandas as pd
from pathlib import Path
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

# Load environment variables early (crucial for TabPFN token)
load_dotenv()

from renovai.predictor.feature_extractor import build_feature_matrix
from renovai.predictor.price_model import train_model

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("train_model")
console = Console()

def main():
    parser = argparse.ArgumentParser(description="RenovAI Price Model Trainer — Module 6")
    parser.add_argument("--quotes-dir", type=str, default="data/processed/quotes_json/", help="Adjusted JSONs directory")
    parser.add_argument("--model-dir", type=str, default="data/models/", help="Output directory for saved models")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        
    quotes_dir = Path(args.quotes_dir)
    model_dir = Path(args.model_dir)
    
    if not quotes_dir.exists():
        console.print(f"[bold red]Error: Quotes directory not found: {quotes_dir}[/bold red]")
        sys.exit(1)
        
    console.print(f"\n[bold yellow]Step 1: Building Feature Matrix from {quotes_dir}...[/bold yellow]")
    X, y = build_feature_matrix(quotes_dir)
    
    if len(X) == 0:
        console.print("[bold red]Error: No adjusted quotes found to train on. Run inflation adjustment first.[/bold red]")
        sys.exit(1)
        
    console.print(f"Feature matrix shape: [green]{X.shape}[/green]")
    console.print(f"Target vector size: [green]{len(y)}[/green]")
    
    console.print("\n[bold yellow]Step 2: Training and Evaluating Models (LOO-CV)...[/bold yellow]")
    results = train_model(X, y, model_dir)
    
    # Model Comparison Table
    console.print("\n[bold]Model Comparison (MAPE):[/bold]")
    comp_table = Table(show_header=True, header_style="bold yellow")
    comp_table.add_column("Model", style="cyan")
    comp_table.add_column("MAPE (%)", justify="right")
    comp_table.add_column("Status", justify="center")
    
    for model_name, mape in results["mape_comparison"].items():
        status = "[green]Selected[/green]" if model_name == results["recommended_model"] else ""
        comp_table.add_row(model_name.upper(), f"{mape*100:.1f}%", status)
    console.print(comp_table)
    
    console.print(f"\n[bold]Best Model Results:[/bold]")
    console.print(f"MAE:  [cyan]{results['loo_mae']:,.0f} Ft[/cyan]")
    console.print(f"MAPE: [cyan]{results['loo_mape_pct']:.1f}%[/cyan]")
    
    # Feature Importances Table
    if results["feature_importances"]:
        console.print("\n[bold]Top Feature Importances (GBR):[/bold]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Feature", style="dim")
        table.add_column("Importance", justify="right")
        
        for feat, imp in results["feature_importances"].items():
            table.add_row(feat, f"{imp:.4f}")
        console.print(table)
        
    # Examples
    console.print("\n[bold]Example Predictions (LOO folds):[/bold]")
    for i, ex in enumerate(results["examples"]):
        diff = ex["pred"] - ex["actual"]
        diff_pct = (diff / ex["actual"]) * 100
        console.print(f"  {i+1}. Actual: [blue]{ex['actual']:,.0f} Ft[/blue] | Pred: [green]{ex['pred']:,.0f} Ft[/green] | Diff: {diff_pct:+.1f}%")
        
    console.print(f"\n[bold green]Success![/bold green] Model saved to {model_dir}/price_model.joblib\n")

if __name__ == "__main__":
    main()
