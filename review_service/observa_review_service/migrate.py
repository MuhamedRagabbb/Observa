"""Bring the database schema up to date (idempotent). The container runs it before serving.

    python -m observa_review_service.migrate [--database-url URL]
"""
import argparse
from pathlib import Path

from .db import upgrade
from .settings import Settings


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    url = ap.parse_args(argv).database_url or Settings().DATABASE_URL
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    upgrade(url)
    print("schema is up to date", flush=True)


if __name__ == "__main__":
    main()
