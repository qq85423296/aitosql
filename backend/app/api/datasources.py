"""数据源路由（C1.2）：CRUD / 测试连接 / 摄取 SSE / DDL·CSV·AI 导入 / 表清单 / ER 图谱摆位。

摄取进度走 SSE（`{pct, line}` 事件流，中断续摄 E1002）；
DDL 解析由 sqlcheck/ddl.py 收口（D3），本层仅编排显式传参（D2）。
修复回路：S-1 底层异常原文不出网；Q-6 摄取并发守卫；Q-10 列表聚合计数；
R-1 摄取/导入后建议生产（ingest/suggest）+ CSV 模板 / AI 草稿端点；
F-4 数据源变更/删除时 exec 连接池失效。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import anyio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import TokenDep, ok, run
from app.api.schemas.datasources import (
    AiDraftIn,
    CsvTemplateIn,
    DatasourceCreateIn,
    DatasourcePatchIn,
    DdlPreviewIn,
    GraphLayoutIn,
    InstructionIn,
)
from app.errors import E_BAD_REQUEST, E_CONFLICT, E_DS_CONNECT, BizError
from app.llm import secrets as llm_secrets
from app.store import datasources as store_datasources
from app.store import db

router = APIRouter(tags=["datasources"], dependencies=[TokenDep])

logger = logging.getLogger("aitosql.api.datasources")

_BG_TASKS: set[asyncio.Task] = set()  # 后台任务引用集（摄取/建议生产，防 GC 取消）
_INGEST_LOCKS: dict[int, asyncio.Lock] = {}  # Q-6：数据源粒度摄取并发守卫


def _ingest_lock(ds_id: int) -> asyncio.Lock:
    return _INGEST_LOCKS.setdefault(ds_id, asyncio.Lock())


def _spawn_bg(coro: Callable[..., Coroutine[Any, Any, Any]], *args: Any) -> None:
    """后台任务统一挂载（RUF006：持引用 + 完成回调回收）。"""
    task = asyncio.create_task(coro(*args))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


@router.get("/datasources/{ds_id}/retrieval-index")
async def retrieval_index_status(ds_id: int) -> dict:
    from app.retrieval import index as rag_index

    def _status() -> dict:
        with db.get_conn() as conn:
            store_datasources.get_datasource(conn, ds_id)
            return rag_index.status(conn, ds_id)

    return ok(await run(_status))


@router.post("/datasources/{ds_id}/reindex")
async def reindex_datasource(ds_id: int) -> dict:
    from app.retrieval import index as rag_index

    def _check() -> None:
        with db.get_conn() as conn:
            store_datasources.get_datasource(conn, ds_id)

    await run(_check)
    return ok(await run(rag_index.rebuild, ds_id))


@router.get("/datasources/{ds_id}/retrieval-insights")
async def retrieval_insights(ds_id: int) -> dict:
    """查询日志挖掘（演进 #3）：高使用低注释表清单，供「补注释」提示。"""
    from app.retrieval import insights as rag_insights

    def _q() -> dict:
        with db.get_conn() as conn:
            store_datasources.get_datasource(conn, ds_id)
            return {"gaps": rag_insights.comment_gaps(conn, ds_id)}

    return ok(await run(_q))


@router.get("/datasources")
async def list_datasources() -> dict:
    """列表聚合 table_count/field_count（Q-10：单次 GROUP BY，非逐源 N+1）。"""

    def _q() -> list[dict]:
        with db.get_conn() as conn:
            items = [d.model_dump() for d in store_datasources.query_datasources(conn)]
            stats = store_datasources.datasource_stats(conn)
            for d in items:
                c = stats.get(d["id"]) or {}
                d["table_count"] = c.get("table_count", 0)
                d["field_count"] = c.get("field_count", 0)
            return items

    return ok(await run(_q))


@router.post("/datasources", status_code=201)
async def create_datasource(payload: DatasourceCreateIn) -> dict:
    def _c() -> dict:
        with db.get_conn() as conn:
            enc = llm_secrets.encrypt_text(payload.password) if payload.password else None
            ds_id = store_datasources.save_datasource(
                conn,
                name=payload.name,
                dialect=payload.dialect,
                mode=payload.mode,
                host=payload.host,
                port=payload.port,
                db_name=payload.db_name,
                username=payload.username,
                password_enc=enc,
            )
            return store_datasources.get_datasource(conn, ds_id).model_dump()

    return ok(await run(_c), "创建成功")


