"""wardwatch-fhir: run migrations, serve the API, or export the OpenAPI schema."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from wardwatch_fhir.app import create_app
from wardwatch_fhir.log_config import configure_logging
from wardwatch_fhir.migrate import upgrade
from wardwatch_fhir.settings import Settings


def openapi_document() -> dict[str, object]:
    app = create_app(Settings(run_consumers=False))
    document: dict[str, object] = app.openapi()
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wardwatch-fhir", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="migrate the database, then serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("migrate", help="upgrade the database schema")
    export = commands.add_parser("openapi", help="write the OpenAPI schema")
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    settings = Settings()
    if args.command == "openapi":
        args.output.write_text(json.dumps(openapi_document(), indent=2, sort_keys=True) + "\n")
        return 0
    configure_logging(settings.log_level)
    upgrade(settings.database_url)
    if args.command == "serve":
        uvicorn.run(create_app(settings), host=args.host, port=args.port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
