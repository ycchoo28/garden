"""HTTP endpoints of the budgets module. Each feature keeps its own router, included here."""

from fastapi import APIRouter

from app.modules.budgets.lines.api import router as lines_router
from app.modules.budgets.months.api import router as months_router
from app.modules.budgets.variance.api import router as variance_router

router = APIRouter(prefix="/api/budgets", tags=["budgets"])
router.include_router(lines_router)
router.include_router(months_router)
router.include_router(variance_router)
