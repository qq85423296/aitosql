"""查询主链路路由（C1.5 / C2）：发起（幂等）/ 门裁决（CAS）/ 停止 / 存样例 / 导出。

- 幂等：同 client_request_id 重发 → 200 直返同 query_id 与 SSE 地址（不重跑）；
- 门裁决 CAS：仅 waiting_* 态受理，否则 409（E4090）；澄清裁决写 clarify_pref +
  同步直写 memory_log（D-14 入口 1），随后 Command(resume) 续跑（runner 内）；
- ~~POST /queries/:id/retry~~ 不新增（评审项 29 关闭：D03 自动续跑 + 前端重发覆盖）。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app import config
from app.api.deps import TokenDep, ok, run
from app.api.schemas.queries import QueryCreateIn, ResumeIn, SaveSampleIn, SteerIn
from app.errors import E_BAD_REQUEST, E_CONFLICT, E_NOT_FOUND, BizError
from app.graph import runner as graph_runner
from app.identifiers import require_id
from app.store import datasources as store_datasources
from app.store import db
from app.store import inbox as store_inbox
from app.store import queries as store_queries
from app.store import samples as store_samples
from app.store import sessions as store_sessions

router = APIRouter(tags=["queries"], dependencies=[TokenDep])


@router.post("/queries", status_code=201)
async def create_query(payload: QueryCreateIn) -> dict:
    """发起查询：幂等键查重 → 会话忙时排队 followup（P6）→ 落 query 行（running）→ 后台起图。

    P6 followup 语义（DSH inbox next-turn 队列）：会话存在活动查询时，新问题不并发
    起图，落 query_inbox(kind=followup) 锚定活动查询，待其终态后由 runner 自动开新轮。
    """
    payload.check_ids()

    existing = await run(_find, payload.client_request_id)
    if existing is not None:
        return ok(
            {
                "query_id": existing["id"],
                "status": existing["status"],
                "stream": f"/api/queries/{existing['id']}/stream",
            },
            "幂等命中：返回既有查询",
        )

    def _queue_followup() -> dict | None:
        """会话忙 → 排队；返回排队回执或 None（空闲可直跑）。"""
        with db.get_conn() as conn:
            anchor = store_queries.active_query_in_session(conn, payload.session_id)
            if anchor is None:
                return None
            store_datasources.get_datasource(conn, payload.datasource_id)
            inbox_id = store_inbox.add_inbox(
                conn,
                query_id=anchor,
                kind="followup",
                content=payload.question,
                datasource_id=payload.datasource_id,
            )
            return {"anchor": anchor, "inbox_id": inbox_id}

    queued = await run(_queue_followup)
    if queued is not None:
        return ok(
            {
                "query_id": None,
                "status": "queued",
                "queued": True,
                "inbox_id": queued["inbox_id"],
                "anchor_query_id": queued["anchor"],
                "stream": f"/api/queries/{queued['anchor']}/stream",
            },
            "会话忙：已排队，当前查询完成后自动发起",
        )

    def _create() -> dict | None:
        """落 query 行；并发同键（双击/重试风暴）PK 冲突 → 回查既有行（Q-5）。

        返回 None = 本次新建（调用方起图）；返回 dict = 竞态命中既有（不起图）。
        """
        with db.get_conn() as conn:
            ds = store_datasources.get_datasource(conn, payload.datasource_id)
            store_sessions.get_session(conn, payload.session_id)
            try:
                store_queries.save_query(
                    conn,
                    query_id=payload.client_request_id,
                    session_id=payload.session_id,
                    datasource_id=ds.id,
                    dialect=ds.dialect,
                    question=payload.question,
                )
            except sqlite3.IntegrityError:
                existing = store_queries.find_by_idempotent_key(conn, payload.client_request_id)
                if existing is None:
                    raise  # 非幂等键冲突（如 FK），照常抛
                return {"id": existing.id, "status": existing.status}
        return None

    raced = await run(_create)
    if raced is not None:
        return ok(
            {
                "query_id": raced["id"],
                "status": raced["status"],
                "stream": f"/api/queries/{raced['id']}/stream",
            },
            "幂等命中：返回既有查询",
        )
    await graph_runner.start_query(payload.client_request_id)
    return ok(
        {
            "query_id": payload.client_request_id,
            "status": "running",
            "stream": f"/api/queries/{payload.client_request_id}/stream",
        },
        "已受理",
    )


def _find(client_request_id: str) -> dict | None:
    with db.get_conn() as conn:
        q = store_queries.find_by_idempotent_key(conn, client_request_id)
        return {"id": q.id, "status": q.status} if q else None


@router.post("/queries/{query_id}/steer", status_code=201)
async def steer_query(query_id: str, payload: SteerIn) -> dict:
    """运行中引导/注入（P6，DSH steer/inject 语义）：仅活动查询受理，入 inbox 待图领取。

    steer：下一边界（generate/correct 重进时）把内容并入当轮工作问题；
    inject：补充上下文（不改写原始问题语义，节点边界同样生效）。
    已终态查询 409——请在会话中直接发新问题（followup）。
    """

    def _add() -> int:
        with db.get_conn() as conn:
            q = store_queries.get_query(conn, query_id)
            if q.status not in ("running", "waiting_clarify", "waiting_confirm"):
                raise BizError(E_CONFLICT, f"查询已终态（{q.status}），无法引导", 409)
            return store_inbox.add_inbox(
                conn, query_id=query_id, kind=payload.kind, content=payload.content
            )

    inbox_id = await run(_add)
    return ok({"inbox_id": inbox_id, "queued": True}, "已受理，将在下一步骤生效")


@router.post("/queries/{query_id}/resume")
async def resume_query(query_id: str, payload: ResumeIn) -> dict:
    """门裁决（CAS，D-05/D-22）：仅 waiting_* 受理；澄清写 clarify_pref + memory_log。"""

    def _cas() -> dict:
        with db.get_conn() as conn:
            q = store_queries.get_query(conn, query_id)
            if payload.gate == "clarify":
                if q.status != "waiting_clarify":
                    raise BizError(E_CONFLICT, f"当前状态 {q.status} 非 waiting_clarify，裁决被拒", 409)
                choice = payload.effective_choice()
                if not choice:
                    raise BizError(E_BAD_REQUEST, "clarify 裁决缺 choice/choices/custom", 422)
                ok_cas = store_queries.cas_status(conn, query_id, expect=("waiting_clarify",), to="running")
                if not ok_cas:
                    raise BizError(E_CONFLICT, "裁决竞态冲突（CAS 拒绝）", 409)
                # 澄清沉淀：clarify_pref UPSERT（下次免问）+ memory_log 同步直写
                store_sessions.save_clarify_pref(
                    conn,
                    datasource_id=require_id(q.datasource_id),
                    term=choice[:50],
                    chosen_rule=choice,
                )
                from app.store import memory as store_memory

                store_memory.flush_sync(
                    conn,
                    kind="caliber",
                    fact=f"「{choice[:50]}」口径={choice}（用户澄清门裁决）",
                    datasource_id=require_id(q.datasource_id),
                    source_query_id=query_id,
                )
                store_queries.update_query_result(conn, query_id, clarify_choice=choice)
                return {"resume": {"choice": choice}}
            # confirm 门
            if q.status != "waiting_confirm":
                raise BizError(E_CONFLICT, f"当前状态 {q.status} 非 waiting_confirm，裁决被拒", 409)
            if payload.approved is None:
                raise BizError(E_BAD_REQUEST, "confirm 裁决缺 approved", 422)
            ok_cas = store_queries.cas_status(conn, query_id, expect=("waiting_confirm",), to="running")
            if not ok_cas:
                raise BizError(E_CONFLICT, "裁决竞态冲突（CAS 拒绝）", 409)
            return {"resume": {"approved": bool(payload.approved)}}

    resume_value = await run(_cas)
    await graph_runner.resume_gate(query_id, payload.gate, resume_value["resume"])
    return ok({"query_id": query_id, "resumed": True}, "已裁决续跑")


@router.post("/queries/{query_id}/stop")
async def stop_query(query_id: str) -> dict:
    """停止（引擎 KILL；CH readonly 无 KILL 通道，响应注明）。

    幂等语义（修复回路 P1-2）：已终态（done/failed/stopped）不再 409，返回
    `200 {stopped: False, status}`——前端「先断流再 POST stop」时序下，
    stopped 事件不可达，前端以本响应 + E5 resync 兜底终态。
    """

    def _cas() -> dict:
        with db.get_conn() as conn:
            q = store_queries.get_query(conn, query_id)
            if q.status in store_queries.TERMINAL_STATUS:
                return {"already": True, "status": q.status}
            ok_cas = store_queries.cas_status(
                conn, query_id, expect=("running", "waiting_clarify", "waiting_confirm"), to="stopped"
            )
            if not ok_cas:
                # 竞态窗口内转了终态：按幂等返回当前态（不 409）
                cur = store_queries.get_query(conn, query_id)
                return {"already": True, "status": cur.status}
            return {"already": False}

    pre = await run(_cas)
    if pre["already"]:
        return ok(
            {"query_id": query_id, "stopped": False, "status": pre["status"]},
            f"查询已终态（{pre['status']}），幂等返回",
        )
    result = await graph_runner.stop_query(query_id)
    # P6：停止 = 会话空闲——清队（steer/inject 失效）并领取排队 followup 自动开轮
    await graph_runner.discard_pending_and_start_followup(query_id)
    return ok(
        {"query_id": query_id, "stopped": True, "kill": result},
        "已停止" if result.get("killed") else "已停止（引擎侧见 kill 说明）",
    )


@router.post("/queries/{query_id}/save-sample")
async def save_sample(query_id: str, payload: SaveSampleIn) -> dict:
    """存为样例：同步直写 memory_log（D-14 入口 1，三入口之「存样例」）。"""

    def _s() -> dict:
        with db.get_conn() as conn:
            q = store_queries.get_query(conn, query_id)
            question = payload.question or q.question
            sql = payload.sql or q.sql_final
            if not sql:
                raise BizError(E_BAD_REQUEST, "查询无终态 SQL 可存", 422)
            sample = store_samples.save_sample(
                conn, datasource_id=require_id(q.datasource_id), question=question, sql=sql, source="manual"
            )
            from app.store import memory as store_memory

            store_memory.flush_sync(
                conn,
                kind="preference",
                fact=f"样例沉淀：「{question[:30]}」",
                datasource_id=require_id(q.datasource_id),
                source_query_id=query_id,
            )
            return sample.model_dump()

    return ok(await run(_s), "已存为样例")


@router.get("/queries/{query_id}/export.csv")
async def export_csv(query_id: str) -> FileResponse:
    """全量落盘导出：FileResponse 流式；响应头回显 data_max_time（FR-S3）。"""

    def _load() -> tuple[str, str]:
        with db.get_conn() as conn:
            q = store_queries.get_query(conn, query_id)
            return q.status, (q.data_max_time or "")

    status, data_max_time = await run(_load)
    path = config.exports_dir() / f"{query_id}.csv"
    if status != "done" or not path.is_file():
        raise BizError(E_NOT_FOUND, "导出文件不存在（仅纯 SELECT 成功时落盘）", 404)
    return FileResponse(
        str(path),
        media_type="text/csv; charset=utf-8",
        filename=f"{query_id}.csv",
        headers={"X-Data-Max-Time": data_max_time},
    )


@router.get("/queries/{query_id}")
async def get_query(query_id: str) -> dict:
    """查询详情（补 C1.5 主表读取；前端轮询兜底路径）。"""

    def _q() -> dict:
        with db.get_conn() as conn:
            q = store_queries.get_query(conn, query_id)
            preview = store_queries.get_preview(conn, query_id)
            return {
                **q.model_dump(),
                "last_seq": store_queries.last_seq(conn, query_id),
                "preview": preview.model_dump() if preview else None,
            }

    return ok(await run(_q))
