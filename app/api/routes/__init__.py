from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.batch import router as batch_router
from app.api.routes.costing import router as costing_router
from app.api.routes.extraction import router as extraction_router
from app.api.routes.system import router as system_router
from app.api.routes.uploads import router as uploads_router


router = APIRouter()
router.include_router(system_router)
router.include_router(uploads_router)
router.include_router(batch_router)
router.include_router(extraction_router)
router.include_router(costing_router)

__all__ = ["router"]