@router.get("/datasources/{ds_id}/instruction")
async def get_instruction(ds_id: int) -> dict:
    """每数据源口径说明（P5）：术语/join 惯例/批次口径，注入信封「数据源口径」段。"""

    def _q() -> dict:
        with db.get_conn() as conn:
            store_datasources.get_datasource(conn, ds_id)  # 404 闸门
            return {"datasource_id": ds_id, "content": store_datasources.get_instruction(conn, ds_id)}

    return ok(await run(_q))


@router.put("/datasources/{ds_id}/instruction")
async def put_instruction(ds_id: int, payload: InstructionIn) -> dict:
    def _u() -> dict:
        with db.get_conn() as conn:
            store_datasources.save_instruction(conn, ds_id, payload.content)
            return {"datasource_id": ds_id, "content": store_datasources.get_instruction(conn, ds_id)}

    return ok(await run(_u), "口径说明已保存")


@router.patch("/datasources/{ds_id}")
async def patch_datasource(ds_id: int, payload: DatasourcePatchIn) -> dict:
    def _u() -> dict:
        with db.get_conn() as conn:
            enc = llm_secrets.encrypt_text(payload.password) if payload.password is not None else ...
            store_datasources.update_datasource(
                conn,
                ds_id,
                name=payload.name,
                host=payload.host,
                port=payload.port,
                db_name=payload.db_name,
                username=payload.username,
                password_enc=enc,
            )
            return store_datasources.get_datasource(conn, ds_id).model_dump()

    result = await run(_u)
    _invalidate_pool(ds_id)  # F-4：连接参数可能已变，池内空闲连接全部失效
    return ok(result)


@router.delete("/datasources/{ds_id}")
async def delete_datasource(ds_id: int) -> dict:
    await run(_delete, ds_id)
    _invalidate_pool(ds_id)  # F-4
    _INGEST_LOCKS.pop(ds_id, None)
    return ok(None, "已删除")


def _delete(ds_id: int) -> None:
    with db.get_conn() as conn:
        store_datasources.delete_datasource(conn, ds_id)


def _invalidate_pool(ds_id: int) -> None:
    """F-4：数据源变更/删除 → exec 工作连接池失效（防旧连接混用）。"""
    from app.exec import runner as exec_runner

    exec_runner.invalidate_pool(ds_id)


@router.post("/datasources/{ds_id}/test")
async def test_datasource(ds_id: int) -> dict:
    """测试连接：`200 {version, readonly}` / `502 {code:E1001}`（失败不创建记录）。"""
    from app.exec import runner as exec_runner
    from app.ingest import capability

    def _test() -> dict:
        with db.get_conn() as conn:
            row = store_datasources.get_datasource_row(conn, ds_id)
        password = llm_secrets.decrypt_text(row["password_enc"]) if row["password_enc"] else None
        ds = db.row_to_dict(row)
        try:
            conn_eng = exec_runner.connect_engine(ds, password, timeout_s=10)
            try:
                version = capability.probe_version(
                    ds["dialect"], lambda sql: exec_runner.engine_query(conn_eng, ds["dialect"], sql)
                )
            finally:
                exec_runner._close_quietly(conn_eng)
        except BizError:
            raise
        except Exception as exc:
            # S-1：底层异常原文（驱动栈/连接串/主机端口）不回前端，详情仅落日志
            logger.warning("测试连接失败 ds=%s: %s: %s", ds_id, type(exc).__name__, exc)
            raise BizError(
                E_DS_CONNECT, "数据源连接失败（请检查主机/端口/账号；详细信息见服务端日志）", 502
            ) from exc
        readonly = ds["dialect"] == "clickhouse"
        return {"version": version, "readonly": readonly}

    return ok(await run(_test))


