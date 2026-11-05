"""存活探针（T0.1 验收：GET /api/health → 200）。"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.api.deps import ok, run
from app.store import db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """存活 + 迁移完整性核对（17+ 表/视图建全；缺失名单随响应返回）。"""
    missing_count, missing = await run(db.count_expected_objects)
    return ok(
        {
            "status": "ok" if missing_count == 0 else "degraded",
            "version": __version__,
            "missing_objects": missing,
        }
    )
