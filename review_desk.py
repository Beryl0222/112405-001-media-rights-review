"""Convenience top-level facade.

Most implementation lives in the :mod:`reviewdesk` package.  This module gives
scripts and exploratory tests a short, stable import name.
"""

from reviewdesk import *  # noqa: F401,F403
from reviewdesk.api import (  # noqa: F401
    analyze_scope,
    analyze_text,
    review_file,
    review_payload,
)
from reviewdesk.cli import main

__all__ = [  # noqa: F405
    "ReviewDesk",  # type: ignore[name-defined]
    "ReviewEngine",  # type: ignore[name-defined]
    "ReviewReport",  # type: ignore[name-defined]
    "Case",  # type: ignore[name-defined]
    "Scope",  # type: ignore[name-defined]
    "Authorization",  # type: ignore[name-defined]
    "Contact",  # type: ignore[name-defined]
    "Entity",  # type: ignore[name-defined]
    "Matter",  # type: ignore[name-defined]
    "Objection",  # type: ignore[name-defined]
    "PublicationRecord",  # type: ignore[name-defined]
    "RawRecord",  # type: ignore[name-defined]
    "ReleaseItem",  # type: ignore[name-defined]
    "Revision",  # type: ignore[name-defined]
    "RightRequirement",  # type: ignore[name-defined]
    "review_case",  # type: ignore[name-defined]
    "review_dict",  # type: ignore[name-defined]
    "review_file",
    "review_payload",
    "analyze_text",
    "analyze_scope",
    "assess_infringement",  # type: ignore[name-defined]
    "main",
]


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] != "review" and not argv[0].startswith("-"):
        argv = ["review", *argv]
    raise SystemExit(main(argv))
