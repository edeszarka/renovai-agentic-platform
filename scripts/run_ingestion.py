import argparse
import asyncio
import json
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from renovai.db.repository import CPIRepository, QuoteRepository
from renovai.db.session import get_engine, get_session_maker, init_db
from renovai.ingestion.inflation_calc import adjust_quote, load_price_index
from renovai.ingestion.md_writer import format_huf, write_markdown
from renovai.ingestion.quote_parser import parse_quote

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)],
)
logger = logging.getLogger("ingestion")
console = Console()


async def async_main():
    parser = argparse.ArgumentParser(
        description="RenovAI Ingestion Runner — Module 1 Reloaded"
    )
    parser.add_argument(
        "--quotes-dir",
        type=str,
        default="data/raw/quotes/",
        help="Path to raw XLSX quotes",
    )
    parser.add_argument(
        "--json-dir",
        type=str,
        default="data/processed/quotes_json/",
        help="Output path for JSON",
    )
    parser.add_argument(
        "--md-dir",
        type=str,
        default="data/processed/quotes_md/",
        help="Output path for Markdown",
    )
    parser.add_argument(
        "--adjust-inflation",
        action="store_true",
        help="Enable inflation calculator adjustments",
    )
    parser.add_argument(
        "--target-date",
        type=str,
        default=None,
        help="Target date for inflation adjustment (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--materials-cpi",
        type=str,
        default="data/raw/inflation/materials_cpi.csv",
        help="Path to materials CPI csv",
    )
    parser.add_argument(
        "--labor-cpi",
        type=str,
        default="data/raw/inflation/labor_cpi.csv",
        help="Path to labor CPI csv",
    )
    parser.add_argument(
        "--skip-db", action="store_true", help="Skip database ingestion"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    quotes_path = Path(args.quotes_dir)
    json_path = Path(args.json_dir)
    md_path = Path(args.md_dir)

    # Ensure output directories exist
    json_path.mkdir(parents=True, exist_ok=True)
    md_path.mkdir(parents=True, exist_ok=True)

    # 1. Quote Ingestion
    console.print(
        f"\n[bold yellow]Phase 2: Ingesting Quotes from {quotes_path}...[/bold yellow]"
    )
    xlsx_files = list(quotes_path.rglob("*.xlsx"))

    if not xlsx_files:
        logger.warning(f"No .xlsx files found in {quotes_path}")
        return

    # DB Init
    session_maker = None
    if not args.skip_db:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            logger.error(
                "DATABASE_URL not set in .env. Use --skip-db if database is not needed."
            )
            return
        engine = get_engine(db_url)
        await init_db(engine)
        session_maker = get_session_maker(engine)

    # Inflation Setup
    price_index = None
    target_date = None
    if args.adjust_inflation:
        if args.target_date:
            try:
                target_date = date.fromisoformat(args.target_date)
            except ValueError:
                logger.error(
                    f"Invalid target-date format: {args.target_date}. Expected YYYY-MM-DD."
                )
                return
        else:
            target_date = date.today()

        materials_csv = Path(args.materials_cpi)
        labor_csv = Path(args.labor_cpi)
        if not materials_csv.exists() or not labor_csv.exists():
            logger.error(
                f"CPI CSV files not found. Ensure {materials_csv} and {labor_csv} exist."
            )
            return

        price_index = load_price_index(materials_csv, labor_csv)

        # Save CPI to DB
        if session_maker:
            cpi_repo = CPIRepository()
            async with session_maker() as session:
                await cpi_repo.upsert_cpi_records(
                    session, price_index.materials, component_override="materials"
                )
                await cpi_repo.upsert_cpi_records(
                    session, price_index.labor, component_override="labor"
                )
                await session.commit()

    # Results Table
    table = Table(title="RenovAI Advanced Ingestion Summary")
    table.add_column("Filename", style="cyan", no_wrap=True)
    table.add_column("Style", style="yellow")
    table.add_column("District", justify="center")
    table.add_column("Total (HUF)", justify="right", style="green")
    table.add_column("Items", justify="right")
    table.add_column("Status", justify="center")
    if args.adjust_inflation:
        table.add_column("Delta (%)", justify="right", style="magenta")

    processed_count = 0
    error_count = 0
    total_huf_sum = 0
    style_counts = {
        "text_only": 0,
        "standard_5col": 0,
        "multi_phase": 0,
        "scope_only": 0,
    }
    districts_seen = set()
    failed_quotes = []

    quote_repo = QuoteRepository()

    for file in xlsx_files:
        try:
            # 1. Parse Quote
            quote = parse_quote(file)

            # 2. Metadata Collection for Global Stats
            processed_count += 1
            total_huf_sum += quote.metadata.grand_total
            style_counts[quote.quote_style] += 1
            if quote.metadata.district:
                districts_seen.add(quote.metadata.district)

            # 3. Write JSON (Original)
            json_file = json_path / f"{file.stem}.json"
            with open(json_file, "w", encoding="utf-8") as f:
                f.write(quote.model_dump_json(indent=2))

            # 4. Write Markdown (for RAG)
            md_file = md_path / f"{file.stem}.md"
            write_markdown(quote, md_file)

            # 5. Inflation Adjustment
            adj_quote = None
            if args.adjust_inflation and price_index and target_date:
                adj_quote = adjust_quote(quote, price_index, target_date)
                adj_json_file = json_path / f"{file.stem}_adjusted.json"
                with open(adj_json_file, "w", encoding="utf-8") as f:
                    f.write(adj_quote.model_dump_json(indent=2))

            # 6. Database Upsert
            db_status = "[gray]SKIP[/gray]"
            if session_maker:
                async with session_maker() as session:
                    await quote_repo.upsert_quote(session, quote)
                    await session.commit()
                    db_status = "[green]OK[/green]"

            # Table Row
            row_data = [
                file.name,
                quote.quote_style,
                str(quote.metadata.district or "-"),
                format_huf(quote.metadata.grand_total),
                str(len(quote.line_items)),
                db_status,
            ]
            if args.adjust_inflation:
                if adj_quote:
                    sign = "+" if adj_quote.inflation_delta_pct > 0 else ""
                    row_data.append(f"{sign}{adj_quote.inflation_delta_pct}%")
                else:
                    row_data.append("-")
            table.add_row(*row_data)

        except Exception as e:
            logger.error(f"Failed to process {file.name}: {e}")
            error_count += 1
            failed_quotes.append({"file": file.name, "error": str(e)})
            table.add_row(file.name, "-", "-", "-", "-", "[red]ERROR[/red]")
            if args.adjust_inflation:
                table.add_row(file.name, "-", "-", "-", "-", "[red]ERROR[/red]", "-")

    console.print(table)

    # Write failures log
    if failed_quotes:
        fail_log = Path("data/processed/failed_quotes.json")
        with open(fail_log, "w", encoding="utf-8") as f:
            json.dump(failed_quotes, f, indent=2, ensure_ascii=False)
        logger.warning(f"Logged {len(failed_quotes)} failures to {fail_log}")

    # Aggregate Stats
    console.print("\n[bold]Aggregate Statistics:[/bold]")
    stats_table = Table()
    stats_table.add_column("Metric", style="dim")
    stats_table.add_column("Value", style="bold")

    stats_table.add_row("Total Files Found", str(len(xlsx_files)))
    stats_table.add_row("Succeeded", f"[green]{processed_count}[/green]")
    stats_table.add_row("Failed", f"[red]{error_count}[/red]")
    stats_table.add_row("Total Volume (HUF)", f"{format_huf(total_huf_sum)} Ft")
    stats_table.add_row(
        "Districts Seen", ", ".join(map(str, sorted(list(districts_seen))))
    )

    console.print(stats_table)

    console.print("\n[bold]Style Breakdown:[/bold]")
    for s, count in style_counts.items():
        console.print(f"- {s:15}: {count}")


if __name__ == "__main__":
    asyncio.run(async_main())
