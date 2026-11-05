"""会话路由（C1.1）：CRUD + E5 消息快照（装配五步第 1 步 hydrate，D-21）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.sessions import SessionCreateIn, SessionPatchIn
from app.store import db
from app.store import queries as store_queries
from app.store import sessions as store_sessions

router = APIRouter(tags=["sessions"], dependencies=[TokenDep])


@router.get("/sessions")
async def list_sessions() -> dict:
    def _q() -> list[dict]:
        with db.get_conn() as conn:
            return [s.model_dump() for s in store_sessions.query_sessions(conn)]

    return ok(await run(_q))


@router.post("/sessions", status_code=201)
async def create_session(payload: SessionCreateIn) -> dict:
    return ok(await run(_create, payload), "创建成功")


def _create(payload: SessionCreateIn) -> dict:
    with db.get_conn() as conn:
        s = store_sessions.save_session(conn, datasource_id=payload.datasource_id, title=payload.title)
        return s.model_dump()


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: str, payload: SessionPatchIn) -> dict:
    def _u() -> dict:
        with db.get_conn() as conn:
            s = store_sessions.update_session(
                conn,
                session_id,
                title=payload.title,
                pinned=None if payload.pinned is None else int(payload.pinned),
                archived=None if payload.archived is None else int(payload.archived),
            )
            return s.model_dump()

    return ok(await run(_u))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict:
    def _d() -> list[str]:
        with db.get_conn() as conn:
            return store_sessions.delete_session(conn, session_id)

    qids = await run(_d)
    if qids:
        from app.graph import runner as graph_runner

        await run(graph_runner.purge_checkpoints, qids)  # checkpoints 孤儿清理（审计 P1）
    return ok(None, "已删除")


@router.get("/sessions/{session_id}/queries")
async def session_queries(session_id: str) -> dict:
    """E5 快照：query+result_preview+query_event 聚合（含 last_seq / clarify_choice，
    不依赖 checkpoint；DAO 聚合防 N+1）。"""

    def _q() -> dict:
        with db.get_conn() as conn:
            store_sessions.get_session(conn, session_id)  # 404 闸门
            return store_queries.query_session_snapshot(conn, session_id)

    return ok(await run(_q))