@router.post("/datasources/{ds_id}/ingest")
async def ingest_datasource(ds_id: int) -> StreamingResponse:
    """摄取（SSE 进度 `{pct, line}`；中断续摄 E1002）。

    Q-6：数据源粒度并发守卫——同 ds 重入 409（E4090），防双摄取互踩元数据；
    R-1：摄取成功后后台生产建议（关系推断 + 可选注释补全）。
    """
    lock = _ingest_lock(ds_id)
    if lock.locked():
        raise BizError(E_CONFLICT, "该数据源摄取进行中，请等待完成后重试", 409)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _cb(pct: int, line: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"pct": pct, "line": line})

    async def _worker() -> None:
        from app.exec import runner as exec_runner
        from app.ingest import online

        async with lock:
            try:

                def _ingest() -> dict:
                    with db.get_conn() as conn:
                        row = store_datasources.get_datasource_row(conn, ds_id)
                        password = (
                            llm_secrets.decrypt_text(row["password_enc"]) if row["password_enc"] else None
                        )
                        ds = db.row_to_dict(row)
                        eng = exec_runner.connect_engine(ds, password, timeout_s=30)

                        def _query(sql: str, params: Any) -> list[tuple]:
                            return exec_runner.engine_query(eng, ds["dialect"], sql, params)

                        try:
                            result = online.ingest_datasource(
                                conn,
                                ds_id,
                                dialect=ds["dialect"],
                                db_name=row["db_name"] or "",
                                query_fn=_query,
                                event_cb=_cb,
                            )
                        finally:
                            exec_runner._close_quietly(eng)
                    # Embedding happens outside a SQLite write transaction.
                    from app.retrieval import index as rag_index

                    result["retrieval_index"] = rag_index.rebuild(ds_id)
                    return result

                result = await anyio.to_thread.run_sync(_ingest)
                queue.put_nowait(
                    {"pct": 100, "line": f"完成：{result['tables']} 表；{result['key_summary']}"}
                )
            except BizError as exc:
                queue.put_nowait({"error": exc.code, "line": exc.message})
            except Exception as exc:
                # S-1：底层异常原文不出 SSE 通道，详情落日志
                logger.warning("摄取失败 ds=%s: %s: %s", ds_id, type(exc).__name__, exc)
                queue.put_nowait({"error": "E1002", "line": "摄取中断（可重试续摄；详细信息见服务端日志）"})
            finally:
                queue.put_nowait(None)

    _spawn_bg(_worker)

    async def _gen() -> AsyncIterator[str]:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.post("/datasources/{ds_id}/import-ddl")
async def preview_ddl(ds_id: int, payload: DdlPreviewIn) -> dict:
    """DDL 预览：`{ddl}` → `{tables, errors:[{line,message}]}`（部分失败行标红）。"""
    from app.sqlcheck import ddl as sql_ddl

    def _p() -> dict:
        with db.get_conn() as conn:
            ds = store_datasources.get_datasource(conn, ds_id)
        return sql_ddl.parse_ddl(payload.ddl, ds.dialect)

    return ok(await run(_p))


@router.post("/datasources/{ds_id}/import-ddl/commit")
async def commit_ddl(ds_id: int, payload: DdlPreviewIn) -> dict:
    """DDL 入库：成功部分可入库（解析产物经显式参数传递，分层裁决见 ingest/offline）。"""
    from app.ingest import offline
    from app.sqlcheck import ddl as sql_ddl

    def _c() -> dict:
        with db.get_conn() as conn:
            ds = store_datasources.get_datasource(conn, ds_id)
            parsed = sql_ddl.parse_ddl(payload.ddl, ds.dialect)
            result = offline.commit_parsed_tables(conn, ds_id, parsed["tables"])
            return {**result, "errors": parsed["errors"]}

    result = await run(_c)
    return ok(result, "入库完成")


@router.post("/datasources/{ds_id}/import-csv-template")
async def import_csv_template(ds_id: int, payload: CsvTemplateIn) -> dict:
    """元数据字典模板通道：11 列中文表头（必填 8 列）→ 多表批量入库。

    `{csv_text}` → `{tables, columns, errors}`；行级错误不阻断其他行；
    同名表走合并策略（upsert 只刷物理属性，人工注释不覆盖）；
    表模式名落库 table_meta.schema_name（0011：问数 SQL 限定前缀），响应汇总透出。
    """
    from app.ingest import offline

    def _c() -> dict:
        with db.get_conn() as conn:
            store_datasources.get_datasource(conn, ds_id)  # 404 守卫
            tables, errors = offline.csv_dict_template_to_tables(payload.csv_text)
            if not tables:
                first = errors[0]["message"] if errors else "无有效数据行"
                raise BizError(E_BAD_REQUEST, f"模板无有效数据行（{first}）", 422)
            result = offline.commit_parsed_tables(conn, ds_id, tables)
            schemas = sorted({t["schema"] for t in tables})
            return {**result, "errors": errors, "schemas": schemas}

    result = await run(_c)
    return ok(result, "入库完成")


