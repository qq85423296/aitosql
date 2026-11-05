"""图运行器（design D-31/D-01/D-06，BR-10）。

职责：编译图（StateGraph + AsyncSqliteSaver，checkpoints.db 独立文件）+
事件先落库再推送（query_event → SSE 桥接）+ 双库写入顺序执行 +
D03 启动清扫自动续跑 + stop/KILL 协调。

D-01 写入顺序实现注记（编码期定型，回写 design 段 5）：
节点内的终态/门态 app.db 写入（persist_fn）与事件落库（emit）发生在 superstep 内，
langgraph 的 checkpoint 提交发生在 superstep 边界——天然满足「app.db 先提交」；
runner 收尾仅做幂等 reconciliation 写入。
R-1 注：interrupt/Command(resume) API 形态按 langgraph ≥0.2 官方语义实现（V-5 spike 校准点）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app import config
from app.errors import E_FIX_EXHAUSTED, E_MODEL_DOWN, E_RECOVERY, BizError
from app.graph import edges
from app.graph.context import NodeContext
from app.graph.nodes import NODE_FUNCS
from app.graph.state import QueryState
from app.identifiers import require_id
from app.sqlcheck import clean as sql_clean
from app.sqlcheck import rules as sql_rules
from app.store import datasources as store_datasources
from app.store import db as store_db
from app.store import inbox as store_inbox
from app.store import queries as store_queries
from app.store import sessions as store_sessions
from app.store import settings as store_settings

logger = logging.getLogger("aitosql.graph.runner")

_saver: Any = None
_saver_cm: Any = None
_hub: dict[str, set[asyncio.Queue]] = {}  # query_id -> 订阅队列集（SSE 桥）
_tasks: dict[str, asyncio.Task] = {}  # query_id -> 运行任务


# ---------- 生命周期 ----------


async def init_runner() -> None:
    """初始化 checkpointer（AsyncSqliteSaver，checkpoints.db 进度权威，不入备份）。"""
    global _saver, _saver_cm
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    path = str(config.checkpoints_db_path())
    cm = AsyncSqliteSaver.from_conn_string(path)
    _saver_cm = cm
    _saver = await cm.__aenter__() if hasattr(cm, "__aenter__") else cm
    logger.info("checkpointer 就绪: %s", path)


async def shutdown_runner() -> None:
    global _saver, _saver_cm
    for task in list(_tasks.values()):
        task.cancel()
    _tasks.clear()
    if _saver_cm is not None and hasattr(_saver_cm, "__aexit__"):
        await _saver_cm.__aexit__(None, None, None)
    _saver = None
    _saver_cm = None


def purge_checkpoints(thread_ids: list[str]) -> int:
    """按 thread_id(=query_id) 清理 checkpoints.db 孤儿（删会话/查询后调用）。
    独立短连接直删 checkpoints/writes；失败仅告警（孤儿数据惰性无害，代码审计 P1）。"""
    import sqlite3

    ids = [str(t) for t in thread_ids if t]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    conn = None
    try:
        conn = sqlite3.connect(str(config.checkpoints_db_path()), timeout=5)
        cur = conn.execute(f"DELETE FROM checkpoints WHERE thread_id IN ({marks})", ids)
        n = cur.rowcount
        conn.execute(f"DELETE FROM writes WHERE thread_id IN ({marks})", ids)
        conn.commit()
        return n
    except Exception as exc:
        logger.warning("purge_checkpoints 失败（跳过）: %s", type(exc).__name__)
        return 0
    finally:
        if conn is not None:
            conn.close()


# ---------- 图编译 ----------


def build_graph(ctx: NodeContext) -> Any:
    """按查询编译图（NodeContext 以 partial 绑定；checkpointer 进程级共享）。"""
    graph = StateGraph(QueryState)
    for name, fn in NODE_FUNCS.items():
        graph.add_node(name, partial(fn, ctx=ctx))
    max_rounds = int(ctx.settings.get("query.max_fix_rounds") or 3)
    clarify_on = bool(ctx.settings.get("feature.clarify_gate"))
    write_confirm_on = bool(ctx.settings.get("query.write_confirm"))

    graph.add_edge(START, "supply_schema")
    graph.add_edge("supply_schema", "generate")
    graph.add_conditional_edges(
        "generate",
        partial(edges.after_generate, clarify_gate_on=clarify_on),
        {"supply_schema": "supply_schema", "clarify_gate": "clarify_gate", "validate": "validate", END: END},
    )
    graph.add_edge("clarify_gate", "generate")
    graph.add_conditional_edges(
        "validate",
        partial(edges.after_validate, write_confirm_on=write_confirm_on, max_rounds=max_rounds),
        {"correct": "correct", "confirm_gate": "confirm_gate", "execute": "execute", END: END},
    )
    graph.add_conditional_edges("confirm_gate", edges.after_confirm_gate, {"execute": "execute", END: END})
    graph.add_conditional_edges(
        "execute",
        partial(edges.after_execute, max_rounds=max_rounds),
        {"correct": "correct", "verify_result": "verify_result", END: END},
    )
    graph.add_conditional_edges(
        "verify_result", partial(edges.after_verify, max_rounds=max_rounds), {"correct": "correct", END: END}
    )
    graph.add_conditional_edges("correct", edges.after_correct, {"validate": "validate", END: END})
    return graph.compile(checkpointer=_saver)


# ---------- 事件桥（先落库再推送，D-06） ----------


def subscribe(query_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _hub.setdefault(query_id, set()).add(q)
    return q


def unsubscribe(query_id: str, q: asyncio.Queue) -> None:
    subs = _hub.get(query_id)
    if subs is None:
        return
    subs.discard(q)
    if not subs:
        del _hub[query_id]  # Q-16/F-5：空集合条目即清，长跑实例内存不缓涨


def _publish(query_id: str, frame: dict[str, Any]) -> None:
    for q in list(_hub.get(query_id, set())):
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            logger.warning("SSE 订阅队列溢出，丢弃最旧帧: %s", query_id)
            try:
                q.get_nowait()
                q.put_nowait(frame)
            except asyncio.QueueEmpty:
                pass


def is_terminal(query_id: str) -> bool:
    task = _tasks.get(query_id)
    return task is None or task.done()


# ---------- NodeContext 装配 ----------


def _make_emit(query_id: str, db_path: Path | None, max_payload_kb: int) -> Callable[..., Awaitable[int]]:
    async def emit(event_type: str, payload: dict[str, Any], persist: bool = True) -> int:
        seq = 0
        if persist:
            # reasoning_full 是思考全文（GLM 级深度推理实测可达 ~1MB UTF-8），给 4MB 独立上限，
            # 防止 16KB 通用截断把思考正文截成 preview 标记（前端轨迹回放依赖完整 text）
            cap_kb = 4096 if event_type == "reasoning_full" else max_payload_kb

            def _append() -> int:
                with store_db.get_conn(db_path) as conn:
                    return store_queries.append_event(
                        conn,
                        query_id=query_id,
                        event_type=event_type,
                        payload=payload,
                        max_payload_kb=cap_kb,
                    )

            seq = await anyio.to_thread.run_sync(_append)
        _publish(query_id, {"seq": seq, "type": event_type, "payload": payload})
        return seq

    return emit


def _make_persist(query_id: str, db_path: Path | None) -> Callable[..., Awaitable[None]]:
    async def persist_fn(**fields: Any) -> None:
        def _update() -> None:
            with store_db.get_conn(db_path) as conn:
                store_queries.update_query_result(conn, query_id, **fields)

        await anyio.to_thread.run_sync(_update)

    return persist_fn


def _make_chat(db_path: Path | None, query_id: str | None = None) -> Callable[..., Awaitable[Any]]:
    async def chat_fn(
        route_ref: str, messages: list[dict[str, str]], max_tokens: int = 4096, on_delta: Any = None
    ) -> Any:
        from app.llm import providers as llm_providers
        from app.llm import registry as llm_registry
        from app.llm import retry as llm_retry

        def _resolve() -> tuple[Any, str, Any, float]:
            with store_db.get_conn(db_path) as conn:
                profile, model = llm_registry.resolve_route(conn, route_ref)
                settings_map = store_settings.get_settings_map(conn)
                policy = llm_retry.policy_from_settings(settings_map)
                timeout_s = float(settings_map.get("llm.timeout_s") or 900)
                return profile, model, policy, timeout_s

        profile, model, policy, timeout_s = await anyio.to_thread.run_sync(_resolve)
        adapter = llm_providers.get_adapter(profile)

        # EMPTY_RESPONSE（finish=length，思考吃满输出预算）自动上调 max_tokens 重试：
        # 逐级翻倍直至产出正文（32768 → 65536 → 131072 封顶；网关实测接受 131072）。
        # 这是思考型模型（glm-5.3 等）的确定性恢复手段，优先于通用重试策略；
        # 每次上调落 llm_retry 事件（轨迹可见）。
        _max_out_cap = 131072
        budget = max_tokens

        async def _once() -> Any:
            nonlocal budget
            while True:
                try:
                    return await adapter.chat(
                        model, messages, max_tokens=budget, on_delta=on_delta, timeout_s=timeout_s
                    )
                except BizError as exc:
                    data = exc.data or {}
                    if data.get("failure_class") != "EMPTY_RESPONSE" or budget >= _max_out_cap:
                        raise
                    prev = budget
                    budget = min(budget * 2, _max_out_cap)
                    logger.info(
                        "EMPTY_RESPONSE（思考占满输出）：max_tokens %d → %d 自动上调重试", prev, budget
                    )
                    await _on_retry(0, 0, "EMPTY_RESPONSE")

        # 先落事件后等待（dsh-llm-retry 语义）：崩溃不留隐形重试
        async def _on_retry(attempt: int, delay_ms: int, failure_class: str) -> None:
            if query_id is None:
                return
            emit = _make_emit(query_id, db_path, 16)
            await emit(
                "llm_retry",
                {
                    "type": "llm_retry",
                    "attempt": attempt,
                    "delay_ms": delay_ms,
                    "failure_class": failure_class,
                    "route": route_ref,
                },
                True,
            )

        return await llm_retry.chat_with_retry(_once, policy=policy, on_retry=_on_retry)

    return chat_fn


async def build_context(query_id: str) -> NodeContext:
    """从 app.db 装配查询上下文（设置快照 + 数据源快照 + 解密密码仅内存）。"""
    from app.llm import secrets as llm_secrets

    def _load() -> tuple[Any, Any, dict[str, Any], str | None]:
        with store_db.get_conn() as conn:
            query = store_queries.get_query(conn, query_id)
            ds_row = store_datasources.get_datasource_row(conn, require_id(query.datasource_id))
            settings_map = store_settings.get_settings_map(conn)
            password = llm_secrets.decrypt_text(ds_row["password_enc"]) if ds_row["password_enc"] else None
            ds = {k: v for k, v in store_db.row_to_dict(ds_row).items() if k != "password_enc"}
            return query, ds, settings_map, password

    _query, ds, settings_map, password = await anyio.to_thread.run_sync(_load)
    return NodeContext(
        query_id=query_id,
        emit=_make_emit(query_id, None, int(settings_map.get("sse.event_payload_kb") or 16)),
        chat_fn=_make_chat(None, query_id=query_id),
        persist_fn=_make_persist(query_id, None),
        settings=settings_map,
        datasource=ds,
        password=password,
    )


# ---------- 发起 / 裁决 / 停止 ----------


async def start_query(query_id: str) -> None:
    """发起查询主链路（api 已落 query 行 running；幂等查重在 api 层完成）。"""
    ctx = await build_context(query_id)
    initial: QueryState = {
        "query_id": query_id,
        "thread_id": query_id,
        "session_id": ctx.settings.get("_session_id", ""),
        "datasource_id": ctx.datasource["id"],
        "dialect": ctx.datasource["dialect"],
        "ds_mode": ctx.datasource["mode"],
        "question": "",
        "round": 0,
        "tokens": 0,
        "resupply_used": False,
        "verify_fallback_used": False,
        "fingerprints": [],
        "error_history": [],
        "write_confirmed": False,
        "status": "running",
    }

    def _fill() -> QueryState:
        with store_db.get_conn() as conn:
            q = store_queries.get_query(conn, query_id)
            initial["question"] = q.question
            initial["session_id"] = q.session_id or ""
            return initial

    state = await anyio.to_thread.run_sync(_fill)
    task = asyncio.create_task(_run(query_id, state, ctx))
    _tasks[query_id] = task


async def resume_gate(query_id: str, gate: str, value: dict[str, Any]) -> None:
    """门裁决续跑（CAS 已在 api 层完成）；Command(resume=...) 恢复图。"""
    ctx = await build_context(query_id)
    graph = build_graph(ctx)
    config_ = {"configurable": {"thread_id": query_id}}
    task = asyncio.create_task(_run(query_id, Command(resume=value), ctx, graph=graph, config_=config_))
    _tasks[query_id] = task


async def stop_query(query_id: str) -> dict[str, Any]:
    """停止：任务取消 + 引擎 KILL（CH 无通道仅断连，SSOT §5.4-4）。

    Q-4：终态落库改 CAS（expect 运行/门态）——不再无条件覆盖：竞态窗口内已
    done/failed 的查询不被「回退」为 stopped；api 层已 CAS 时此处为幂等 no-op。
    """
    from app.exec import runner as exec_runner
    from app.llm import secrets as llm_secrets

    task = _tasks.get(query_id)
    if task and not task.done():
        task.cancel()

    def _kill() -> dict[str, Any]:
        with store_db.get_conn() as conn:
            query = store_queries.get_query(conn, query_id)
            ds_row = store_datasources.get_datasource_row(conn, require_id(query.datasource_id))
            password = llm_secrets.decrypt_text(ds_row["password_enc"]) if ds_row["password_enc"] else None
        return exec_runner.kill_query(query_id, password=password)

    result = await anyio.to_thread.run_sync(_kill)
    ctx_emit = _make_emit(query_id, None, 16)
    await ctx_emit("stopped", {"type": "stopped", "reason": "user_stop"}, True)

    def _mark() -> None:
        with store_db.get_conn() as conn:
            store_queries.cas_status(
                conn, query_id, expect=("running", "waiting_clarify", "waiting_confirm"), to="stopped"
            )

    await anyio.to_thread.run_sync(_mark)
    return result


# ---------- 主执行 ----------


async def _run(
    query_id: str, invocation: Any, ctx: NodeContext, graph: Any = None, config_: dict | None = None
) -> None:
    """运行/续跑图并收尾：终态落库（幂等 reconciliation）→ meta pre_done → done。

    Q-3：泛化异常按类型映射错误码（BizError 透传原码 / ParseError→E3002 /
    超时族→E2001 / 其余→E5000），不再一律误报 E2001「模型端点无响应」。
    Q-16/F-5：任务登记槽位在 finally 统一回收（仅当槽位仍为本任务，
    防 stop→重发竞态误删新任务）。
    """
    graph = graph or build_graph(ctx)
    config_ = config_ or {"configurable": {"thread_id": query_id}}
    try:
        try:
            result = await graph.ainvoke(invocation, config_)
        except asyncio.CancelledError:
            raise  # E12：节点内不吞取消，取消传播即停管线
        except BizError as exc:
            await _fail(query_id, ctx, exc.code, exc.message)
            return
        except Exception as exc:
            await _fail(query_id, ctx, sql_clean.classify_unexpected(exc), sql_clean.mask_error(str(exc)))
            return
        if result is None:
            return
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        if interrupts:
            # 门悬停：状态/门事件已由节点持久化（waiting_*，门不自动放行）
            logger.info("查询 %s 悬停于门: %s", query_id, interrupts[0].value.get("type"))
            return
        await _finalize(query_id, ctx, result if isinstance(result, dict) else {})
    finally:
        if _tasks.get(query_id) is asyncio.current_task():
            _tasks.pop(query_id, None)


async def _fail(query_id: str, ctx: NodeContext, code: str, message: str) -> None:
    await ctx.emit(
        "error", {"type": "error", "code": code, "message": message, "retryable": code == E_MODEL_DOWN}, True
    )
    await ctx.persist_fn(status="failed")
    await ctx.emit(
        "done", {"type": "done", "query_id": query_id, "status": "failed", "unverified": False}, True
    )
    # P6：失败同样让会话空闲——清队并领取排队 followup（DSH idle→next-turn 语义）
    await _discard_pending_and_start_followup(query_id)


async def _finalize(query_id: str, ctx: NodeContext, result: dict[str, Any]) -> None:
    """终态 reconciliation：app.db 单事务写入（status/sql/contract/meta/tokens）。

    P1-14（修复回路）：残留 running/waiting_* 不再一律映射 done——
    `needs_correct` 残留 = 「修正轮数耗尽/无剩余额度」（validate/execute/verify
    的 END 分支），映射 **failed** 并先落 error 事件（E2004，前端错误卡），
    杜绝「错误结果当成功交付」；无修正残留者（正常成功 / 离线 generate_only /
    写操作离线确认）仍映射 done。
    """
    status = str(result.get("status") or "done")
    exhausted = bool(result.get("needs_correct"))
    if status in ("running", "waiting_clarify", "waiting_confirm"):
        status = "failed" if exhausted else "done"
    exec_meta = result.get("exec_meta") or {}
    await ctx.persist_fn(
        status=status,
        sql_final=result.get("sql"),
        contract=result.get("contract"),
        rounds=result.get("round"),
        tokens=result.get("tokens"),
        scan_rows=exec_meta.get("scan_rows"),
        data_max_time=exec_meta.get("data_max_time"),
        duration_ms=exec_meta.get("duration_ms"),
        result_unverified=int(bool(result.get("result_unverified"))),
        clarify_choice=result.get("clarify_choice"),
    )
    if status == "failed" and exhausted:
        # 错误事件先于 done（终态事件最后发，顺序铁律）
        await ctx.emit(
            "error",
            {
                "type": "error",
                "code": E_FIX_EXHAUSTED,
                "message": "修正轮数耗尽，未得出可交付结果（最近一版 SQL 已保留，可调整后重新提问）",
                "retryable": False,
            },
            True,
        )
    # meta pre_done（C3.2 两次全字段之第二次）
    await ctx.emit(
        "meta",
        {
            "type": "meta",
            "phase": "pre_done",
            "hit_tables": result.get("hit_tables") or [],
            "route": {
                "generate": str(ctx.settings.get("route.generate") or ""),
                "fix": str(ctx.settings.get("route.fix") or ""),
            },
            "tokens": int(result.get("tokens") or 0),
        },
        True,
    )
    if status == "stopped":
        # F8：确认门取消的终态帧是 stopped（confirm_gate 节点已发出）——
        # 再发 done{status:'stopped'} 越出冻结契约（done.status ∈ done|failed），
        # 前端会把「已停止」误显为「已失败」（且随 SSE 分块时序抛硬币）。跳过。
        # P6：终态仍须清队 + 领取排队 followup。
        await _discard_pending_and_start_followup(query_id)
        return
    await ctx.emit(
        "done",
        {
            "type": "done",
            "query_id": query_id,
            "status": status,
            "unverified": bool(result.get("result_unverified")),
        },
        True,
    )
    if status == "done":
        await _post_done(query_id, result)
    # P6：终态清空未领取的 steer/inject（已无从生效）+ 自动开排队的 followup
    await _discard_pending_and_start_followup(query_id)


async def discard_pending_and_start_followup(query_id: str) -> None:
    """终态队列收尾（公开给 stop API 等不经 _finalize 的终态路径调用）。"""
    await _discard_pending_and_start_followup(query_id)


async def _discard_pending_and_start_followup(query_id: str) -> None:
    """终态队列收尾：丢弃未领取 steer/inject，随后自动开最早 followup。"""

    def _discard() -> None:
        with store_db.get_conn() as conn:
            conn.execute(
                "UPDATE query_inbox SET status = 'discarded'"
                " WHERE query_id = ? AND status = 'pending' AND kind IN ('steer', 'inject')",
                (query_id,),
            )

    await anyio.to_thread.run_sync(_discard)
    await _start_followup_if_any(query_id)


async def _start_followup_if_any(query_id: str) -> None:
    """当前查询终态后自动开最早排队 followup（P6，DSH next-turn 队列语义）。

    - 领取 oldest pending followup → 以新 query_id 落 query 行并起图；
    - 其余 pending 输入重锚到新查询（队列迁移，防老锚点悬空）；
    - 向老锚点流发 followup_started 事件（前端据此切换流）；
    - 全程静默失败仅记日志（队列不阻断主链路）。
    """
    try:

        def _pop() -> tuple[str, int] | None:
            from app.ids import new_query_id

            with store_db.get_conn() as conn:
                item = store_inbox.oldest_pending_followup(conn, query_id)
                if item is None:
                    return None
                old = store_queries.get_query(conn, query_id)
                ds_id = item.get("datasource_id") or old.datasource_id
                ds = store_datasources.get_datasource(conn, require_id(ds_id))
                new_id = new_query_id()
                store_queries.save_query(
                    conn,
                    query_id=new_id,
                    session_id=str(old.session_id),
                    datasource_id=ds.id,
                    dialect=ds.dialect,
                    question=str(item["content"]),
                )
                store_inbox.claim_inbox(conn, [int(item["id"])])
                # 队列迁移：老锚点其余 pending 输入重锚到新查询
                conn.execute(
                    "UPDATE query_inbox SET query_id = ? WHERE query_id = ? AND status = 'pending'",
                    (new_id, query_id),
                )
                seq = store_queries.append_event(
                    conn,
                    query_id=query_id,
                    event_type="followup_started",
                    payload={"type": "followup_started", "query_id": new_id},
                )
                return new_id, seq

        popped = await anyio.to_thread.run_sync(_pop)
        if popped is None:
            return
        new_id, seq = popped
        _publish(
            query_id,
            {
                "seq": seq,
                "type": "followup_started",
                "payload": {"type": "followup_started", "query_id": new_id},
            },
        )
        logger.info("followup 自动开轮: %s → %s", query_id, new_id)
        await start_query(new_id)
    except Exception:
        logger.exception("followup 自动开轮失败: %s", query_id)


async def _post_done(query_id: str, result: dict[str, Any]) -> None:
    """done 后：首问自动标题（C6.7）+ 记忆异步 flush（D-14，失败静默下轮重试）。"""

    def _title() -> tuple[str, str] | None:
        with store_db.get_conn() as conn:
            q = store_queries.get_query(conn, query_id)
            if q.session_id:
                store_sessions.maybe_auto_title(conn, q.session_id, q.question)
                store_sessions.touch_session(conn, q.session_id)
                return q.session_id, q.question
        return None

    info = await anyio.to_thread.run_sync(_title)
    if info is None:
        return
    session_id, question = info
    flush_task = asyncio.create_task(_flush_async(query_id, question, result))
    _tasks[f"_flush:{query_id}"] = flush_task  # RUF006：持有引用防 GC 取消
    flush_task.add_done_callback(lambda _t: _tasks.pop(f"_flush:{query_id}", None))
    # R-2：会话压缩接线——done 后按阈值触发（route.ai 未配置/未超阈值/抢救失败则跳过）
    compact_task = asyncio.create_task(_maybe_compact(session_id))
    _tasks[f"_compact:{session_id}"] = compact_task
    compact_task.add_done_callback(lambda _t: _tasks.pop(f"_compact:{session_id}", None))


async def _flush_async(query_id: str, question: str, result: dict[str, Any]) -> None:
    """异步 flush 流水账（route.ai 提取新事实；失败静默，不阻断对话，E16）。"""
    try:
        from app.store import memory as store_memory

        def _route() -> str:
            with store_db.get_conn() as conn:
                return str(store_settings.get_setting(conn, "route.ai") or "")

        route_ref = await anyio.to_thread.run_sync(_route)
        if not route_ref:
            return
        assumptions = result.get("assumptions") or []
        if not assumptions:
            return
        facts = [
            f"查询「{question[:30]}」口径假设：{a.get('text', '')}"
            for a in assumptions
            if a.get("impact") == "low"
        ]
        if not facts:
            return

        def _write() -> None:
            with store_db.get_conn() as conn:
                q = store_queries.get_query(conn, query_id)
                for fact in facts:
                    store_memory.flush_sync(
                        conn,
                        kind="caliber",
                        fact=fact,
                        datasource_id=q.datasource_id,
                        source_query_id=query_id,
                    )

        await anyio.to_thread.run_sync(_write)
    except Exception:
        logger.info("记忆异步 flush 失败（静默）: %s", query_id)


async def _maybe_compact(session_id: str) -> None:
    """会话压缩接线（R-2，SSOT §12.6）：done 后触发，双阈值（P1，借鉴 dsh-compaction-basic）：
    消息数 > `session.compact_msgs` **或** 尾部 token 压力 ≥ `session.compact_pressure_ratio`%
    × 模型上下文（app.tokens 启发式估算，与信封预算同一估算器）。

    - 先抢救 flush（待压缩轮次 low 假设 → memory_log，按 (source_query_id, fact)
      判重防重复直写）；**flush 失败压缩推迟**（spec §5.3 强制约束）；
    - 摘要走 route.ai（未配置则跳过压缩，不阻断对话）；保留最近 `session.keep_msgs`
      轮完整；滚动摘要链写 session.summary / summary_upto（GET /sessions 与 E5 快照
      透出，前端 Banners 压缩提示条数据源）。
    - 摘要请求带稳定 system 前缀（P2 热前缀复用：route.ai 与生成同路由时共享 KV 前缀）。
    - 全程静默：任何异常仅记日志。
    """
    try:
        from app import tokens as app_tokens
        from app.llm import prompts as llm_prompts
        from app.store import memory as store_memory

        def _read() -> tuple[Any, int, int, int, str, bool, str]:
            """返回 (session, msg_count, compact_msgs, keep_msgs, route_ai, pressure, sys_prompt)。"""
            with store_db.get_conn() as conn:
                s = store_sessions.get_session(conn, session_id)
                cnt = int(
                    conn.execute("SELECT COUNT(*) FROM query WHERE session_id = ?", (session_id,)).fetchone()[
                        0
                    ]
                )
                compact_msgs = int(store_settings.get_setting(conn, "session.compact_msgs") or 40)
                keep_msgs = int(store_settings.get_setting(conn, "session.keep_msgs") or 6)
                route_ai = str(store_settings.get_setting(conn, "route.ai") or "")
                ratio = int(store_settings.get_setting(conn, "session.compact_pressure_ratio") or 80)
                # token 压力：全部轮次（question+sql_final）+ 既有摘要的启发式估算
                rows = conn.execute(
                    "SELECT question, sql_final FROM query WHERE session_id = ?", (session_id,)
                ).fetchall()
                tail_tokens = sum(
                    app_tokens.estimate_tokens(f"{r['question'] or ''}\n{r['sql_final'] or ''}")
                    for r in rows
                ) + app_tokens.estimate_tokens(s.summary or "")
                pressure = tail_tokens >= llm_prompts.DEFAULT_CTX * ratio // 100
                # P2 热前缀：与生成同源的系统提示词（按会话数据源方言渲染）
                sys_prompt = ""
                if s.datasource_id is not None:
                    ds_row = store_datasources.get_datasource_row(conn, int(s.datasource_id))
                    sys_prompt = llm_prompts.render_system_prompt(
                        str(store_settings.get_setting(conn, "prompt.system")),
                        str(ds_row["dialect"]),
                    )
                return s, cnt, compact_msgs, keep_msgs, route_ai, pressure, sys_prompt

        (
            session,
            msg_count,
            compact_msgs,
            keep_msgs,
            route_ai,
            pressure_triggered,
            sys_prompt,
        ) = await anyio.to_thread.run_sync(_read)
        if not route_ai:
            return  # 无 AI 路由无法生成摘要 → 跳过（压缩不阻断对话）
        summary_upto = session.summary_upto or ""
        chat_fn = _make_chat(None)

        def _pending_rows(conn: Any, n: int) -> list[dict[str, Any]]:
            """待压缩轮次（最旧 n 条；二次压缩时仅取 summary_upto 之后的增量）。"""
            rows = conn.execute(
                "SELECT id, question, sql_final, datasource_id, contract_json,"
                " created_at FROM query WHERE session_id = ?"
                " ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
            if summary_upto:
                upto = conn.execute("SELECT created_at FROM query WHERE id = ?", (summary_upto,)).fetchone()
                if upto is not None:
                    rows = [
                        r for r in rows if (r["created_at"], r["id"]) > (upto["created_at"], summary_upto)
                    ]
            import json as _json

            return [
                {
                    "query_id": r["id"],
                    "question": r["question"],
                    "sql_final": r["sql_final"],
                    "datasource_id": r["datasource_id"],
                    "assumptions": (_json.loads(r["contract_json"]) or {}).get("assumptions")
                    if r["contract_json"]
                    else [],
                }
                for r in rows[: max(n, 0)]
            ]

        async def _flush_fn() -> bool:
            def _f() -> bool:
                try:
                    with store_db.get_conn() as conn:
                        pending = _pending_rows(conn, msg_count - keep_msgs)
                        for row in pending:
                            for a in row["assumptions"] or []:
                                if a.get("impact") != "low":
                                    continue
                                fact = f"查询「{str(row['question'])[:30]}」口径假设：{a.get('text', '')}"
                                dup = conn.execute(
                                    "SELECT 1 FROM memory_log WHERE source_query_id = ? AND fact = ? LIMIT 1",
                                    (row["query_id"], fact),
                                ).fetchone()
                                if dup is None:
                                    store_memory.flush_sync(
                                        conn,
                                        kind="caliber",
                                        fact=fact,
                                        datasource_id=row["datasource_id"],
                                        source_query_id=row["query_id"],
                                    )
                    return True
                except Exception:
                    logger.info("压缩抢救 flush 失败（压缩推迟）: %s", session_id)
                    return False

            return await anyio.to_thread.run_sync(_f)

        async def _summarize(prev: str | None, pending: list[dict[str, Any]]) -> str:
            rounds_text = "\n".join(f"Q: {p['question']}\nSQL: {p.get('sql_final') or ''}" for p in pending)
            user = (
                "请把以下历史查询轮次并入滚动摘要（保留口径/假设/结论，"
                "输出 200 字内纯文本）：\n既有摘要："
                f"{prev or '（无）'}\n新增轮次：\n{rounds_text}"
            )
            # P2 热前缀：稳定 system 段在前，与生成请求共享前缀（同路由时命中 KV cache）
            messages = (
                [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]
                if sys_prompt
                else [{"role": "user", "content": user}]
            )
            outcome = await chat_fn(route_ai, messages, 1024)
            return str(getattr(outcome, "text", "")).strip() or (prev or "")

        with store_db.get_conn() as conn:
            # sqlite3 默认延迟事务：await 期间本连接仅 SELECT，不持写锁；
            # 唯一写入（save_summary）发生在 LLM 返回之后（F-3 边界）
            result = await store_memory.compact_session(
                conn,
                session_id,
                msg_count=msg_count,
                compact_msgs=compact_msgs,
                keep_msgs=keep_msgs,
                flush_fn=_flush_fn,
                summarize_fn=_summarize,
                queries_fn=lambda n: _pending_rows(conn, n),
                current_summary=session.summary,
                pressure_triggered=pressure_triggered,
            )
        if result.get("compressed"):
            logger.info("会话压缩完成: %s（%s 轮 → 摘要）", session_id, result.get("compressed_rounds"))
    except Exception:
        logger.info("会话压缩失败（静默，下轮重试）: %s", session_id)


# ---------- D03 启动清扫（自动续跑） ----------


async def startup_sweep() -> None:
    """启动清扫七条（SSOT §5.4）：

    running → checkpoint 存在则自动续跑至终态（写操作 approved 不继承，重过门）；
    waiting_* → 保持门态（门事件经 query_event 重放等待裁决）；
    checkpoint 缺失/损坏 → failed + 标注（E5001）。
    """

    def _scan() -> tuple[list[Any], list[Any]]:
        with store_db.get_conn() as conn:
            return store_queries.query_running(conn), store_queries.query_waiting(conn)

    running, waiting = await anyio.to_thread.run_sync(_scan)
    for q in waiting:
        logger.info("门态保持（不自动放行）: %s %s", q.id, q.status)
    for q in running:
        await _resume_or_mark(q)


async def _resume_or_mark(q: Any) -> None:
    ctx = await build_context(q.id)
    graph = build_graph(ctx)
    config_ = {"configurable": {"thread_id": q.id}}
    try:
        snapshot = await graph.aget_state(config_)
        has_checkpoint = bool(snapshot and snapshot.values)
    except Exception:
        has_checkpoint = False
    if not has_checkpoint:
        await ctx.emit(
            "error",
            {
                "type": "error",
                "code": E_RECOVERY,
                "message": "恢复失败：checkpoint 缺失/损坏",
                "retryable": False,
            },
            True,
        )
        await ctx.persist_fn(status="failed")
        await ctx.emit(
            "done", {"type": "done", "query_id": q.id, "status": "failed", "unverified": False}, True
        )
        return
    # A checkpoint never proves that an external write did not commit.
    from app.store import executions

    def _has_started() -> bool:
        with store_db.get_conn() as conn:
            return executions.has_started(conn, q.id)

    values = snapshot.values
    sql = str(values.get("sql") or "")
    if await anyio.to_thread.run_sync(_has_started):
        await ctx.persist_fn(sql_final=sql, result_unverified=1)
        await _fail(q.id, ctx, E_RECOVERY, "检测到已开始的写操作；执行结果待核对，已停止自动重放")
        return
    if sql and not sql_rules.is_pure_select_sql(sql, dialect=str(q.dialect)):
        try:
            # as_node=generate schedules validate again, which routes through the gate.
            await graph.aupdate_state(
                config_,
                {"write_confirmed": False, "gate_decision": "", "status": "running"},
                as_node="generate",
            )
        except Exception:
            await _fail(q.id, ctx, E_RECOVERY, "恢复时重新建立确认流程失败，查询已停止")
            return
    # R-3：恢复路径产出持久化标记——E5 快照项 recovered:true，前端亮「已自动恢复」
    # 绿横幅（checkpoint 缺失路径不置位：该路径有独立 E5001 error 事件标注）
    await ctx.persist_fn(recovered=1)
    task = asyncio.create_task(_run(q.id, None, ctx, graph=graph, config_=config_))
    _tasks[q.id] = task
    logger.info("D03 自动续跑: %s", q.id)


__all__ = [
    "build_context",
    "build_graph",
    "init_runner",
    "is_terminal",
    "resume_gate",
    "shutdown_runner",
    "start_query",
    "startup_sweep",
    "stop_query",
    "subscribe",
    "unsubscribe",
]
