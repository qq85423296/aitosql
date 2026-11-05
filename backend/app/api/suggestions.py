"""建议队列路由（C1.3）：分组列表 / 批量采纳 / 忽略（采纳映射 C6.8，P5 人裁决）。

GET 响应包络冻结（修复回路 P1-6，回写 contracts.md C1.3）：
`{groups: {kind: [AiSuggestion...]}, total}`——pending 按 kind 分组；
单项含 `payload`（解析后对象）与 `payload_json`（原文串，前端评审展示用），
`confidence` 缺省回退 1.0（前端 toFixed 消费，不得为 null）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import TokenDep, ok, run
from app.store import db
from app.store import suggestions as store_suggestions
from app.store.models import AiSuggestion

router = APIRouter(tags=["suggestions"], dependencies=[TokenDep])


class IdsIn(BaseModel):
    ids: list[int] = Field(min_length=1)


class AcceptIn(BaseModel):
    """批量采纳 + 编辑后采纳（方案 v1.1 §7）：edits={建议id: 编辑文本}（comment 类生效）。"""

    ids: list[int] = Field(min_length=1)
    edits: dict[int, str] | None = None


def _present(s: AiSuggestion) -> dict:
    """建议项呈现模型（P1-6）：payload_json 原文串 + confidence 非空默认。"""
    d = s.model_dump()
    d["payload_json"] = json.dumps(s.payload, ensure_ascii=False)
    d["confidence"] = float(s.confidence) if s.confidence is not None else 1.0
    return d


def _suggestion_table(s: AiSuggestion) -> str | None:
    """建议关联表名（前端按表过滤用）：comment/std_bind 取 payload.table；relation 取 from 前缀。"""
    t = s.payload.get("table")
    if isinstance(t, str) and t:
        return t
    f = s.payload.get("from")
    if isinstance(f, str) and "." in f:
        return f.split(".", 1)[0]
    return None


@router.get("/suggestions")
async def list_suggestions(
    datasource_id: int | None = None, run_id: int | None = None, table: str | None = None
) -> dict:
    """建议队列：pending 按 kind 分组返回（前端 SuggestionDrawer 分组评审）。

    过滤（方案 v1.1 §8，向后兼容的可选参数）：run_id 按产出批次、table 按关联表。
    """

    def _q() -> dict:
        with db.get_conn() as conn:
            items = store_suggestions.query_suggestions(conn, datasource_id, status="pending", run_id=run_id)
            if table:
                items = [s for s in items if _suggestion_table(s) == table]
            grouped: dict[str, list[dict]] = {}
            for s in items:
                grouped.setdefault(s.kind, []).append(_present(s))
            return {"groups": grouped, "total": len(items)}

    return ok(await run(_q))


@router.post("/suggestions/accept")
async def accept_suggestions(payload: AcceptIn) -> dict:
    """批量采纳 `{ids}`：四类映射落目标表（C6.8 + C6.8+ 表级注释）；edits 编辑后采纳。"""

    def _a() -> dict:
        with db.get_conn() as conn:
            return store_suggestions.accept_suggestions(conn, payload.ids, payload.edits)

    return ok(await run(_a), "已采纳")


@router.post("/suggestions/ignore")
async def ignore_suggestions(payload: IdsIn) -> dict:
    def _i() -> dict:
        with db.get_conn() as conn:
            return store_suggestions.ignore_suggestions(conn, payload.ids)

    return ok(await run(_i), "已忽略")
