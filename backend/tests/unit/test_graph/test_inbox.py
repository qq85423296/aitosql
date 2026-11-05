"""P6 运行中输入双队列（query_inbox）测试——借鉴 dsh-agent-loop inbox 语义。

- DAO：add/pending(FIFO)/claim/discard/oldest_pending_followup；
- 节点边界领取：generate 前 _drain_inbox 领取 steer+inject 并入工作问题；
- 终态自动开轮：_discard_pending_and_start_followup 领取最早 followup
  落新 query 行、重锚其余 pending、发 followup_started 事件、起图（mock）。

隔离说明：runner/nodes 内 `store_db.get_conn()` 无参会落到 config.app_db_path()
（env AITOSQL_DATA），本文件用 tmp_env 夹具把数据目录指到 tmp_path，绝不碰真实库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.graph import nodes as graph_nodes
from app.graph import runner as graph_runner
from app.ids import new_query_id
from app.store import datasources as store_datasources
from app.store import db as store_db
from app.store import inbox as store_inbox
from app.store import queries as store_queries
from app.store import sessions as store_sessions
from app.store import settings as store_settings


@pytest.fixture()
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """路径隔离的临时数据目录（get_conn() 无参也指向它）。"""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AITOSQL_DATA", str(data))
    db_path = data / "app.db"
    store_db.run_migrations(db_path)
    conn = store_db.connect(db_path)
    store_settings.ensure_defaults(conn)
    conn.commit()
    yield conn
    conn.close()


def _mk_query(conn: sqlite3.Connection, *, question: str = "查收入") -> str:
    ds_id = store_datasources.save_datasource(
        conn,
        name="ds_inbox",
        dialect="mysql",
        mode="online",
        host="127.0.0.1",
        port=3306,
        db_name="d",
        username="u",
        password_enc=None,
    )
    session = store_sessions.save_session(conn, datasource_id=ds_id)
    qid = new_query_id()
    store_queries.save_query(
        conn,
        query_id=qid,
        session_id=session.id,
        datasource_id=ds_id,
        dialect="mysql",
        question=question,
    )
    conn.commit()  # 释放写事务：图侧走独立连接（WAL 单写者）
    return qid


def test_inbox_fifo_claim_discard(tmp_env: sqlite3.Connection) -> None:
    qid = _mk_query(tmp_env)
    store_inbox.add_inbox(tmp_env, query_id=qid, kind="steer", content="先看A")
    store_inbox.add_inbox(tmp_env, query_id=qid, kind="inject", content="补充：B 表已归档")
    store_inbox.add_inbox(tmp_env, query_id=qid, kind="followup", content="再看C")

    rows = store_inbox.pending_inbox(tmp_env, qid)
    assert [r["content"] for r in rows] == ["先看A", "补充：B 表已归档", "再看C"]  # FIFO
    # 只取 steer/inject 边界
    edge = store_inbox.pending_inbox(tmp_env, qid, kinds=("steer", "inject"))
    assert [r["kind"] for r in edge] == ["steer", "inject"]
    store_inbox.claim_inbox(tmp_env, [int(r["id"]) for r in edge])
    assert [r["kind"] for r in store_inbox.pending_inbox(tmp_env, qid)] == ["followup"]
    assert store_inbox.discard_inbox(tmp_env, qid) == 1
    assert store_inbox.pending_inbox(tmp_env, qid) == []


async def test_drain_inbox_appends_and_claims(tmp_env: sqlite3.Connection) -> None:
    """节点边界：steer→「用户引导」、inject→「补充上下文」并入文本，领取即持久迁移。"""
    qid = _mk_query(tmp_env)
    store_inbox.add_inbox(tmp_env, query_id=qid, kind="steer", content="按日粒度")
    store_inbox.add_inbox(tmp_env, query_id=qid, kind="inject", content="账期字段是 batch_no")
    tmp_env.commit()  # 释放写事务后图侧连接才能领取

    emitted: list[dict] = []

    class _Ctx:
        query_id = qid
        db_path = None  # get_conn(None) → tmp_env 的 AITOSQL_DATA

        async def emit(self, event_type: str, payload: dict, persist: bool = True) -> int:
            emitted.append(payload)
            return 1

    note = await graph_nodes._drain_inbox(_Ctx())
    assert "用户引导：按日粒度" in note
    assert "补充上下文：账期字段是 batch_no" in note
    assert emitted and emitted[0]["type"] == "inbox_claimed"
    assert store_inbox.pending_inbox(tmp_env, qid) == []  # 已领取
    # 再次领取为空（幂等）
    assert await graph_nodes._drain_inbox(_Ctx()) == ""


def _record(bucket: list[str]):
    async def _start(qid: str) -> None:
        bucket.append(qid)

    return _start


async def test_finalize_starts_followup(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """终态自动开轮：最早 followup 领出新 query，其余 pending 重锚，steer/inject 丢弃。"""
    qid = _mk_query(tmp_env, question="第一轮")
    store_inbox.add_inbox(tmp_env, query_id=qid, kind="followup", content="第二轮问题")
    store_inbox.add_inbox(tmp_env, query_id=qid, kind="followup", content="第三轮问题")
    store_inbox.add_inbox(tmp_env, query_id=qid, kind="steer", content="来不及领了")
    tmp_env.commit()  # 释放写事务后 runner 连接才能领取/重锚

    started: list[str] = []
    monkeypatch.setattr(graph_runner, "start_query", _record(started))
    monkeypatch.setattr(graph_runner, "_publish", lambda *a, **k: None)

    await graph_runner._discard_pending_and_start_followup(qid)

    assert len(started) == 1
    new_q = store_queries.get_query(tmp_env, started[0])
    assert new_q.question == "第二轮问题"
    rows = tmp_env.execute(
        "SELECT kind, content, status, query_id FROM query_inbox ORDER BY id"
    ).fetchall()
    by_content = {r["content"]: r for r in rows}
    assert by_content["第二轮问题"]["status"] == "claimed"
    assert by_content["第二轮问题"]["query_id"] == qid  # 锚点不动（claimed 历史保留）
    # 队列迁移：剩余 followup 重锚到新查询
    assert by_content["第三轮问题"]["status"] == "pending"
    assert by_content["第三轮问题"]["query_id"] == started[0]
    assert by_content["来不及领了"]["status"] == "discarded"  # steer 终态丢弃


async def test_finalize_no_followup_noop(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    qid = _mk_query(tmp_env)
    started: list[str] = []
    monkeypatch.setattr(graph_runner, "start_query", _record(started))
    await graph_runner._discard_pending_and_start_followup(qid)
    assert started == []


def test_active_query_in_session(tmp_env: sqlite3.Connection) -> None:
    qid = _mk_query(tmp_env)
    q = store_queries.get_query(tmp_env, qid)
    assert store_queries.active_query_in_session(tmp_env, str(q.session_id)) == qid
    store_queries.update_query_result(tmp_env, qid, status="done")
    assert store_queries.active_query_in_session(tmp_env, str(q.session_id)) is None
