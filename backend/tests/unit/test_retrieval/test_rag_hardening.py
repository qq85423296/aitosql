"""RAG evidence: recall, scope isolation, index freshness, join context and budget."""

from __future__ import annotations

from app import tokens
from app.retrieval import index, supply, vector
from app.store import datasources, samples
from tests.unit.test_retrieval.test_retrieval import _seed_schema


def add_table(conn, ds_id, name, comment):
    tid, _ = datasources.upsert_table_physical(
        conn, ds_id, name=name, comment_db=comment, row_estimate=1, field_count=1, partition_type=None
    )
    datasources.upsert_column_physical(
        conn, tid, name="id", type_="bigint", comment_db=comment, is_pk=1, is_partition=0, is_bucket=0
    )
    return tid


def test_metadata_document_contains_all_columns_and_comments(tmp_db):
    ds = _seed_schema(tmp_db)
    docs = index.documents(tmp_db, ds)
    assert len(docs) == 1
    assert all(value in docs[0][1] for value in ["id", "created_at", "订单主表", "下单时间"])


def test_lazy_fts_refresh_after_same_second_manual_edit(tmp_db):
    ds = _seed_schema(tmp_db)
    first = supply.supply(
        tmp_db, question="订单", datasource_id=ds, mode_setting="retrieval", budget_tokens=1000
    )
    assert first.trace["fts_tables"] == ["orders"]
    before = index.status(tmp_db, ds)["revision"]
    table = datasources.get_table_by_name(tmp_db, ds, "orders")
    datasources.update_table_meta(tmp_db, table.id, comment_custom="星际运费结算")
    assert index.status(tmp_db, ds)["revision"] > before
    result = supply.supply(
        tmp_db, question="星际运费", datasource_id=ds, mode_setting="retrieval", budget_tokens=1000
    )
    assert result.trace["fts_tables"] == ["orders"]
    assert "星际运费" in result.schema_text
    state = index.status(tmp_db, ds)
    assert state["fts_revision"] == state["revision"]


def test_confirmed_join_bridge_and_no_pending_injection(tmp_db):
    ds = _seed_schema(tmp_db)
    add_table(tmp_db, ds, "customers", "客户")
    add_table(tmp_db, ds, "membership", "桥接")
    add_table(tmp_db, ds, "untrusted", "未确认关系")
    for left, right, status in [
        ("orders", "membership", "confirmed"),
        ("membership", "customers", "confirmed"),
        ("orders", "untrusted", "pending"),
    ]:
        tmp_db.execute(
            "INSERT INTO relation(datasource_id,from_table,from_col,to_table,to_col,source,status)"
            " VALUES(?,?,'id',?,'id','manual',?)",
            (ds, left, right, status),
        )
    result = supply.supply(
        tmp_db,
        question="orders customers",
        datasource_id=ds,
        need_tables=["orders", "customers"],
        mode_setting="retrieval",
        budget_tokens=2000,
    )
    assert "membership" in result.hit_tables
    assert "untrusted" not in result.hit_tables
    assert "已确认关系: orders.id = membership.id" in result.schema_text


def test_all_modes_obey_budget_and_report_only_injected_tables(tmp_db):
    ds = _seed_schema(tmp_db)
    for n in range(10):
        add_table(tmp_db, ds, f"t_{n}", "元数据" * 50)
    for mode in ("auto", "full", "retrieval"):
        result = supply.supply(
            tmp_db,
            question="orders",
            datasource_id=ds,
            mode_setting=mode,
            need_tables=["orders"],
            budget_tokens=180,
        )
        assert tokens.estimate_tokens(result.schema_text) <= 180
        assert all(f"表 {name}" in result.schema_text for name in result.hit_tables)
        assert result.hit_tables[0] == "orders"


def test_fewshot_relevance_and_datasource_scope(tmp_db):
    ds = _seed_schema(tmp_db)
    irrelevant = samples.save_sample(tmp_db, datasource_id=ds, question="员工考勤", sql="SELECT 1")
    good = samples.save_sample(
        tmp_db, datasource_id=ds, question="订单销售额", sql="SELECT SUM(amount) FROM orders"
    )
    tmp_db.execute("UPDATE sample SET hit_count=10000 WHERE id=?", (irrelevant.id,))
    assert samples.query_fewshot(tmp_db, ds, 1, question="订单销售额趋势")[0].id == good.id
    assert samples.query_fewshot(tmp_db, ds + 1, 3, question="订单销售额") == []
    samples.update_sample(tmp_db, good.id, status="stale")
    assert samples.query_fewshot(tmp_db, ds, 1, question="订单销售额") == []


def test_stale_vectors_are_not_used(tmp_db, monkeypatch):
    ds = _seed_schema(tmp_db)

    def unexpected():
        raise AssertionError("Stale vector index should fall back before loading the model")

    monkeypatch.setattr(vector, "_get_embedder", unexpected)
    assert vector.search_tables(tmp_db, ds, "订单") == []


def test_real_sqlite_vec_cosine_and_scope(tmp_db, monkeypatch):
    ds = _seed_schema(tmp_db)
    assert vector.ensure_vec_table(tmp_db, 512)
    embedding = [1.0] + [0.0] * 511
    vector.replace_vectors(tmp_db, ds, [("orders", "订单")], [embedding], dim=512)
    vector.replace_vectors(tmp_db, ds + 1, [("private_other", "其他数据源")], [embedding], dim=512)
    tmp_db.execute(
        "UPDATE schema_revision SET vector_revision=revision,vector_model=?,vector_dim=512"
        " WHERE datasource_id=?",
        (vector.MODEL_NAME, ds),
    )
    monkeypatch.setattr(vector, "_get_embedder", lambda: object())
    monkeypatch.setattr(vector, "embed_texts", lambda texts: [embedding for _ in texts])
    hits = vector.search_tables(tmp_db, ds, "订单")
    assert hits and hits[0][0] == "orders"
    assert all(name != "private_other" for name, _ in hits)


def test_index_rebuild_rejects_concurrent_schema_change(tmp_path, monkeypatch):
    from app.store import db, settings

    path = tmp_path / "app.db"
    db.run_migrations(path)
    with db.get_conn(path) as conn:
        settings.ensure_defaults(conn)
        ds = _seed_schema(conn)

    def embed(texts):
        # A second writer succeeds: the embedding operation holds no DB write lock.
        with db.get_conn(path) as conn:
            conn.execute("UPDATE table_meta SET comment_custom='新版' WHERE datasource_id=?", (ds,))
        return [[1.0] + [0.0] * 511 for _ in texts]

    monkeypatch.setattr(vector, "embed_texts", embed)
    result = index.rebuild(ds, db_path=path)
    assert result == {"installed": False, "reason": "schema_changed_during_embedding"}
