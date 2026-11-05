"""Evolution evidence: feedback loop (#1), wide-table column recall (#2), log mining (#3)."""

from __future__ import annotations

import math

import pytest

from app.exec import runner as engine
from app.graph import nodes
from app.graph.context import NodeContext
from app.retrieval import feedback, insights, supply
from app.sqlcheck import rules as sql_rules
from app.store import datasources, samples  # noqa: F401  (samples kept for symmetry with rag tests)
from app.store import settings as store_settings
from tests.unit.test_hardening import seed
from tests.unit.test_retrieval.test_rag_hardening import add_table
from tests.unit.test_retrieval.test_retrieval import _seed_schema


def test_tables_in_sql_extracts_physical_tables() -> None:
    sql = (
        "WITH recent AS (SELECT * FROM orders WHERE created_at > '2026-01-01') "
        "SELECT r.id, c.name FROM recent r JOIN customers c ON c.id = r.id"
    )
    assert sql_rules.tables_in_sql(sql, dialect="mysql") == ["customers", "orders"]
    assert sql_rules.tables_in_sql("SELECT * FROM", dialect="mysql") == []
    assert sql_rules.tables_in_sql("", dialect="mysql") == []


def test_feedback_record_and_boost_reorders_rrf(tmp_db) -> None:
    ds = _seed_schema(tmp_db)
    assert feedback.record_success(tmp_db, ds, ["orders", "orders", ""]) == 1
    feedback.record_success(tmp_db, ds, ["orders"])
    boosts = feedback.boosts(tmp_db, ds)
    assert boosts["orders"] == pytest.approx(math.log1p(2))
    base = supply._rrf_fuse([("a_t", 1.0), ("b_t", 0.9)], [])
    assert base == ["a_t", "b_t"]
    boosted = supply._rrf_fuse([("a_t", 1.0), ("b_t", 0.9)], [], {"b_t": math.log1p(50)})
    assert boosted[0] == "b_t"  # 高频成功表乘性加权后反超
    tie = supply._rrf_fuse([("b_t", 1.0)], [("a_t", 0.5)], {"zz_missing": 9.0})
    assert tie == ["a_t", "b_t"]  # 未命中召回的反馈表不改变集合与确定性


def test_supply_applies_feedback_boost(tmp_db) -> None:
    ds = _seed_schema(tmp_db)
    add_table(tmp_db, ds, "customers", "客户主档")
    for _ in range(30):
        feedback.record_success(tmp_db, ds, ["customers"])
    result = supply.supply(
        tmp_db,
        question="订单 客户",
        datasource_id=ds,
        mode_setting="retrieval",
        budget_tokens=2000,
    )
    assert set(result.hit_tables) == {"orders", "customers"}
    assert "customers" in result.trace["feedback_tables"]
    assert result.schema_text.index("customers") < result.schema_text.index("orders")


@pytest.mark.asyncio
async def test_execute_success_records_feedback(tmp_path, monkeypatch) -> None:
    path = tmp_path / "app.db"
    ds = seed(path)

    async def emit(*args):
        return 1

    async def persist(**kw):
        pass

    ctx = NodeContext(
        "q_test",
        emit,
        emit,
        persist,
        settings={},
        datasource={"id": ds, "dialect": "mysql", "mode": "online"},
        db_path=path,
    )

    def ok_run(*args, **kw):
        return engine.ExecOutcome(
            columns=[{"name": "n", "type": "bigint"}], rows=[(1,)], exec_meta={"rows": 1}
        )

    monkeypatch.setattr(engine, "run_query", ok_run)
    monkeypatch.setattr(
        engine,
        "connect_engine",
        lambda *a, **k: (_ for _ in ()).throw(engine.ConnectError("skip")),
    )
    from app.exec import meta as exec_meta

    monkeypatch.setattr(exec_meta, "dump_csv", lambda *a, **k: "unused.csv")
    state = {"sql": "SELECT id FROM orders", "dialect": "mysql", "ds_mode": "online", "datasource_id": ds}
    await nodes.execute(state, ctx=ctx)
    await nodes.execute(state, ctx=ctx)
    from app.store import db as store_db

    with store_db.get_conn(path) as conn:
        row = conn.execute(
            "SELECT success_count FROM retrieval_feedback WHERE datasource_id=? AND table_name='orders'",
            (ds,),
        ).fetchone()
        assert row is not None and row[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM retrieval_feedback WHERE table_name='ghost'"
        ).fetchone()[0] == 0


