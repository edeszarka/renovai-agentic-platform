import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from renovai.db.session import get_engine, get_session_maker
from renovai.db.text_to_sql import TextToSQLEngine

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.ERROR)
console = Console()


async def main():
    parser = argparse.ArgumentParser(
        description="RenovAI SQL Natural Language Query — Module 1b"
    )
    args = parser.parse_args()

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        console.print("[bold red]Error: GOOGLE_API_KEY not found in .env[/bold red]")
        sys.exit(1)

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        console.print("[bold red]Error: DATABASE_URL not found in .env[/bold red]")
        sys.exit(1)

    # Initialize components
    engine = get_engine(db_url)
    session_maker = get_session_maker(engine)

    sql_engine = TextToSQLEngine(api_key)

    console.print(
        Panel.fit(
            "[bold green]RenovAI SQL Query Engine Ready[/bold green]\n"
            "Tegyen fel kérdéseket magyarul az árajánlatokkal kapcsolatban.\n"
            "Parancsok: 'quit' (kilépés), 'clear' (képernyő törlése)",
            title="RenovAI Module 1b",
        )
    )

    while True:
        try:
            question = console.input("\n[bold cyan]RenovAI SQL > [/bold cyan]").strip()

            if not question:
                continue

            if question.lower() in ["quit", "exit", "q"]:
                break

            if question.lower() == "clear":
                console.clear()
                continue

            with console.status(
                "[bold green]SQL generálása és futtatása...[/bold green]"
            ):
                async with session_maker() as session:
                    result = await sql_engine.query(question, session)

            if "error" in result:
                console.print(f"[bold red]Hiba történt:[/bold red] {result['error']}")
                if "sql" in result:
                    console.print(f"[dim]Generált SQL: {result['sql']}[/dim]")
                continue

            # Display SQL
            console.print(f"\n[dim]Generált SQL:[/dim]\n[blue]{result['sql']}[/blue]\n")

            if not result["rows"]:
                console.print("[yellow]Nincs találat.[/yellow]")
                continue

            # Display Table
            table = Table(show_header=True, header_style="bold magenta")
            for key in result["rows"][0].keys():
                table.add_column(key)

            for row in result["rows"]:
                table.add_row(*[str(val) for val in row.values()])

            console.print(table)
            console.print(f"[dim]{result['row_count']} sor visszaadva.[/dim]")

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[bold red]Váratlan hiba:[/bold red] {e}")

    console.print("\n[green]Viszlát![/green]")


if __name__ == "__main__":
    asyncio.run(main())
