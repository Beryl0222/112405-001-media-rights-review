"""Synthetic rights and release review workbench.

The package deliberately keeps people, characters, works, scopes, claims and
publication events as distinct, linkable objects.  It never turns a note such
as "consent obtained" into a verified authorization and never reaches an
automatic infringement conclusion from similarity or attention metrics.
"""

from .model import (
    Authorization,
    Case,
    Contact,
    Entity,
    Matter,
    Objection,
    PublicationRecord,
    RawRecord,
    ReleaseItem,
    Revision,
    RightRequirement,
    Scope,
)
from .desk import ReviewDesk
from .engine import ReviewEngine, ReviewReport, review_case, review_dict
from .api import (
    analyze_scope,
    analyze_text,
    build_report,
    check_release,
    evaluate_case,
    process_case,
    review_file,
    review_payload,
    review_release,
    review_text,
    parse_text,
)
from .infringement import InfringementAssessment, assess_infringement

__all__ = [
    "Authorization",
    "Case",
    "Contact",
    "Entity",
    "Matter",
    "Objection",
    "PublicationRecord",
    "RawRecord",
    "ReleaseItem",
    "Revision",
    "RightRequirement",
    "ReviewDesk",
    "ReviewEngine",
    "ReviewReport",
    "Scope",
    "assess_infringement",
    "review_case",
    "review_dict",
    "review_file",
    "review_payload",
    "analyze_text",
    "analyze_scope",
    "review_text",
    "parse_text",
    "process_case",
    "evaluate_case",
    "build_report",
    "check_release",
    "review_release",
    "License",
    "Grant",
    "ReviewSystem",
    "ReleaseReviewDesk",
    "Workbench",
]

__version__ = "0.2.0"

License = Authorization
Grant = Authorization
ReviewSystem = ReviewDesk
ReviewService = ReviewDesk
ReleaseReviewDesk = ReviewDesk
Workbench = ReviewDesk
check_infringement = assess_infringement
InfringementResult = InfringementAssessment
