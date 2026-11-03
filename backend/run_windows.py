"""Run the API with the event loop required by psycopg on Windows."""

import argparse
import asyncio
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the TalentOS API on Windows")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18080, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    loop_factory: str | object = "auto"
    if sys.platform == "win32":
        # Uvicorn 0.52 creates its loop through asyncio.run(), which ignores the
        # legacy policy hook. Supplying a factory guarantees psycopg receives a
        # selector loop in the actual server process.
        loop_factory = asyncio.SelectorEventLoop

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop=loop_factory,
    )


if __name__ == "__main__":
    main()
