"""AI 补全任务路由（方案 v1.1 §8）：创建 / SSE 进度 / 取消 / 查询。

契约（新增，不动既有冻结契约）：
- POST /ai/completion-runs {datasource_id, scope:{type,table_id?}, kinds[], options{}}
  → {run_id}；同数据源进行中冲突 → 409 E3101；run 不存在 → 404 E4040；
- GET  /ai/completion-runs/{id}/stream  SSE：泳道事件 {lane,table,phase,line,...}
  → 终帧 {phase:"run_end", status, made, failed?}；
- POST /ai/completion-runs/{id}/cancel → {cancelled:true}（项间协作式取消）；
- GET  /ai/completion-runs/{id} → 任务行（轮询/终态复核）。

分层（D2/D6）：LLM chat_fn 在本层解析注入引擎（ai 层不 import llm）；
底层异常原文不出 SSE/网（S-1），详情仅落日志。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import TokenDep, ok, run
from app.errors import E_AI_RUN_CONFLICT, E_BAD_REQUEST, BizError
from app.identifiers import require_id
from app.store import ai_runs as store_runs
from app.store import datasources as store_datasources
from app.store import db

router = APIRouter(tags=["ai"], dependencies=[TokenDep])
logger = logging.getLogger("aitosql.api.ai_runs")

_RUN_QUEUES: dict[int, asyncio.Queue] = {}
_RUN_CANCELS: dict[int, asyncio.Event] = {}
_BG_TASKS: set[asyncio.Task] = set()

VALID_KINDS = ("comment", "relation", "std_bind")  # metric 归纳留 P3


class ScopeIn(BaseModel):
    type: str = Field(pattern="^(datasource|table|relations)$")
    table_id: int | None = None
    table_names: list[str] | None = None  # type=datasource 时的自选表子集（缺省=整库）


class OptionsIn(BaseModel):
    rescan_existing: bool = False
    use_sample_values: bool = False


class CompletionIn(BaseModel):
    datasource_id: int
    scope: ScopeIn
    kinds: list[str] = Field(default=["comment", "relation", "std_bind"])
    options: OptionsIn = Field(default_factory=OptionsIn)


def _spawn(coro: Coroutine[Any, Any, Any]) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


def _sample_values_allowed() -> bool:
    """全局门控（§6：采样值默认禁出境，设置项开启后才允许 run option 生效）。"""
    from app.store import settings as store_settings

    with db.get_conn() as conn:
        return bool(store_settings.get_setting(conn, "ai.sample_values_allowed"))


def _resolve_chat_fn() -> Callable[..., Any] | None:
    """route.ai → chat_fn（本层注入，ai 层不见 llm）；未配置返回 None。"""
    from app.llm import providers as llm_providers
    from app.llm import registry as llm_registry
    from app.store import settings as store_settings

    with db.get_conn() as conn:
        route_ref = str(store_settings.get_setting(conn, "route.ai") or "")
        if not route_ref:
            return None
        resolved = llm_registry.resolve_route(conn, route_ref)
    if resolved is None:
        return None
    profile, model = resolved
    adapter = llm_providers.get_adapter(profile)

    async def chat_fn(_sys: str, messages: list[dict[str, str]], max_tokens: int) -> str:
        outcome = await adapter.chat(model, messages, max_tokens=max_tokens)
        return outcome.text

    return chat_fn


def _query_fn_factory(ds_id: int) -> Callable[[], tuple]:
    """采样 query_fn 工厂（api 层装配 exec/llm 依赖后注入引擎，D2/D6 分层）。

    返回 () → (query_fn|None, close_fn|None)：连接失败降级 (None, None)（无画像继续跑）。
    """

    def factory() -> tuple:
        from app.exec import runner as exec_runner
        from app.llm import secrets as llm_secrets

        with db.get_conn() as conn:
            ds_row = store_datasources.get_datasource_row(conn, ds_id)
        password = llm_secrets.decrypt_text(ds_row["password_enc"]) if ds_row["password_enc"] else None
        try:
            eng = exec_runner.connect_engine(db.row_to_dict(ds_row), password, timeout_s=15)
        except Exception:
            logger.info("采样连接失败（降级无画像）: ds=%s", ds_id, exc_info=True)
            return None, None
        dialect = ds_row["dialect"]

        def _query(sql: str, params: object = None) -> list[tuple]:
            return exec_runner.engine_query(eng, dialect, sql, params)

        return _query, lambda: exec_runner._close_quietly(eng)

    return factory


async def _run_worker(run_id: int, ds_id: int, use_samples: bool) -> None:
    from app.ai.completion import CompletionEngine

    queue = _RUN_QUEUES[run_id]
    cancel = _RUN_CANCELS[run_id]

    def emit(ev: dict) -> None:
        queue.put_nowait(ev)

    try:
        chat_fn = await run(_resolve_chat_fn)
        sample_allowed = await run(_sample_values_allowed)
        factory = _query_fn_factory(ds_id) if (use_samples and sample_allowed) else None
        engine = CompletionEngine(run_id, emit, cancel, chat_fn, factory)
        await engine.run()
    except Exception:
        logger.warning("补全任务异常 run=%s", run_id, exc_info=True)
        queue.put_nowait(
            {"phase": "run_end", "status": "failed", "error": "补全任务中断（详细信息见服务端日志）"}
        )
    finally:
        queue.put_nowait(None)


@router.post("/ai/completion-runs", status_code=201)
async def create_completion_run(payload: CompletionIn) -> dict:
    kinds = [k for k in dict.fromkeys(payload.kinds) if k in VALID_KINDS]
    if not kinds:
        raise BizError(E_BAD_REQUEST, "补全类型不能为空（可选 comment/relation/std_bind）", 422)
    if payload.scope.type == "table" and payload.scope.table_id is None:
        raise BizError(E_BAD_REQUEST, "scope=table 需要 table_id", 422)

    def _create() -> int:
        with db.get_conn() as conn:
            ds = store_datasources.get_datasource(conn, payload.datasource_id)  # 404 守卫
            if payload.scope.type == "table":
                t = store_datasources.get_table(conn, require_id(payload.scope.table_id))
                if t.datasource_id != ds.id:
                    raise BizError(E_BAD_REQUEST, "表不属于该数据源", 422)
            options = payload.options.model_dump()
            if payload.scope.type == "datasource" and payload.scope.table_names:
                known = {t.name for t in store_datasources.query_tables(conn, ds.id)}
                names = [n for n in dict.fromkeys(payload.scope.table_names) if n in known]
                if not names:
                    raise BizError(E_BAD_REQUEST, "自选表为空或不属于该数据源", 422)
                options["table_names"] = names
            if store_runs.active_run(conn, ds.id) is not None:
                raise BizError(E_AI_RUN_CONFLICT, "该数据源已有补全任务进行中，请等待完成后重试", 409)
            return store_runs.create_run(
                conn,
                datasource_id=ds.id,
                scope_type=payload.scope.type,
                scope_table_id=payload.scope.table_id,
                kinds=kinds,
                options=options,
            )

    run_id = await run(_create)
    _RUN_QUEUES[run_id] = asyncio.Queue()
    _RUN_CANCELS[run_id] = asyncio.Event()
    _spawn(_run_worker(run_id, payload.datasource_id, payload.options.use_sample_values))
    return ok({"run_id": run_id}, "已创建")


@router.get("/ai/completion-runs")
async def list_completion_runs(datasource_id: int, limit: int = 10) -> dict:
    """任务清单（建议抽屉按批次过滤用）：按 id 倒序。"""

    def _q() -> list[dict]:
        with db.get_conn() as conn:
            return [r.model_dump() for r in store_runs.latest_runs(conn, datasource_id, limit)]

    return ok(await run(_q))


@router.get("/ai/completion-runs/{run_id}")
async def get_completion_run(run_id: int) -> dict:
    def _q() -> dict:
        with db.get_conn() as conn:
            return store_runs.get_run(conn, run_id).model_dump()

    return ok(await run(_q))


@router.post("/ai/completion-runs/{run_id}/cancel")
async def cancel_completion_run(run_id: int) -> dict:
    def _q() -> None:
        with db.get_conn() as conn:
            store_runs.get_run(conn, run_id)  # 404 守卫

    await run(_q)
    ev = _RUN_CANCELS.get(run_id)
    if ev is not None:
        ev.set()
    return ok({"cancelled": True}, "已取消")


@router.get("/ai/completion-runs/{run_id}/stream")
async def stream_completion_run(run_id: int) -> StreamingResponse:
    queue = _RUN_QUEUES.get(run_id)
    if queue is None:
        # 任务已结束且流已被消费（或服务重启）：直接回放终态一帧
        def _q() -> dict:
            with db.get_conn() as conn:
                return store_runs.get_run(conn, run_id).model_dump()

        row = await run(_q)

        async def _replay() -> AsyncIterator[str]:
            payload = {"phase": "run_end", "status": row["status"], "made": row["made"]}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(_replay(), media_type="text/event-stream")

    async def _gen() -> AsyncIterator[str]:
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            _RUN_QUEUES.pop(run_id, None)
            _RUN_CANCELS.pop(run_id, None)

    return StreamingResponse(_gen(), media_type="text/event-stream")
