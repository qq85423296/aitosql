"""样例库路由（C1.4）：CRUD + EXPLAIN 干跑复验（revalidate）。

复验编排：sqlcheck 构造干跑语句（收口），exec 供给引擎连接（显式参数 D2）；
offline 样例跳过干跑并注明。
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.samples import SampleCreateIn, SamplePatchIn
from app.store import db
from app.store import samples as store_samples

router = APIRouter(tags=["samples"], dependencies=[TokenDep])


@router.get("/samples")
async def list_samples(datasource_id: int | None = None, status: str | None = None) -> dict:
    def _q() -> list[dict]:
        with db.get_conn() as conn:
            return [s.model_dump() for s in store_samples.query_samples(conn, datasource_id, status)]

    return ok(await run(_q))


@router.post("/samples", status_code=201)
async def create_sample(payload: SampleCreateIn) -> dict:
    def _c() -> dict:
        with db.get_conn() as conn:
            s = store_samples.save_sample(
                conn,
                datasource_id=payload.datasource_id,
                question=payload.question,
                sql=payload.sql,
                source=payload.source,
            )
            return s.model_dump()

    return ok(await run(_c), "已创建")


@router.patch("/samples/{sample_id}")
async def patch_sample(sample_id: int, payload: SamplePatchIn) -> dict:
    def _u() -> dict:
        with db.get_conn() as conn:
            return store_samples.update_sample(
                conn, sample_id, question=payload.question, sql=payload.sql, status=payload.status
            ).model_dump()

    return ok(await run(_u))


@router.delete("/samples/{sample_id}")
async def delete_sample(sample_id: int) -> dict:
    def _d() -> None:
        with db.get_conn() as conn:
            store_samples.delete_sample(conn, sample_id)

    await run(_d)
    return ok(None, "已删除")


@router.post("/samples/{sample_id}/revalidate")
async def revalidate_sample(sample_id: int) -> dict:
    """EXPLAIN 干跑复验：通过 → 回 on + 刷新 validated_at；offline 样例注明跳过。"""
    from app.exec import runner as exec_runner
    from app.llm import secrets as llm_secrets
    from app.sqlcheck import explain as sql_explain
    from app.store import datasources as store_datasources

    def _load() -> tuple[dict, dict, str | None]:
        with db.get_conn() as conn:
            sample = store_samples.get_sample(conn, sample_id)
            row = store_datasources.get_datasource_row(conn, sample.datasource_id)
            password = llm_secrets.decrypt_text(row["password_enc"]) if row["password_enc"] else None
            return sample.model_dump(), db.row_to_dict(row), password

    sample, ds, password = await run(_load)
    if ds["mode"] != "online":
        return ok({"status": sample["status"], "note": "离线库样例不做干跑（只生成不执行）"})

    def _query(stmt: str, _params: Any) -> list[tuple]:
        conn_eng = exec_runner.connect_engine(ds, password, timeout_s=5)
        try:
            return exec_runner.engine_query(conn_eng, ds["dialect"], stmt)
        finally:
            exec_runner._close_quietly(conn_eng)

    def _dry() -> dict:
        return sql_explain.dry_run(
            sample["sql"], dialect=ds["dialect"], ds_mode=ds["mode"], caps={}, query_fn=_query, timeout_s=5
        )

    result = await anyio.to_thread.run_sync(_dry)
    if result["ok"]:
        refreshed = await run(_mark, sample_id)
        return ok({"status": refreshed["status"], "validated_at": refreshed["validated_at"]})
    return ok({"status": sample["status"], "note": "EXPLAIN 失败（E3001 降级标注）"})


def _mark(sample_id: int) -> dict:
    with db.get_conn() as conn:
        return store_samples.mark_revalidated(conn, sample_id).model_dump()
