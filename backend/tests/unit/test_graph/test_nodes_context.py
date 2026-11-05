"""多轮上下文用例：检索问题拼接（_retrieval_question）——当前句 + 上一轮问题。"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from app.graph.nodes import _retrieval_question
from app.ids import new_query_id
from app.store import datasources as store_datasources
from app.store import queries as store_queries
from app.store import sessions as store_sessions


def _seed_two_rounds(conn: sqlite3.Connection) -> tuple[str, str]:
    ds_id = store_datasources.save_datasource(
        conn,
        name="t",
        dialect="mysql",
        mode="online",
        host="127.0.0.1",
        port=3306,
        db_name="biz",
        username="u",
        password_enc=None,
    )
    session = store_sessions.save_session(conn, datasource_id=ds_id)
    qid1 = new_query_id()
    store_queries.save_query(
        conn, query_id=qid1, session_id=session.id, datasource_id=ds_id, dialect="mysql",
        question="查询调度任务的后续一级依赖",
    )
    qid2 = new_query_id()
    store_queries.save_query(
        conn, query_id=qid2, session_id=session.id, datasource_id=ds_id, dialect="mysql",
        question="需要加上 batch_no 批次日期",
    )
    return session.id, qid2


def test_retrieval_question_concat_previous(tmp_db: sqlite3.Connection) -> None:
    """修复轮检索：上一轮问题拼在前面（治检索焦点漂移），当前句保留。"""
    sid, qid2 = _seed_two_rounds(tmp_db)
    state = {"question": "需要加上 batch_no 批次日期", "session_id": sid}
    ctx = SimpleNamespace(query_id=qid2)
    q = _retrieval_question(tmp_db, state, ctx)  # type: ignore[arg-type]
    assert "后续一级依赖" in q  # 上一轮问题进入检索
    assert "batch_no" in q  # 当前句保留
    assert q.index("后续一级依赖") < q.index("batch_no")  # 上一轮在前


def test_retrieval_question_first_round_unchanged(tmp_db: sqlite3.Connection) -> None:
    """首轮/无会话：检索问题原样返回，不拼接。"""
    ds_id = store_datasources.save_datasource(
        tmp_db, name="t", dialect="mysql", mode="online",
        host="127.0.0.1", port=3306, db_name="biz", username="u", password_enc=None,
    )
    session = store_sessions.save_session(tmp_db, datasource_id=ds_id)
    qid = new_query_id()
    store_queries.save_query(
        tmp_db, query_id=qid, session_id=session.id, datasource_id=ds_id, dialect="mysql",
        question="首轮问题",
    )
    ctx = SimpleNamespace(query_id=qid)
    state = {"question": "首轮问题", "session_id": session.id}
    assert _retrieval_question(tmp_db, state, ctx) == "首轮问题"  # type: ignore[arg-type]
    # 无会话
    assert _retrieval_question(tmp_db, {"question": "孤立问题", "session_id": ""}, ctx) == "孤立问题"  # type: ignore[arg-type]
