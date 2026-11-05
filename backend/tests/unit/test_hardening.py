"""Regression tests for crash fences, atomic migrations and replay/live SSE seams."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.api import sse
from app.errors import BizError
from app.graph import nodes, runner
from app.graph.context import NodeContext
from app.store import datasources, db, executions, queries, sessions, settings


def seed(path):
    db.run_migrations(path)
    with db.get_conn(path) as conn:
        settings.ensure_defaults(conn)
        ds = datasources.save_datasource(
            conn,
            name="test",
            dialect="mysql",
            mode="online",
            host="127.0.0.1",
            port=3306,
            db_name="test",
            username="test",
            password_enc=None,
        )
        session = sessions.save_session(conn, datasource_id=ds)
        queries.save_query(
            conn, query_id="q_test", session_id=session.id, datasource_id=ds, dialect="mysql", question="test"
        )
    return ds


def test_failed_migration_is_atomic(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    script = migrations / "0001.sql"
    script.write_text("CREATE TABLE probe(id INTEGER); SELECT * FROM missing;", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", migrations)
    path = tmp_path / "app.db"
    with pytest.raises(db.MigrationError):
        db.run_migrations(path)
    with db.get_conn(path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='probe'").fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    script.write_text("CREATE TABLE probe(id INTEGER); INSERT INTO probe VALUES(1);", encoding="utf-8")
    assert db.run_migrations(path) == ["0001.sql"]
    assert db.run_migrations(path) == []


def test_write_fence_binds_exact_sql_and_rejects_replay(tmp_path):
    path = tmp_path / "app.db"
    seed(path)
    sql = "UPDATE orders SET n=n+1"
    with db.get_conn(path) as conn:
        executions.approve(conn, "q_test", sql)
    with pytest.raises(BizError), db.get_conn(path) as conn:
        executions.start(conn, "q_test", sql + " ", require_approval=True)
    with db.get_conn(path) as conn:
        executions.start(conn, "q_test", sql, require_approval=True)
    with pytest.raises(BizError), db.get_conn(path) as conn:
        executions.start(conn, "q_test", sql, require_approval=True)
    with db.get_conn(path) as conn:
        executions.finish(conn, "q_test", sql, succeeded=True)
    with pytest.raises(BizError), db.get_conn(path) as conn:
        executions.start(conn, "q_test", sql, require_approval=True)


@pytest.mark.asyncio
async def test_crash_after_external_commit_never_reexecutes(tmp_path, monkeypatch):
    from app.exec import runner as engine

    path = tmp_path / "app.db"
    ds = seed(path)
    sql = "UPDATE orders SET n=n+1"
    with db.get_conn(path) as conn:
        executions.approve(conn, "q_test", sql)

    async def emit(*args):
        return 1

    async def persist(**kw):
        pass

    ctx = NodeContext(
        "q_test",
        emit,
        emit,
        persist,
        settings={"query.write_confirm": True},
        datasource={"id": ds, "dialect": "mysql", "mode": "online"},
        db_path=path,
    )
    calls = []

    def committed_then_crashed(*args, **kw):
        calls.append(1)  # Model the external COMMIT before checkpoint persistence.
        raise RuntimeError("process lost after external commit")

    monkeypatch.setattr(engine, "run_query", committed_then_crashed)
    state = {"sql": sql, "dialect": "mysql", "ds_mode": "online", "datasource_id": ds}
    with pytest.raises(RuntimeError):
        await nodes.execute(state, ctx=ctx)
    with pytest.raises(BizError):
        await nodes.execute(state, ctx=ctx)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_recovery_stops_started_write(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    seed(path)
    monkeypatch.setenv("AITOSQL_DATA", str(tmp_path))
    sql = "UPDATE orders SET n=n+1"
    with db.get_conn(path) as conn:
        executions.start(conn, "q_test", sql, require_approval=False)
    calls = []

    async def emit(*args):
        calls.append(args)
        return 1

    async def persist(**kw):
        calls.append(kw)

    ctx = NodeContext("q_test", emit, emit, persist)

    async def context(qid):
        return ctx

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={"sql": sql, "write_confirmed": True})

        async def ainvoke(self, *args):
            pytest.fail("write recovery replayed the graph")

    monkeypatch.setattr(runner, "build_context", context)
    monkeypatch.setattr(runner, "build_graph", lambda ctx: Graph())
    await runner._resume_or_mark(SimpleNamespace(id="q_test", dialect="mysql"))
    assert any(isinstance(c, dict) and c.get("status") == "failed" for c in calls)
    assert "q_test" not in runner._tasks


@pytest.mark.asyncio
async def test_sse_subscribes_before_read_and_replays_final_event(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    seed(path)
    monkeypatch.setenv("AITOSQL_DATA", str(tmp_path))
    original = queries.query_events
    injected = False

    def read(conn, query_id, after_seq=0):
        nonlocal injected
        assert query_id in runner._hub
        if not injected:
            injected = True
            # Event arriving at the old replay/subscribe seam.
            conn.rollback()
            with db.get_conn(path) as writer:
                queries.save_preview(
                    writer,
                    query_id=query_id,
                    columns=[{"name": "n", "type": "int"}],
                    rows=[[1]],
                    truncated=False,
                )
                queries.append_event(writer, query_id=query_id, event_type="done", payload={"status": "done"})
                queries.update_query_result(writer, query_id, status="done")
            conn.execute("BEGIN")
        return original(conn, query_id, after_seq)

    monkeypatch.setattr(queries, "query_events", read)
    response = await sse.stream("q_test", last_event_id="another-query:999")
    frames = [frame async for frame in response.body_iterator]
    assert any("id: q_test:1" in frame for frame in frames)
    assert json.loads(frames[0].split("data: ")[1])["type"] == "result"
    assert "q_test" not in runner._hub


def test_sse_cursor_is_scoped_to_query():
    assert sse._parse_last_event_id("other:100", "q_test") == 0
    assert sse._parse_last_event_id("q_test:-2", "q_test") == 0
    assert sse._parse_last_event_id("q_test:5", "q_test") == 5
