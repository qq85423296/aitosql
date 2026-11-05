"""字段标准路由（C1.3）：/standards CRUD。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.semantics import StandardIn
from app.store import db
from app.store import semantics as store_semantics

router = APIRouter(tags=["standards"], dependencies=[TokenDep])


@router.get("/standards")
async def list_standards() -> dict:
    def _q() -> list[dict]:
        with db.get_conn() as conn:
            return [s.model_dump() for s in store_semantics.query_standards(conn)]

    return ok(await run(_q))


@router.post("/standards", status_code=201)
async def create_standard(payload: StandardIn) -> dict:
    def _c() -> int:
        with db.get_conn() as conn:
            return store_semantics.save_standard(
                conn,
                name=payload.name,
                data_type=payload.data_type,
                unit=payload.unit,
                value_desc=payload.value_desc,
                dimension_standard_id=payload.dimension_standard_id,
                description=payload.description,
            )

    return ok({"id": await run(_c)}, "已创建")


@router.patch("/standards/{std_id}")
async def patch_standard(std_id: int, payload: StandardIn) -> dict:
    def _u() -> None:
        with db.get_conn() as conn:
            store_semantics.update_standard(
                conn,
                std_id,
                name=payload.name,
                data_type=payload.data_type,
                unit=payload.unit,
                value_desc=payload.value_desc,
                dimension_standard_id=payload.dimension_standard_id,
                description=payload.description,
            )

    await run(_u)
    return ok(None, "已更新")


@router.delete("/standards/{std_id}")
async def delete_standard(std_id: int) -> dict:
    def _d() -> None:
        with db.get_conn() as conn:
            store_semantics.delete_standard(conn, std_id)

    await run(_d)
    return ok(None, "已删除")
