"""Compatibility alias for callers naming the release desk directly."""

from reviewdesk import *  # noqa: F401,F403
from reviewdesk import ReviewDesk as ReleaseDesk  # noqa: F401
from reviewdesk import ReviewEngine, review_dict, review_file  # noqa: F401

ReleaseReviewer = ReviewDesk
