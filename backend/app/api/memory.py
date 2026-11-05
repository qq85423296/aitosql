"""记忆路由（C1.6 / FR-C7）：总账管理 + 流水账只读 + 手动合并（`{merged, dropped}`）。

合并的 LLM 调用（route.ai）由本层注入 store（D2/D6：store 不 import llm）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.memory import MemoryCreateIn, MemoryPatchIn, MemoryToggleIn
from app.store import db
from app.store import memory as store_memory

router = APIRouter(tags=["memory"], dependencies=[TokenDep])

logger = logging.getLogger("aitosql.api.memory")


@router.get("/memory")
async def get_memory() -> dict:
    """总账：按 topic 分组。"""

    def _q() -> dict:
        with db.get_conn() as conn:
            grouped = store_memory.query_memory_grouped(conn)
            return {topic: [m.model_dump() for m in items] for topic, items in grouped.items()}

    return ok(await run(_q))


@router.post("/memory", status_code=201)
async def create_memory(payload: MemoryCreateIn) -> dict:
    def _c() -> int:
        with db.get_conn() as conn:
            return store_memory.save_memory(
                conn,
                scope=payload.scope,
                datasource_id=payload.datasource_id,
                topic=payload.topic,
                content=payload.content,
            )

    return ok({"id": await run(_c)}, "已创建")


@router.patch("/memory/{memory_id}")
async def patch_memory(memory_id: int, payload: MemoryPatchIn) -> dict:
    def _u() -> dict:
        with db.get_conn() as conn:
            return store_memory.update_memory(
                conn, memory_id, topic=payload.topic, content=payload.content
            ).model_dump()

    return ok(await run(_u))


@router.post("/memory/{memory_id}/toggle")
async def toggle_memory(memory_id: int, payload: MemoryToggleIn | None = None) -> dict:
    """停用即不注入（下次生成即生效）。

    P1-8：请求体可省略（或 `enabled` 缺省）→ 服务端读当前态取反；
    显式 `{enabled: bool}` 语义不变（幂等设定）。
    """

    def _t() -> dict:
        with db.get_conn() as conn:
            return store_memory.toggle_memory(
                conn,
                memory_id,
                None if payload is None else payload.enabled,
            ).model_dump()

    return ok(await run(_t))


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: int) -> dict:
    def _d() -> None:
        with db.get_conn() as conn:
            store_memory.delete_memory(conn, memory_id)

    await run(_d)
    return ok(None, "已删除")


@router.get("/memory/log")
async def memory_log(limit: int = 200) -> dict:
    """流水账只读（追加只写，从不修改）。"""

    def _q() -> list[dict]:
        with db.get_conn() as conn:
            return [m.model_dump() for m in store_memory.query_memory_log(conn, limit=limit)]

    return ok(await run(_q))


@router.post("/memory/consolidate")
async def consolidate_memory(force: bool = False) -> dict:
    """手动合并（水位线增量；返回 `{merged, dropped}`）。

    F-3 事务边界：读相（水位线裁决+取材）与写相（落产物+裁剪+推进）各自经 run()
    短连接完成；LLM 合并（route.ai）裸 await——**不持有 sqlite 连接跨网络往返**
    （单 worker 下会阻塞全部请求）。
    """
    from app.llm import providers as llm_providers
    from app.llm import registry as llm_registry
    from app.store import settings as store_settings

    def _prepare() -> tuple:
        with db.get_conn() as conn:
            min_gap = int(store_settings.get_setting(conn, "memory.consolidate_min_gap_min"))
            max_tokens = int(store_settings.get_setting(conn, "memory.max_tokens"))
            route_ref = str(store_settings.get_setting(conn, "route.ai") or "")
            prepared = store_memory.consolidate_prepare(conn, min_gap_min=min_gap, force=force)
            return prepared, min_gap, max_tokens, route_ref

    prepared, _min_gap, max_tokens, route_ref = await run(_prepare)
    if prepared is None:
        return ok({"merged": 0, "dropped": 0})
    if not route_ref:
        return ok({"merged": 0, "dropped": 0})  # 无 AI 路由：跳过（水位线不推进，下轮重试）

    def _resolve() -> tuple:
        with db.get_conn() as conn:
            return llm_registry.resolve_route(conn, route_ref)

    try:
        profile, model = await run(_resolve)
        logs, existing = prepared["new_logs"], prepared["existing"]
        facts = "\n".join(f"- [{lg.kind}] {lg.fact}" for lg in logs)
        topics = "\n".join(f"- [{m.topic}] {m.content}" for m in existing)
        outcome = await llm_providers.get_adapter(profile).chat(
            model,
            [
                {
                    "role": "user",
                    "content": (
                        "把以下新事实合并进记忆总账（输出 JSON 数组，元素含"
                        " scope/topic/content）：\n新事实：\n"
                        + facts
                        + "\n现有总账：\n"
                        + (topics or "（空）")
                    ),
                }
            ],
            max_tokens=2048,
        )
        import json as _json
        import re as _re

        m = _re.search(r"\[.*\]", outcome.text, _re.DOTALL)
        produced = _json.loads(m.group(0)) if m else []
        if not isinstance(produced, list):
            produced = []
    except Exception:
        logger.info("记忆合并 LLM 失败（静默，下轮重试）")
        return ok({"merged": 0, "dropped": 0})

    def _commit() -> dict:
        with db.get_conn() as conn:
            return store_memory.consolidate_commit(
                conn, prepared=prepared, produced=produced, max_tokens=max_tokens
            )

    return ok(await run(_commit))
