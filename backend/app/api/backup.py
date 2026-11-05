"""备份路由（C1.6 / FR-C8）：立即备份 + 列表（含恢复语义提示）。

恢复语义 ≠ 重启（SSOT §12.3）：恢复后 running 一律 failed+标注，不适用 D03 自动续跑；
提示文案随列表返回（「恢复后进行中的查询一律标记失败，不适用自动续跑」）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.store import backup as store_backup

router = APIRouter(tags=["backup"], dependencies=[TokenDep])


@router.post("/backup")
async def create_backup() -> dict:
    """立即备份：VACUUM INTO → 密文剥离 → 可选加密 → 滚动 keep=10。"""
    path = await run(store_backup.create_backup)
    return ok({"file": path.name}, "备份完成")


@router.get("/backup/list")
async def list_backups() -> dict:
    items = await run(store_backup.list_backups)
    return ok({"items": items, "restore_hint": store_backup.RESTORE_HINT})
