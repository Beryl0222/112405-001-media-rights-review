"""Compatibility package name for the distribution name ``case-workbench``."""

from reviewdesk import *  # noqa: F401,F403
from reviewdesk import (  # noqa: F401
    Authorization,
    Case,
    Contact,
    Entity,
    Matter,
    Objection,
    PublicationRecord,
    RawRecord,
    ReleaseItem,
    ReviewDesk,
    ReviewEngine,
    ReviewReport,
    Scope,
    assess_infringement,
    review_case,
    review_dict,
    review_file,
    review_payload,
)

ReviewService = ReviewDesk
