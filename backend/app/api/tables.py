"""表/字段路由（C1.2）：详情 / 字段清单 / 编辑 / 提示词视图（D-09 同源）。

修复回路 P1-5a：补 `GET /tables/{table_id}/columns`（前端 ContextPanel Schema 树
展开字段的直取端点；与 GET /tables/{id} 包络内 columns 同形，含 standard_name）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.tables import ColumnPatchIn, TablePatchIn
from app.store import datasources as store_datasources
from app.store import db

router = APIRouter(tags=["tables"], dependencies=[TokenDep])


@router.get("/tables/{table_id}")
async def get_table(table_id: int) -> dict:
    """表详情：字段表 + 键徽章 + 标准绑定（在线库字段名/类型只读——模型层不放行）。"""

    def _q() -> dict:
        with db.get_conn() as conn:
            t = store_datasources.get_table(conn, table_id)
            cols = store_datasources.query_columns(conn, table_id)
            std_names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM field_standard").fetchall()}
            return {
                "table": t.model_dump(),
                "columns": [
                    {**c.model_dump(), "standard_name": std_names.get(c.field_standard_id)} for c in cols
                ],
            }

    return ok(await run(_q))


@router.get("/tables/{table_id}/columns")
async def list_columns(table_id: int) -> dict:
    """字段清单（P1-5a）：`[{...column, standard_name}]`，与表详情包络内 columns 同形。

    前端 ContextPanel 展开 Schema 树字段时直取本端点，无需拉整表详情包络。
    """

    def _q() -> list[dict]:
        with db.get_conn() as conn:
            store_datasources.get_table(conn, table_id)  # 404 闸门
            cols = store_datasources.query_columns(conn, table_id)
            std_names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM field_standard").fetchall()}
            return [{**c.model_dump(), "standard_name": std_names.get(c.field_standard_id)} for c in cols]

    return ok(await run(_q))


@router.patch("/tables/{table_id}")
async def patch_table(table_id: int, payload: TablePatchIn) -> dict:
    """表编辑（人工注释/分区粒度/行数校准）；重摄取不回滚人工字段（合并策略在 store）。"""

    def _u() -> dict:
        with db.get_conn() as conn:
            store_datasources.update_table_meta(
                conn,
                table_id,
                comment_custom=(... if payload.comment_custom is None else payload.comment_custom),
                partition_type=(... if payload.partition_type is None else payload.partition_type),
                row_estimate=(... if payload.row_estimate is None else payload.row_estimate),
            )
            return store_datasources.get_table(conn, table_id).model_dump()

    return ok(await run(_u))


@router.patch("/columns/{column_id}")
async def patch_column(column_id: int, payload: ColumnPatchIn) -> dict:
    def _u() -> dict:
        with db.get_conn() as conn:
            store_datasources.update_column_meta(
                conn,
                column_id,
                comment_custom=(... if payload.comment_custom is None else payload.comment_custom),
                is_pk=None if payload.is_pk is None else int(payload.is_pk),
                is_partition=(None if payload.is_partition is None else int(payload.is_partition)),
                is_bucket=None if payload.is_bucket is None else int(payload.is_bucket),
                field_standard_id=(... if payload.field_standard_id is None else payload.field_standard_id),
            )
            return store_datasources.get_column(conn, column_id).model_dump()

    return ok(await run(_u))


@router.get("/tables/{table_id}/prompt-view")
async def prompt_view(table_id: int) -> dict:
    """提示词视图 `{text}`：与真实注入逐字一致（同一渲染器 retrieval/supply.render_table_text）。"""

    def _q() -> dict:
        from app.retrieval import supply as retrieval_supply
        from app.store import settings as store_settings

        with db.get_conn() as conn:
            t = store_datasources.get_table(conn, table_id)
            text = retrieval_supply.render_table_text(
                conn,
                t.datasource_id,
                t.name,
                enum_top_n=int(store_settings.get_setting(conn, "schema.enum_top_n")),
                partition_hint=bool(store_settings.get_setting(conn, "feature.partition_hint")),
            )
            return {"text": text}

    return ok(await run(_q))
