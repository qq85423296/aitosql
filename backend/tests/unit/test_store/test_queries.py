"""查询链路 DAO 用例：幂等查重 / CAS 裁决互斥 / 事件 seq 单调 / E5 聚合
（冻结包络 {session, items, last_seq_by_query}，P0-1/R-3）。"""

from __future__ import annotations

import sqlite3

from app.ids import new_query_id
from app.store import datasources as store_datasources
from app.store import queries as store_queries
from app.store import sessions as store_sessions


def _seed(conn: sqlite3.Connection) -> tuple[int, str, str]:
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
    qid = new_query_id()
    store_queries.save_query(
        conn, query_id=qid, session_id=session.id, datasource_id=ds_id, dialect="mysql", question="本月收入？"
    )
    return ds_id, session.id, qid


def test_idempotent_lookup(tmp_db: sqlite3.Connection) -> None:
    _ds, _sid, qid = _seed(tmp_db)
    found = store_queries.find_by_idempotent_key(tmp_db, qid)
    assert found is not None and found.id == qid  # 同 client_request_id 直返同 query_id
    assert store_queries.find_by_idempotent_key(tmp_db, "q_nonexistent") is None


def test_cas_gate_conflict(tmp_db: sqlite3.Connection) -> None:
    _ds, _sid, qid = _seed(tmp_db)
    # running 态裁决 → CAS 拒绝（仅 waiting_* 受理，409 语义）
    assert not store_queries.cas_status(tmp_db, qid, expect=("waiting_clarify",), to="running")
    tmp_db.execute("UPDATE query SET status = 'waiting_clarify' WHERE id = ?", (qid,))
    assert store_queries.cas_status(tmp_db, qid, expect=("waiting_clarify",), to="running")
    # 二次裁决互斥
    assert not store_queries.cas_status(tmp_db, qid, expect=("waiting_clarify",), to="running")


def test_event_seq_monotonic(tmp_db: sqlite3.Connection) -> None:
    _ds, _sid, qid = _seed(tmp_db)
    s1 = store_queries.append_event(tmp_db, query_id=qid, event_type="stage", payload={"stage": "supply"})
    s2 = store_queries.append_event(tmp_db, query_id=qid, event_type="sql", payload={"sql": "SELECT 1"})
    assert (s1, s2) == (1, 2)
    events = store_queries.query_events(tmp_db, qid, after_seq=1)
    assert [e.seq for e in events] == [2]  # Last-Event-ID 增量接缝


def test_event_payload_truncation(tmp_db: sqlite3.Connection) -> None:
    _ds, _sid, qid = _seed(tmp_db)
    big = {"blob": "x" * (20 * 1024)}
    store_queries.append_event(tmp_db, query_id=qid, event_type="result_meta", payload=big, max_payload_kb=16)
    ev = store_queries.query_events(tmp_db, qid)[0]
    assert ev.payload.get("truncated") is True  # 超限截断标注（C0）


def test_append_event_concurrent_writers(tmp_path) -> None:
    """F12：stop 的 stopped 事件与运行中图的 emit 走各自连接并发——两连接可读出
    相同 MAX(seq) 撞 PK(query_id,seq)，或 WAL 快照冲突（locked/busy）；该竞态曾让
    stop API 500(E5000) 且停止事件丢失。修复后：冲突内部重试，无异常逃逸，
    双侧事件全数落库且 seq 连续唯一。"""
    import threading

    from app.store import db as store_db

    db_path = tmp_path / "app.db"
    store_db.run_migrations(db_path)
    seed_conn = store_db.connect(db_path)
    _ds, _sid, qid = _seed(seed_conn)
    seed_conn.commit()
    seed_conn.close()

    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def _writer(kind: str, count: int) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(count):
                with store_db.get_conn(db_path) as conn:
                    store_queries.append_event(
                        conn, query_id=qid, event_type=kind, payload={"type": kind, "i": i}
                    )
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_writer, args=("stage", 15)),
        threading.Thread(target=_writer, args=("stopped", 15)),
    ]
    for t in threads:
        t.start()
    barrier.wait(timeout=10)
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"并发写逃逸异常: {errors!r}"
    with store_db.get_conn(db_path) as conn:
        seqs = sorted(e.seq for e in store_queries.query_events(conn, qid))
    assert seqs == list(range(1, 31))


def test_e5_snapshot_aggregates(tmp_db: sqlite3.Connection) -> None:
    """E5 冻结包络（P0-1/P1-1/R-3）：{session, items, last_seq_by_query}；
    单项带 preview + recovered（无逐条 last_seq——续播接缝统一走聚合 map）。"""
    _ds, sid, qid = _seed(tmp_db)
    store_queries.append_event(tmp_db, query_id=qid, event_type="stage", payload={})
    store_queries.append_event(tmp_db, query_id=qid, event_type="sql", payload={"sql": "SELECT 1"})
    store_queries.save_preview(
        tmp_db, query_id=qid, columns=[{"name": "c", "type": ""}], rows=[[1]], truncated=False
    )
    snap = store_queries.query_session_snapshot(tmp_db, sid)
    assert set(snap.keys()) == {"session", "items", "last_seq_by_query"}
    assert snap["session"]["id"] == sid
    assert snap["last_seq_by_query"] == {qid: 2}  # 装配-续播接缝（P0-1 聚合 map）
    assert len(snap["items"]) == 1
    item = snap["items"][0]
    assert "last_seq" not in item  # 旧形状字段已移除
    assert item["recovered"] is False  # R-3：默认未恢复
    assert item["preview"]["rows"] == [[1]]
    assert item["clarify_choice"] is None  # 门回显字段存在


