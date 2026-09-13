import argparse

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="RenovAI API Runner — Module 8")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the API on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload for development"
    )

    args = parser.parse_args()

    print(f"\n[bold green]Indítás: RenovAI API[/bold green]")
    print(f"Dokumentáció elérhető: http://{args.host}:{args.port}/docs\n")

    uvicorn.run(
        "legacy.renovai_api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
