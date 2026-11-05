"""记忆模块用例（D-14 / §12.6）：flush 直写 / 水位线合并 / 压缩抢救（flush 失败压缩推迟）。"""

from __future__ import annotations

import sqlite3

from app.store import memory as store_memory
from app.store import sessions as store_sessions


def test_flush_sync_append_only(tmp_db: sqlite3.Connection) -> None:
    mid = store_memory.flush_sync(tmp_db, kind="caliber", fact="「收入」口径=含税", datasource_id=1)
    assert mid >= 1
    logs = store_memory.query_memory_log(tmp_db)
    assert logs[0].fact == "「收入」口径=含税"  # 追加只写


async def test_consolidate_watermark_incremental(tmp_db: sqlite3.Connection) -> None:
    store_memory.flush_sync(tmp_db, kind="quirk", fact="orders 表有 is_test 脏数据")

    async def merge_fn(logs, existing):
        return [{"scope": "global", "topic": "表怪癖", "content": "orders.is_test 需过滤", "token_est": 10}]

    out = await store_memory.consolidate(
        tmp_db, merge_fn=merge_fn, min_gap_min=0, max_tokens=4000, force=True
    )
    assert out == {"merged": 1, "dropped": 0}
    # 水位线推进后无增量 → 零合并
    out2 = await store_memory.consolidate(
        tmp_db, merge_fn=merge_fn, min_gap_min=0, max_tokens=4000, force=False
    )
    assert out2 == {"merged": 0, "dropped": 0}


async def test_compact_deferred_on_flush_failure(tmp_db: sqlite3.Connection) -> None:
    """压缩抢救强制约束：flush 失败 → 压缩推迟（spec §5.3）。"""
    store_sessions.save_session(tmp_db, datasource_id=None)

    async def flush_fail() -> bool:
        return False

    async def summarize(prev, pending):
        raise AssertionError("flush 失败时不应进入压缩")

    out = await store_memory.compact_session(
        tmp_db,
        "s_any",
        msg_count=45,
        compact_msgs=40,
        keep_msgs=6,
        flush_fn=flush_fail,
        summarize_fn=summarize,
        queries_fn=lambda n: [],
        current_summary=None,
    )
    assert out == {"compressed": False, "reason": "flush_failed"}


async def test_compact_success(tmp_db: sqlite3.Connection) -> None:
    s = store_sessions.save_session(tmp_db, datasource_id=None)

    async def flush_ok() -> bool:
        return True

    async def summarize(prev, pending):
        return "新滚动摘要"

    out = await store_memory.compact_session(
        tmp_db,
        s.id,
        msg_count=45,
        compact_msgs=40,
        keep_msgs=6,
        flush_fn=flush_ok,
        summarize_fn=summarize,
        queries_fn=lambda n: [{"query_id": "q_x"}] * max(n, 0),
        current_summary=None,
    )
    assert out["compressed"] is True and out["keep"] == 6
    assert store_sessions.get_session(tmp_db, s.id).summary == "新滚动摘要"


def test_toggle_memory_excludes_inject(tmp_db: sqlite3.Connection) -> None:
    mid = store_memory.save_memory(
        tmp_db, scope="global", datasource_id=None, topic="口径", content="收入=含税", token_est=5
    )
    store_memory.toggle_memory(tmp_db, mid, False)
    assert store_memory.query_injectable(tmp_db, None) == []  # 停用即不注入


async def test_compact_pressure_triggered_below_count(tmp_db: sqlite3.Connection) -> None:
    """P1 双阈值：条数未超但 token 压力触达 → 仍然压缩（dsh-compaction-basic 压力语义）。"""
    s = store_sessions.save_session(tmp_db, datasource_id=None)

    async def flush_ok() -> bool:
        return True

    async def summarize(prev, pending):
        return "压力触发的摘要"

    out = await store_memory.compact_session(
        tmp_db,
        s.id,
        msg_count=10,
        compact_msgs=40,
        keep_msgs=6,
        flush_fn=flush_ok,
        summarize_fn=summarize,
        queries_fn=lambda n: [{"query_id": "q_x"}] * max(n, 0),
        current_summary=None,
        pressure_triggered=True,
    )
    assert out["compressed"] is True
    assert store_sessions.get_session(tmp_db, s.id).summary == "压力触发的摘要"


async def test_compact_pressure_nothing_to_compact(tmp_db: sqlite3.Connection) -> None:
    """P1 边界：压力触达但轮次 ≤ keep_msgs（无可压缩头部）→ 不发起空摘要调用。"""
    s = store_sessions.save_session(tmp_db, datasource_id=None)

    async def flush_ok() -> bool:
        return True

    async def summarize(prev, pending):
        raise AssertionError("无待压缩轮次时不应调用摘要")

    out = await store_memory.compact_session(
        tmp_db,
        s.id,
        msg_count=5,
        compact_msgs=40,
        keep_msgs=6,
        flush_fn=flush_ok,
        summarize_fn=summarize,
        queries_fn=lambda n: [],
        current_summary=None,
        pressure_triggered=True,
    )
    assert out == {"compressed": False, "reason": "nothing_to_compact"}
