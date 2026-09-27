"""Backwards-compatible entry point: ``uv run main.py URL [options]``."""

from web_scraper.cli import run

if __name__ == "__main__":
    run()
