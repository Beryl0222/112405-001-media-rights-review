"""Compatibility alias for rights-review oriented imports."""

from reviewdesk import *  # noqa: F401,F403
from reviewdesk import ReviewDesk as RightsReviewService  # noqa: F401
from reviewdesk import ReviewEngine, review_dict, review_file  # noqa: F401

RightsReviewDesk = ReviewDesk = RightsReviewService
