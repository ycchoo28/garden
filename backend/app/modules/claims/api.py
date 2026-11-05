"""HTTP endpoints of the claims module, mounted from its drafting and review features.

The review routes come first: their "/queue" route must match before the drafting "/{claim_id}"
route, or "queue" would be read as a claim id. The prefix is given at include time, because the
drafting list and create routes have an empty path, which FastAPI allows only with a prefix.
"""

from fastapi import APIRouter

from app.modules.claims.drafting.api import router as drafting_router
from app.modules.claims.review.api import router as review_router

router = APIRouter()
router.include_router(review_router, prefix="/api/claims", tags=["claims"])
router.include_router(drafting_router, prefix="/api/claims", tags=["claims"])
