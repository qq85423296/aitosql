"""迁移执行器用例（T0.1 验收）：幂等 + 表/视图建全 + 失败报迁移号。"""

from __future__ import annotations

import sqlite3

import pytest

from app.store import db as store_db


def test_migrations_idempotent(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    first = store_db.run_migrations(db_path)
    assert first == [p.name for p in store_db.migration_files()]
    second = store_db.run_migrations(db_path)
    assert second == []  # 幂等：二次执行零新增


def test_expected_objects_built(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store_db.run_migrations(db_path)
    missing_count, missing = store_db.count_expected_objects(db_path)
    assert missing_count == 0, f"缺失表/视图: {missing}"


def test_migration_failure_reports_version(tmp_path, monkeypatch) -> None:
    bad_dir = tmp_path / "migrations_bad"
    bad_dir.mkdir()
    (bad_dir / "0001_bad.sql").write_text("CREATE TABL broken(", encoding="utf-8")
    monkeypatch.setattr(store_db, "MIGRATIONS_DIR", bad_dir)
    with pytest.raises(store_db.MigrationError) as exc_info:
        store_db.run_migrations(tmp_path / "bad.db")
    assert "0001_bad.sql" in str(exc_info.value)  # 失败拒启报迁移号（D-39）


def test_query_event_pk_and_cascade(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    store_db.run_migrations(db_path)
    conn: sqlite3.Connection = store_db.connect(db_path)
    try:
        conn.execute("INSERT INTO query(id, question) VALUES ('q_01ARZ3NDEKTSV4RRFFQ69G5FAV', 't')")
        conn.execute(
            "INSERT INTO query_event(query_id, seq, type, payload)"
            " VALUES ('q_01ARZ3NDEKTSV4RRFFQ69G5FAV', 1, 'stage', '{}')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT COUNT(*) FROM query_event WHERE query_id = 'q_01ARZ3NDEKTSV4RRFFQ69G5FAV'"
        ).fetchone()
        assert row[0] == 1
    finally:
        conn.close()
