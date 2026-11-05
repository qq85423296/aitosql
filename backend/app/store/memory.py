"""记忆双层 DAO（memory_log 流水账 / memory 总账 / memory_watermark，D-14 / SSOT §12.6）。

- 流水账追加只写、从不修改；三入口：澄清裁决/假设修正/存样例同步直写（零损耗），
  查询完成异步 flush（失败静默下轮重试）由 graph 节点调用 flush_sync；
- 水位线增量合并：≥ memory.consolidate_min_gap_min(30min) 且有增量才走 route.ai；
  合并/摘要等 LLM 调用经显式参数注入（D2/D6：store 不 import llm）；
- 会话压缩：先抢救 flush，**flush 失败压缩推迟**（spec §5.3 强制约束）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from app.errors import E_NOT_FOUND, BizError
from app.identifiers import require_id
from app.store.models import Memory, MemoryLog

# ---------- Layer 1 流水账（追加只写） ----------


def flush_sync(
    conn: sqlite3.Connection,
    *,
    kind: str,
    fact: str,
    datasource_id: int | None = None,
    source_query_id: str | None = None,
) -> int:
    """同步直写流水账（入口 1：澄清裁决/假设修正/存样例；不经 LLM，零损耗）。"""
    if kind not in ("caliber", "correction", "quirk", "preference"):
        raise BizError("E4420", f"非法记忆类别: {kind}", 422)
    cur = conn.execute(
        "INSERT INTO memory_log(datasource_id, kind, fact, source_query_id) VALUES (?, ?, ?, ?)",
        (datasource_id, kind, fact, source_query_id),
    )
    return require_id(cur.lastrowid)


def query_memory_log(conn: sqlite3.Connection, limit: int = 200) -> list[MemoryLog]:
    """流水账只读视图（设置页 FR-C7）。"""
    rows = conn.execute("SELECT * FROM memory_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [MemoryLog.from_row(r) for r in rows]


def query_log_since(conn: sqlite3.Connection, last_log_id: int) -> list[MemoryLog]:
    rows = conn.execute("SELECT * FROM memory_log WHERE id > ? ORDER BY id", (last_log_id,)).fetchall()
    return [MemoryLog.from_row(r) for r in rows]


# ---------- Layer 2 总账 ----------


def query_memory_grouped(conn: sqlite3.Connection) -> dict[str, list[Memory]]:
    """GET /memory：按 topic 分组返回。"""
    rows = conn.execute("SELECT * FROM memory ORDER BY topic, id").fetchall()
    grouped: dict[str, list[Memory]] = {}
    for r in rows:
        m = Memory.from_row(r)
        grouped.setdefault(m.topic, []).append(m)
    return grouped


def save_memory(
    conn: sqlite3.Connection,
    *,
    scope: str,
    datasource_id: int | None,
    topic: str,
    content: str,
    token_est: int = 0,
) -> int:
    cur = conn.execute(
        "INSERT INTO memory(scope, datasource_id, topic, content, token_est, updated_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (scope, datasource_id, topic, content, token_est),
    )
    return require_id(cur.lastrowid)


def get_memory(conn: sqlite3.Connection, memory_id: int) -> Memory:
    row = conn.execute("SELECT * FROM memory WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"记忆条目不存在: {memory_id}", 404)
    return Memory.from_row(row)


def update_memory(
    conn: sqlite3.Connection, memory_id: int, *, topic: str | None = None, content: str | None = None
) -> Memory:
    get_memory(conn, memory_id)
    if topic is not None:
        conn.execute(
            "UPDATE memory SET topic = ?, updated_at = datetime('now') WHERE id = ?", (topic, memory_id)
        )
    if content is not None:
        conn.execute(
            "UPDATE memory SET content = ?, updated_at = datetime('now') WHERE id = ?", (content, memory_id)
        )
    return get_memory(conn, memory_id)


def toggle_memory(conn: sqlite3.Connection, memory_id: int, enabled: bool | None) -> Memory:
    """停用即不注入（人工管理入口，改动下次生成即生效）。

    P1-8：`enabled=None` 时读当前态取反（前端无 body 的 toggle(id) 调用形态）；
    显式 True/False 为幂等设定。
    """
    current = get_memory(conn, memory_id)
    target = (not current.enabled) if enabled is None else bool(enabled)
    conn.execute(
        "UPDATE memory SET enabled = ?, updated_at = datetime('now') WHERE id = ?", (int(target), memory_id)
    )
    return get_memory(conn, memory_id)


def delete_memory(conn: sqlite3.Connection, memory_id: int) -> None:
    cur = conn.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
    if cur.rowcount == 0:
        raise BizError(E_NOT_FOUND, f"记忆条目不存在: {memory_id}", 404)


def query_injectable(conn: sqlite3.Connection, datasource_id: int | None) -> list[Memory]:
    """信封第 3 段取材：当前数据源 scope + global，enabled 且按 topic 排序。"""
    rows = conn.execute(
        "SELECT * FROM memory WHERE enabled = 1"
        " AND (scope = 'global' OR (scope = 'datasource' AND datasource_id = ?))"
        " ORDER BY topic, updated_at",
        (datasource_id,),
    ).fetchall()
    return [Memory.from_row(r) for r in rows]


# ---------- 水位线合并 ----------


def _get_watermark(conn: sqlite3.Connection) -> sqlite3.Row:
    conn.execute("INSERT OR IGNORE INTO memory_watermark(id, last_log_id) VALUES (1, 0)")
    return conn.execute("SELECT * FROM memory_watermark WHERE id = 1").fetchone()


def consolidate_prepare(
    conn: sqlite3.Connection,
    *,
    min_gap_min: int,
    force: bool = False,
) -> dict[str, Any] | None:
    """合并读相（F-3 拆相）：水位线/间隔裁决 + 增量日志 + 现有总账。

    返回 None = 本轮跳过；否则返回 {new_logs, existing, base_log_id}。
    本函数返回后连接即应释放——LLM 调用（merge_fn）必须在无连接/事务持有时进行
    （单 worker 下持有连接跨网络往返会阻塞全部请求）。
    """
    wm = _get_watermark(conn)
    last_at = wm["last_consolidate_at"]
    if not force and last_at:
        last_dt = datetime.fromisoformat(last_at)
        if datetime.now() - last_dt < timedelta(minutes=min_gap_min):
            return None
    new_logs = query_log_since(conn, int(wm["last_log_id"]))
    if not new_logs and not force:
        return None
    existing = [m for ms in query_memory_grouped(conn).values() for m in ms]
    return {"new_logs": new_logs, "existing": existing, "base_log_id": int(wm["last_log_id"])}


def consolidate_commit(
    conn: sqlite3.Connection,
    *,
    prepared: dict[str, Any],
    produced: list[dict[str, Any]],
    max_tokens: int,
) -> dict[str, int]:
    """合并写相（F-3 拆相）：落合并产物 + 容量裁剪 + 水位线推进（单事务提交）。"""
    merged = 0
    for item in produced:
        save_memory(
            conn,
            scope=item.get("scope", "global"),
            datasource_id=item.get("datasource_id"),
            topic=item.get("topic", "杂项"),
            content=item.get("content", ""),
            token_est=int(item.get("token_est", 0)),
        )
        merged += 1
    dropped = _trim_overcapacity(conn, max_tokens)
    new_logs: list[MemoryLog] = prepared["new_logs"]
    max_id = max((log.id for log in new_logs), default=int(prepared["base_log_id"]))
    conn.execute(
        "UPDATE memory_watermark SET last_log_id = ?, last_consolidate_at = datetime('now') WHERE id = 1",
        (max_id,),
    )
    return {"merged": merged, "dropped": dropped}


async def consolidate(
    conn: sqlite3.Connection,
    *,
    merge_fn: Callable[[list[MemoryLog], list[Memory]], Awaitable[list[dict[str, Any]]]],
    min_gap_min: int,
    max_tokens: int,
    force: bool = False,
) -> dict[str, int]:
    """水位线增量合并：返回 `{merged, dropped}`（C1.6）。

    merge_fn（route.ai 小模型）由调用方注入；失败静默（调用侧 try 住，下轮重试 E16）。
    总账超 max_tokens 裁最旧未命中条目。
    注：api 层使用 prepare/commit 拆相变体（F-3：不持连接跨 LLM await）；本函数
    保留给测试与「调用方自备短事务」场景。
    """
    prepared = consolidate_prepare(conn, min_gap_min=min_gap_min, force=force)
    if prepared is None:
        return {"merged": 0, "dropped": 0}
    produced = await merge_fn(prepared["new_logs"], prepared["existing"])
    return consolidate_commit(conn, prepared=prepared, produced=produced, max_tokens=max_tokens)


def _trim_overcapacity(conn: sqlite3.Connection, max_tokens: int) -> int:
    """容量裁剪：总账超 max_tokens 时裁最旧条目（updated_at 升序）。"""
    rows = conn.execute("SELECT id, token_est FROM memory ORDER BY updated_at, id").fetchall()
    total = sum(int(r["token_est"]) for r in rows)
    dropped = 0
    for r in rows:
        if total <= max_tokens:
            break
        conn.execute("DELETE FROM memory WHERE id = ?", (r["id"],))
        total -= int(r["token_est"])
        dropped += 1
    return dropped


# ---------- 会话压缩（先抢救，flush 失败压缩推迟） ----------


async def compact_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    msg_count: int,
    compact_msgs: int,
    keep_msgs: int,
    flush_fn: Callable[[], Awaitable[bool]],
    summarize_fn: Callable[[str | None, list[dict[str, Any]]], Awaitable[str]],
    queries_fn: Callable[[int], list[dict[str, Any]]],
    current_summary: str | None,
    pressure_triggered: bool = False,
) -> dict[str, Any]:
    """压缩编排（D-14）：消息数 > compact_msgs **或** token 压力触发（P1 双阈值，
    借鉴 dsh-compaction-basic 的压力驱动语义）；保留最近 keep_msgs 轮完整。

    flush_fn = 待压缩轮次事实抢救（返回 False 视为 flush 失败 → 压缩推迟，强制约束）；
    summarize_fn（route.ai）与 queries_fn（取待压缩轮次）由调用方注入。
    """
    if msg_count <= compact_msgs and not pressure_triggered:
        return {"compressed": False, "reason": "below_threshold"}
    flushed = await flush_fn()
    if not flushed:
        return {"compressed": False, "reason": "flush_failed"}  # 压缩推迟
    pending = queries_fn(msg_count - keep_msgs)
    if not pending:
        # 压力触发但无可压缩头部（轮次 ≤ keep_msgs）：不发起空摘要调用
        return {"compressed": False, "reason": "nothing_to_compact"}
    new_summary = await summarize_fn(current_summary, pending)
    from app.store import sessions as store_sessions

    store_sessions.save_summary(
        conn, session_id, summary=new_summary, summary_upto=pending[-1]["query_id"] if pending else ""
    )
    return {"compressed": True, "compressed_rounds": len(pending), "keep": keep_msgs}
