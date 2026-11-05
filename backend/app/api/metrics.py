"""指标路由（C1.3）：/metrics CRUD；保存时 sqlglot 校验（未知标识符黄警不阻断）。

D-19 checklist：metric_standard.filter 列全链路引号/参数化（store/semantics 已承载）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.semantics import MetricIn
from app.errors import E_BAD_REQUEST, BizError
from app.store import db
from app.store import semantics as store_semantics

router = APIRouter(tags=["metrics"], dependencies=[TokenDep])


@router.get("/metrics")
async def list_metrics() -> dict:
    def _q() -> list[dict]:
        with db.get_conn() as conn:
            return [m.model_dump() for m in store_semantics.query_metrics(conn)]

    return ok(await run(_q))


@router.post("/metrics", status_code=201)
async def create_metric(payload: MetricIn) -> dict:
    """Q-11：响应回传完整指标行（与 GET /metrics 元素同形），避免前端二次拉取。"""
    warnings = await run(_check_metric_expr, payload.expr)

    def _c() -> dict:
        with db.get_conn() as conn:
            mid = store_semantics.save_metric(
                conn,
                name=payload.name,
                expr=payload.expr,
                filter_=payload.filter,
                base_tables=payload.base_tables,
                description=payload.description,
            )
            return store_semantics.get_metric(conn, mid).model_dump()

    return ok({**await run(_c), "warnings": warnings}, "已创建")


@router.patch("/metrics/{metric_id}")
async def patch_metric(metric_id: int, payload: MetricIn) -> dict:
    warnings = await run(_check_metric_expr, payload.expr)

    def _u() -> None:
        with db.get_conn() as conn:
            store_semantics.update_metric(
                conn,
                metric_id,
                name=payload.name,
                expr=payload.expr,
                filter_=payload.filter,
                base_tables=payload.base_tables,
                description=payload.description,
            )

    await run(_u)
    return ok({"warnings": warnings}, "已更新")


@router.delete("/metrics/{metric_id}")
async def delete_metric(metric_id: int) -> dict:
    def _d() -> None:
        with db.get_conn() as conn:
            store_semantics.delete_metric(conn, metric_id)

    await run(_d)
    return ok(None, "已删除")


def _check_metric_expr(expr: str) -> list[str]:
    """sqlglot 校验（收口在 sqlcheck）：语法错误 422；未知函数黄警不阻断。"""
    from app.sqlcheck import rules as sql_rules

    hit = sql_rules.hard_block_r15(f"SELECT {expr}", dialect="mysql")
    if hit is not None:
        raise BizError(E_BAD_REQUEST, f"指标表达式无法解析: {hit['message']}", 422)
    tree_risks = sql_rules.check_all(
        f"SELECT {expr}", dialect="mysql", caps={"supports": []}, meta={}, ds_mode="offline"
    )
    return [r["message"] for r in tree_risks if r["rule_id"] == "R08"]
