"""delete_datasource 级联完整性回归（F-删）：会话解绑 / 反馈清理 / FK 不阻断。"""

from __future__ import annotations

from app.store import datasources


def _seed_ds(conn) -> int:
    return datasources.save_datasource(
        conn=conn,
        name="to-delete",
        dialect="mysql",
        mode="offline",
        host=None,
        port=None,
        db_name="d",
        username=None,
        password_enc=None,
    )


def test_delete_cascades_all_scoped_rows(tmp_db) -> None:
    conn = tmp_db
    ds = _seed_ds(conn)
    tid, _ = datasources.upsert_table_physical(
        conn, ds, name="orders", comment_db=None, row_estimate=None, field_count=1, partition_type=None
    )
    datasources.upsert_column_physical(
        conn, tid, name="id", type_="bigint", comment_db=None, is_pk=1, is_partition=0, is_bucket=0
    )
    conn.execute(
        "INSERT INTO relation(datasource_id, from_table, from_col, to_table, to_col, source, status)"
        " VALUES (?, 'orders', 'customer_id', 'customers', 'id', 'manual', 'confirmed')",
        (ds,),
    )
    conn.execute(
        "INSERT INTO ai_suggestion(datasource_id, kind, payload_json, confidence,"
        " status, created_at) VALUES (?, 'relation', '{}', 0.9, 'pending', datetime('now'))",
        (ds,),
    )
    conn.execute(
        "INSERT INTO retrieval_feedback(datasource_id, table_name, success_count) VALUES (?, 'orders', 3)",
        (ds,),
    )
    sid = "sess-del-1"
    conn.execute(
        "INSERT INTO session(id, title, datasource_id, created_at, updated_at)"
        " VALUES (?, 't', ?, datetime('now'), datetime('now'))",
        (sid, ds),
    )

    datasources.delete_datasource(conn, ds)

    assert conn.execute("SELECT COUNT(*) FROM datasource WHERE id = ?", (ds,)).fetchone()[0] == 0
    for table in ("table_meta", "relation", "ai_suggestion", "retrieval_feedback", "schema_fts"):
        assert (
            conn.execute(f"SELECT COUNT(*) FROM {table} WHERE datasource_id = ?", (ds,)).fetchone()[0]
            == 0
        ), table
    # 会话保留且解绑
    row = conn.execute("SELECT datasource_id FROM session WHERE id = ?", (sid,)).fetchone()
    assert row is not None and row[0] is None


def test_delete_without_feedback_or_session(tmp_db) -> None:
    ds = _seed_ds(tmp_db)
    datasources.delete_datasource(tmp_db, ds)  # 空数据源直接可删
    assert tmp_db.execute("SELECT COUNT(*) FROM datasource WHERE id = ?", (ds,)).fetchone()[0] == 0
