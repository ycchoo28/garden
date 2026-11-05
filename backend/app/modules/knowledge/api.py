"""HTTP endpoints of the knowledge module, mounted from its three routed features.

Each feature defines its routes without a prefix. This module sets the prefix and tags once.
"""

from fastapi import APIRouter

from app.modules.knowledge.batches.api import router as batches_router
from app.modules.knowledge.collections.api import router as collections_router
from app.modules.knowledge.search.api import router as search_router

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
router.include_router(collections_router)
router.include_router(batches_router)
router.include_router(search_router)
