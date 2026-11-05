"""关系路由（C1.2）：列表/新建/确认/删除（inferred 默认 pending，confirm 后才注入）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.tables import RelationCreateIn
from app.store import datasources as store_datasources
from app.store import db

router = APIRouter(tags=["relations"], dependencies=[TokenDep])


@router.get("/relations")
async def list_relations(datasource_id: int, status: str | None = None) -> dict:
    def _q() -> list[dict]:
        with db.get_conn() as conn:
            return [
                r.model_dump() for r in store_datasources.query_relations(conn, datasource_id, status=status)
            ]

    return ok(await run(_q))


@router.post("/relations", status_code=201)
async def create_relation(payload: RelationCreateIn) -> dict:
    def _c() -> dict:
        with db.get_conn() as conn:
            rid = store_datasources.save_relation(
                conn,
                payload.datasource_id,
                from_table=payload.from_table,
                from_col=payload.from_col,
                to_table=payload.to_table,
                to_col=payload.to_col,
                source=payload.source,
                confidence=payload.confidence,
            )
            row = conn.execute("SELECT * FROM relation WHERE id = ?", (rid,)).fetchone()
            from app.store.models import Relation

            return Relation.from_row(row).model_dump()

    return ok(await run(_c), "已创建")


@router.post("/relations/{relation_id}/confirm")
async def confirm_relation(relation_id: int) -> dict:
    def _c() -> None:
        with db.get_conn() as conn:
            store_datasources.confirm_relation(conn, relation_id)

    await run(_c)
    return ok(None, "已确认（确认后进注入）")


@router.delete("/relations/{relation_id}")
async def delete_relation(relation_id: int) -> dict:
    def _d() -> None:
        with db.get_conn() as conn:
            store_datasources.delete_relation(conn, relation_id)

    await run(_d)
    return ok(None, "已删除")
