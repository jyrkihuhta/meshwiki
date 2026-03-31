"""MeshWiki Agent Factory JSON API (v1)."""

from fastapi import APIRouter

from meshwiki.api.agents import router as agents_router
from meshwiki.api.pages import router as pages_router
from meshwiki.api.tasks import router as tasks_router

router = APIRouter(prefix="/api/v1")
router.include_router(pages_router, tags=["pages"])
router.include_router(tasks_router, tags=["tasks"])
router.include_router(agents_router, tags=["agents"])
