"""维度路由（C1.3）：/dimensions CRUD + 枚举值管理（注入上限 schema.enum_top_n=8）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.semantics import DimensionIn, DimensionValueIn
from app.store import db
from app.store import semantics as store_semantics

router = APIRouter(tags=["dimensions"], dependencies=[TokenDep])


@router.get("/dimensions")
async def list_dimensions() -> dict:
    def _q() -> list[dict]:
        with db.get_conn() as conn:
            return [
                {
                    **d.model_dump(),
                    "values": [v.model_dump() for v in store_semantics.query_dimension_values(conn, d.id)],
                }
                for d in store_semantics.query_dimensions(conn)
            ]

    return ok(await run(_q))


@router.post("/dimensions", status_code=201)
async def create_dimension(payload: DimensionIn) -> dict:
    def _c() -> int:
        with db.get_conn() as conn:
            return store_semantics.save_dimension(conn, name=payload.name, description=payload.description)

    return ok({"id": await run(_c)}, "已创建")


@router.patch("/dimensions/{dim_id}")
async def patch_dimension(dim_id: int, payload: DimensionIn) -> dict:
    def _u() -> None:
        with db.get_conn() as conn:
            store_semantics.update_dimension(conn, dim_id, name=payload.name, description=payload.description)

    await run(_u)
    return ok(None, "已更新")


@router.delete("/dimensions/{dim_id}")
async def delete_dimension(dim_id: int) -> dict:
    def _d() -> None:
        with db.get_conn() as conn:
            store_semantics.delete_dimension(conn, dim_id)

    await run(_d)
    return ok(None, "已删除")


@router.post("/dimensions/{dim_id}/values", status_code=201)
async def add_dimension_value(dim_id: int, payload: DimensionValueIn) -> dict:
    def _c() -> int:
        with db.get_conn() as conn:
            return store_semantics.save_dimension_value(
                conn, dimension_id=dim_id, value=payload.value, label=payload.label, sort=payload.sort
            )

    return ok({"id": await run(_c)}, "已创建")


@router.delete("/dimensions/values/{value_id}")
async def delete_dimension_value(value_id: int) -> dict:
    def _d() -> None:
        with db.get_conn() as conn:
            store_semantics.delete_dimension_value(conn, value_id)

    await run(_d)
    return ok(None, "已删除")
