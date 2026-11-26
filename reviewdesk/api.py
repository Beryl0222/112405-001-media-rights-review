"""Functional API aliases for callers that prefer module-level functions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .desk import ReviewDesk
from .engine import ReviewReport, review_case, review_dict
from .ingest import (
    extract_claims_from_record,
    find_candidate_mentions,
    find_exact_mentions,
    parse_scope_text,
)
from .infringement import assess_infringement
from .model import Case, RawRecord


def review_file(path: str | Path) -> dict[str, Any]:
    """Load, intake revisions, run a review, and return a JSON-safe dict."""

    return ReviewDesk.from_file(path).run_review().to_dict()


def review_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return ReviewDesk.from_dict(payload).run_review().to_dict()


def analyze_text(text: str, entities: list[Any] | None = None) -> list[dict[str, Any]]:
    """Parse one raw text into deterministic claim candidates."""

    entity_objects = []
    for entity in entities or []:
        if hasattr(entity, "all_names"):
            entity_objects.append(entity)
        elif isinstance(entity, dict):
            from .model import Entity
            entity_objects.append(Entity.from_dict(entity))
    record = RawRecord(id="TEXT-1", text=text)
    return [c.to_dict() for c in extract_claims_from_record(record, entity_objects)]


def analyze_scope(text: str) -> dict[str, Any]:
    return parse_scope_text(text).to_dict()


review_text = analyze_text
parse_text = analyze_text
review_scope_text = analyze_scope

# Descriptive functional aliases used by host scripts.
process_case = review_payload
evaluate_case = review_payload
build_report = review_dict
check_release = review_dict
review_release = review_dict


__all__ = [
    "ReviewDesk",
    "ReviewReport",
    "Case",
    "review_case",
    "review_dict",
    "review_file",
    "review_payload",
    "analyze_text",
    "analyze_scope",
    "assess_infringement",
    "find_exact_mentions",
    "find_candidate_mentions",
]
