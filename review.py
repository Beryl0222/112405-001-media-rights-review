#!/usr/bin/env python3
"""Short wrapper so `python review.py data/case.json` works directly."""

from reviewdesk.cli import main

if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] != "review" and not argv[0].startswith("-"):
        argv = ["review", *argv]
    raise SystemExit(main(argv))