def test_e5_snapshot_recovered_flag(tmp_db: sqlite3.Connection) -> None:
    """R-3：recovered=1 落库后快照透出 true（D03 恢复路径标记）。"""
    _ds, sid, qid = _seed(tmp_db)
    store_queries.update_query_result(tmp_db, qid, recovered=1)
    snap = store_queries.query_session_snapshot(tmp_db, sid)
    assert snap["items"][0]["recovered"] is True


def test_e5_snapshot_empty_session(tmp_db: sqlite3.Connection) -> None:
    """E5 空会话（0 查询）仍带 session 键（前端恢复态标题/摘要展示）。"""
    session = store_sessions.save_session(tmp_db, datasource_id=None)
    snap = store_queries.query_session_snapshot(tmp_db, session.id)
    assert snap["session"]["id"] == session.id
    assert snap["items"] == [] and snap["last_seq_by_query"] == {}


def test_e5_snapshot_trajectory_fields(tmp_db: sqlite3.Connection) -> None:
    """E5 过程回放补齐轨迹字段（DSH 轨迹面板）：reasoning_full 各轮思考全文、
    sql_rounds 逐轮 SQL、retries 重试、inbox_notes 引导、hit_tables 命中表。"""
    _ds, sid, qid = _seed(tmp_db)
    store_queries.append_event(
        tmp_db, query_id=qid, event_type="reasoning_full",
        payload={"round": 0, "text": "先想依赖方向"},
    )
    store_queries.append_event(
        tmp_db, query_id=qid, event_type="reasoning_full",
        payload={"round": 1, "text": "修正：加批次限定"},
    )
    store_queries.append_event(
        tmp_db, query_id=qid, event_type="sql", payload={"sql": "SELECT 1", "round": 0}
    )
    store_queries.append_event(
        tmp_db, query_id=qid, event_type="sql", payload={"sql": "SELECT 2", "round": 1}
    )
    store_queries.append_event(
        tmp_db, query_id=qid, event_type="llm_retry",
        payload={"attempt": 1, "delay_ms": 500, "failure_class": "TIMEOUT", "route": "1:deepseek"},
    )
    store_queries.append_event(
        tmp_db, query_id=qid, event_type="inbox_claimed",
        payload={"items": [{"kind": "steer", "content": "按日粒度"}]},
    )
    store_queries.append_event(
        tmp_db, query_id=qid, event_type="meta",
        payload={"phase": "post_supply", "hit_tables": ["dacp_dataflow_job"]},
    )
    snap = store_queries.query_session_snapshot(tmp_db, sid)
    proc = snap["items"][0]["process"]
    assert [r["round"] for r in proc["reasoning_full"]] == [0, 1]
    assert "修正：加批次限定" in proc["reasoning_full"][1]["text"]
    assert proc["sql_rounds"] == {"0": "SELECT 1", "1": "SELECT 2"}
    assert proc["retries"][0]["failure_class"] == "TIMEOUT"
    assert proc["inbox_notes"] == [{"kind": "steer", "content": "按日粒度"}]
    assert proc["hit_tables"] == ["dacp_dataflow_job"]


def test_session_auto_title_once(tmp_db: sqlite3.Connection) -> None:
    _ds, sid, _qid = _seed(tmp_db)
    store_sessions.maybe_auto_title(tmp_db, sid, "上个月各渠道的订单量和销售额环比是多少")
    titled = store_sessions.get_session(tmp_db, sid)
    assert titled.title == "上个月各渠道的订单量和销售额环比是多"[:18]  # 前 18 字（C6.7）
    store_sessions.update_session(tmp_db, sid, title="人工命名")
    store_sessions.maybe_auto_title(tmp_db, sid, "新的问题不会再覆盖")
    assert store_sessions.get_session(tmp_db, sid).title == "人工命名"  # 重命名后不改写


def test_recent_rounds(tmp_db: sqlite3.Connection) -> None:
    """近期轮次 DAO：排除当前轮、升序返回、limit 生效、跨会话隔离。"""
    _ds, sid, qid1 = _seed(tmp_db)
    store_queries.update_query_result(tmp_db, qid1, status="done", sql_final="SELECT 1")
    qid2 = new_query_id()
    store_queries.save_query(
        tmp_db, query_id=qid2, session_id=sid, datasource_id=_ds, dialect="mysql", question="加批次"
    )
    store_queries.update_query_result(tmp_db, qid2, status="done", sql_final="SELECT 2")
    qid3 = new_query_id()
    store_queries.save_query(
        tmp_db, query_id=qid3, session_id=sid, datasource_id=_ds, dialect="mysql", question="当前轮"
    )
    rounds = store_queries.recent_rounds(tmp_db, sid, exclude_query_id=qid3, limit=6)
    assert [r["question"] for r in rounds] == ["本月收入？", "加批次"]
    assert [r["sql_final"] for r in rounds] == ["SELECT 1", "SELECT 2"]
    assert [r["status"] for r in rounds] == ["done", "done"]
    # limit=1 只取最近一轮
    one = store_queries.recent_rounds(tmp_db, sid, exclude_query_id=qid3, limit=1)
    assert len(one) == 1 and one[0]["sql_final"] == "SELECT 2"
    # limit<=0 / 空会话 → 空
    assert store_queries.recent_rounds(tmp_db, sid, exclude_query_id=qid3, limit=0) == []
    assert store_queries.recent_rounds(tmp_db, "", exclude_query_id=qid3, limit=6) == []