def test_wide_table_pruned_by_question_relevance(tmp_db) -> None:
    ds = _seed_schema(tmp_db)
    tid, _ = datasources.upsert_table_physical(
        tmp_db, ds, name="wide_facts", comment_db="宽事实表", row_estimate=1, field_count=121,
        partition_type=None,
    )
    datasources.upsert_column_physical(
        tmp_db, tid, name="id", type_="bigint", comment_db="主键", is_pk=1, is_partition=0, is_bucket=0
    )
    for i in range(119):
        datasources.upsert_column_physical(
            tmp_db, tid, name=f"f_{i:03d}", type_="string", comment_db="",
            is_pk=0, is_partition=0, is_bucket=0,
        )
    datasources.upsert_column_physical(
        tmp_db, tid, name="settle_amount", type_="decimal", comment_db="星际结算金额",
        is_pk=0, is_partition=0, is_bucket=0,
    )
    store_settings.save_setting(tmp_db, "schema.wide_table_columns", 50)
    store_settings.save_setting(tmp_db, "schema.wide_table_keep", 12)
    result = supply.supply(
        tmp_db,
        question="星际结算金额",
        datasource_id=ds,
        need_tables=["wide_facts"],
        mode_setting="retrieval",
        budget_tokens=3000,
    )
    assert "wide_facts" in result.hit_tables
    assert "[宽表：共 121 列，按问题相关性注入" in result.schema_text
    assert "settle_amount" in result.schema_text  # 问题相关列被召回
    assert "- id bigint" in result.schema_text  # 主键必留
    assert "f_050" not in result.schema_text  # 无关列被裁剪
    pruned = result.trace["wide_pruned"]["wide_facts"]
    assert pruned["total"] == 121 and pruned["kept"] <= 12
    narrow = supply.render_table_text(
        tmp_db, ds, "orders", enum_top_n=8, partition_hint=True,
        question="x", wide_threshold=50, wide_keep=12,
    )
    assert "[宽表：" not in narrow  # 低于阈值不触发


def test_comment_gaps_orders_by_usage_and_coverage(tmp_db) -> None:
    ds = _seed_schema(tmp_db)  # orders：表/字段注释齐全
    tid, _ = datasources.upsert_table_physical(
        tmp_db, ds, name="click_logs", comment_db="", row_estimate=1, field_count=2, partition_type=None
    )
    for col in ("uid", "url"):
        datasources.upsert_column_physical(
            tmp_db, tid, name=col, type_="string", comment_db="", is_pk=0, is_partition=0, is_bucket=0
        )
    for _ in range(5):
        feedback.record_success(tmp_db, ds, ["orders"])
    tmp_db.execute(
        "INSERT INTO query(id, session_id, datasource_id, dialect, question, sql_final, status)"
        " VALUES('q_gap', NULL, ?, 'mysql', '点击日志怎么统计', 'SELECT uid FROM click_logs', 'done')",
        (ds,),
    )
    gaps = insights.comment_gaps(tmp_db, ds)
    by_name = {g["table_name"]: g for g in gaps}
    assert by_name["orders"]["uses"] == 5
    assert by_name["click_logs"]["uses"] >= 1
    assert by_name["click_logs"]["comment_coverage"] == 0.0
    assert "table_comment" in by_name["click_logs"]["missing"]
    assert "columns:2/2" in by_name["click_logs"]["missing"]
    assert gaps[0]["table_name"] == "orders"  # 使用次数优先排序
    assert by_name["click_logs"]["comment_coverage"] < by_name["orders"]["comment_coverage"]
    assert "click_logs" in by_name["click_logs"]["suggestion"]
