#!/usr/bin/env python3
"""Convenience entry point for environments expecting ``solution.py``."""

from pathlib import Path

from reviewdesk import ReviewDesk, review_dict, review_file  # noqa: F401
from reviewdesk.cli import main

DEFAULT_CASE = Path(__file__).resolve().parent / "data" / "case.json"


def solve(input_path: str | Path = DEFAULT_CASE) -> dict:
    """Review a case file and return the JSON-safe report."""

    return review_file(input_path)


if __name__ == "__main__":
    raise SystemExit(main())