@router.post("/datasources/{ds_id}/ai-draft")
async def ai_draft(ds_id: int, payload: AiDraftIn) -> dict:
    """AI 草稿通道（FR-D13/R-1）：自然语言描述 → route.ai 生成 DDL → 解析入库。

    `{hint}` → `{tables, columns, errors}`；LLM 产出经 ```sql 围栏剥离后走
    sqlcheck/ddl 同一解析管线（D3），逐表失败行 errors 透出。
    F-3：LLM 裸 await，不持 sqlite 连接跨网络往返。
    """
    from app.ingest import offline
    from app.llm import providers as llm_providers
    from app.llm import registry as llm_registry
    from app.sqlcheck import ddl as sql_ddl
    from app.store import settings as store_settings

    def _resolve() -> tuple:
        with db.get_conn() as conn:
            store_datasources.get_datasource(conn, ds_id)  # 404 守卫
            route_ref = str(store_settings.get_setting(conn, "route.ai") or "")
            if not route_ref:
                raise BizError(
                    E_BAD_REQUEST, "未配置 route.ai 小模型路由（设置页配置后重试，或改用 DDL/CSV 通道）", 422
                )
            ds = store_datasources.get_datasource(conn, ds_id)
            return (*llm_registry.resolve_route(conn, route_ref), ds.dialect)

    profile, model, dialect = await run(_resolve)
    outcome = await llm_providers.get_adapter(profile).chat(
        model, [{"role": "user", "content": offline.build_ai_draft_prompt(payload.hint)}], max_tokens=4096
    )
    raw = outcome.text.strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)```", raw, re.DOTALL)
    ddl_text = (fence.group(1) if fence else raw).strip()
    if not ddl_text:
        raise BizError(E_BAD_REQUEST, "AI 未产出 DDL 文本，请补充描述后重试", 422)

    def _commit() -> dict:
        with db.get_conn() as conn:
            parsed = sql_ddl.parse_ddl(ddl_text, dialect)
            result = offline.commit_parsed_tables(conn, ds_id, parsed["tables"])
            return {**result, "errors": parsed["errors"]}

    result = await run(_commit)
    return ok(result, "AI 草稿已入库")


@router.get("/datasources/{ds_id}/tables")
async def list_tables(ds_id: int) -> dict:
    """表清单：`[{name, comment, fields, rows, pk, partition, ...}]`。"""

    def _q() -> list[dict]:
        with db.get_conn() as conn:
            store_datasources.get_datasource(conn, ds_id)
            out = []
            for t in store_datasources.query_tables(conn, ds_id):
                cols = store_datasources.query_columns(conn, t.id)
                out.append(
                    {
                        "id": t.id,
                        "name": t.name,
                        "schema": t.schema_name,
                        "comment": t.comment_custom or t.comment_db or "",
                        "fields": len(cols),
                        "rows": t.row_estimate,
                        "pk": [c.name for c in cols if c.is_pk],
                        "partition": t.partition_type,
                        "updated_at": t.updated_at,
                    }
                )
            return out

    return ok(await run(_q))


@router.get("/datasources/{ds_id}/graph")
async def er_graph(ds_id: int) -> dict:
    """ER 图谱 `{nodes, edges}`（摆位见 graph/layout 端点，D-18）。"""

    def _q() -> dict:
        with db.get_conn() as conn:
            store_datasources.get_datasource(conn, ds_id)
            return store_datasources.query_er_graph(conn, ds_id)

    return ok(await run(_q))


@router.get("/datasources/{ds_id}/graph/layout")
async def get_layout(ds_id: int) -> dict:
    """摆位读取（缺失返回空，前端按表名字典序确定性平铺）。"""

    def _q() -> dict:
        with db.get_conn() as conn:
            return store_datasources.get_graph_layout(conn, ds_id).model_dump()

    return ok(await run(_q))


@router.put("/datasources/{ds_id}/graph/layout")
async def put_layout(ds_id: int, payload: GraphLayoutIn) -> dict:
    def _s() -> None:
        with db.get_conn() as conn:
            store_datasources.save_graph_layout(conn, ds_id, payload.node_positions)

    await run(_s)
    return ok(None, "已保存")
