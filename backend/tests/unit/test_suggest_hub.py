"""关系推断枢纽列过滤 + 忽略不回潮回归（F-建议质量）。"""

from __future__ import annotations

from app.ingest import suggest
from app.store import datasources


def _mk_ds(conn) -> int:
    return datasources.save_datasource(
        conn=conn,
        name="hub-test",
        dialect="mysql",
        mode="offline",
        host=None,
        port=None,
        db_name="d",
        username=None,
        password_enc=None,
    )


def _mk_table(conn, ds: int, name: str, cols: list[tuple[str, str, int]]) -> None:
    tid, _ = datasources.upsert_table_physical(
        conn, ds, name=name, comment_db=None, row_estimate=None, field_count=len(cols), partition_type=None
    )
    for col_name, type_, is_pk in cols:
        datasources.upsert_column_physical(
            conn, tid, name=col_name, type_=type_, comment_db=None,
            is_pk=is_pk, is_partition=0, is_bucket=0,
        )


def test_hub_pk_column_produces_no_star_relations(tmp_db) -> None:
    """aoto_id 作为 8 表主键 + 2 表普通列：枢纽过滤后不得产出星群假关系。"""
    conn = tmp_db
    ds = _mk_ds(conn)
    for i in range(8):
        _mk_table(conn, ds, f"fact_{i}", [("aoto_id", "varchar(32)", 1), ("amount", "decimal(12,2)", 0)])
    _mk_table(conn, ds, "modo_user", [("id", "bigint", 1), ("aoto_id", "varchar(32)", 0)])
    _mk_table(conn, ds, "modo_order", [("id", "bigint", 1), ("aoto_id", "varchar(32)", 0)])
    # user_id 命名惯例正常命中（users.id），证明非枢纽路径不受影响
    _mk_table(conn, ds, "users", [("id", "bigint", 1)])
    _mk_table(conn, ds, "orders", [("id", "bigint", 1), ("user_id", "bigint", 0)])

    made = suggest.infer_relation_suggestions(conn, ds)
    rows = conn.execute(
        "SELECT payload_json FROM ai_suggestion WHERE datasource_id = ? AND kind = 'relation'", (ds,)
    ).fetchall()
    assert made == len(rows)
    payloads = [r[0] for r in rows]
    assert not any("aoto_id" in p for p in payloads)  # 枢纽列零建议
    assert any("orders.user_id" in p and "users.id" in p for p in payloads)  # 惯例推断保留


def test_ignored_suggestion_does_not_resurrect(tmp_db) -> None:
    """忽略后的建议重跑不再产出（忽略 = 人裁决不再提）。"""
    conn = tmp_db
    ds = _mk_ds(conn)
    _mk_table(conn, ds, "users", [("id", "bigint", 1)])
    _mk_table(conn, ds, "orders", [("id", "bigint", 1), ("user_id", "bigint", 0)])

    assert suggest.infer_relation_suggestions(conn, ds) == 1
    sid = conn.execute("SELECT id FROM ai_suggestion WHERE datasource_id = ?", (ds,)).fetchone()[0]
    from app.store import suggestions as store_suggestions

    store_suggestions.ignore_suggestions(conn, [sid])
    assert suggest.infer_relation_suggestions(conn, ds) == 0  # 重跑不回潮


def test_chat_retry_escalates_budget_on_finish_length(tmp_db, monkeypatch) -> None:
    """finish=length（思考占满预算）→ 同消息 2× max_tokens 重试一次。"""
    import asyncio
    import contextlib
    import sqlite3

    from app.ai.completion import CompletionEngine
    from app.errors import BizError
    from app.store import ai_runs as store_runs
    from app.store import db as store_db

    conn = tmp_db
    ds = _mk_ds(conn)
    conn.commit()
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    @contextlib.contextmanager
    def _tmp_conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    monkeypatch.setattr(store_db, "get_conn", _tmp_conn)

    run_id = store_runs.create_run(
        conn, datasource_id=ds, scope_type="datasource", scope_table_id=None,
        kinds=["comment"], options={},
    )
    conn.commit()

    calls: list[int] = []

    async def chat_fn(_sys: str, _msgs: list, max_tokens: int) -> str:
        calls.append(max_tokens)
        if len(calls) == 1:
            raise BizError("E2001", "模型未产出正文：输出预算被思考内容占满（finish=length）", 502)
        return '[{"target": "col:id", "suggestion": "主键", "confidence": 0.9, "reason": "x"}]'

    engine = CompletionEngine(run_id, lambda ev: None, asyncio.Event(), chat_fn)
    out = asyncio.run(engine._chat_with_retry([{"role": "user", "content": "x"}]))
    assert "col:id" in out
    assert calls == [8192, 16384]  # 默认预算 → 翻倍重试


def test_run_filters_selected_tables(tmp_db, monkeypatch) -> None:
    """自选表：options.table_names 子集过滤（引擎 run 全程，只处理选中的表）。"""
    import asyncio
    import contextlib
    import sqlite3

    from app.ai.completion import CompletionEngine
    from app.store import ai_runs as store_runs
    from app.store import db as store_db

    conn = tmp_db
    ds = _mk_ds(conn)
    for name in ("t_a", "t_b", "t_c"):
        _mk_table(conn, ds, name, [("id", "bigint", 1)])
    conn.commit()
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]

    @contextlib.contextmanager
    def _tmp_conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    monkeypatch.setattr(store_db, "get_conn", _tmp_conn)

    run_id = store_runs.create_run(
        conn, datasource_id=ds, scope_type="datasource", scope_table_id=None,
        kinds=["comment"], options={"table_names": ["t_b", "t_c"]},
    )
    conn.commit()

    async def chat_fn(_sys: str, _msgs: list, _mt: int) -> str:
        return "[]"

    seen: list[str] = []

    def emit(ev: dict) -> None:
        if ev.get("phase") == "done" and "lane" in ev:
            seen.append(ev["table"])

    engine = CompletionEngine(run_id, emit, asyncio.Event(), chat_fn)
    status = asyncio.run(engine.run())
    assert status == "done"
    assert sorted(seen) == ["t_b", "t_c"]


def test_relation_validator_firewall() -> None:
    """relation 五关：源列/目标表/目标列幻觉剔除、自指剔除、已知对剔除、置信度钳制。"""
    from app.ai.completion import validate_relation_items

    table_index = {
        "users": {"comment": "用户", "pk": ["id"], "cols": {"id", "name"}},
        "orders": {"comment": "订单", "pk": ["id"], "cols": {"id", "user_id"}},
    }
    items = [
        {"column": "user_id", "to_table": "users", "to_column": "id",
         "confidence": 0.95, "reason": "主外键"},
        {"column": "ghost", "to_table": "users", "to_column": "id",
         "confidence": 0.9, "reason": "幻觉源列"},
        {"column": "user_id", "to_table": "nope", "to_column": "id",
         "confidence": 0.9, "reason": "幻觉目标表"},
        {"column": "user_id", "to_table": "users", "to_column": "ghost",
         "confidence": 0.9, "reason": "幻觉目标列"},
        {"column": "user_id", "to_table": "orders", "to_column": "id",
         "confidence": 0.9, "reason": "自指"},
        {"column": "user_id", "to_table": "users", "to_column": "id",
         "confidence": 0.9, "reason": "重复"},
    ]
    out = validate_relation_items(
        items, "orders", {"id", "user_id"}, table_index, {"orders.user_id->users.id"}
    )
    assert out == []  # 全部命中防火墙或已知对

    out2 = validate_relation_items(items[:1], "orders", {"id", "user_id"}, table_index, set())
    assert len(out2) == 1 and out2[0]["from"] == "orders.user_id" and out2[0]["to"] == "users.id"
    assert out2[0]["via"] == "llm" and out2[0]["confidence"] <= 0.9
