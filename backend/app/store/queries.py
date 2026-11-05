"""查询链路 DAO（query / result_preview / query_event）。

铁律：
- 幂等：POST /queries 同 client_request_id 重发直返同 query_id（C2）；
- 事件溯源：append_event 先落库再推送（D-06），seq 单调（PK(query_id, seq)）；
- E5 快照聚合防 N+1（单次往返 ×3：query + preview + events，C1.1）；
- 单事件 payload ≤ sse.event_payload_kb(16KB)，超出截断并附 truncated:true（C0）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.errors import E_NOT_FOUND, BizError
from app.store.models import Query, QueryEvent, ResultPreview

TERMINAL_STATUS = ("done", "failed", "stopped")
WAITING_STATUS = ("waiting_clarify", "waiting_confirm")


def get_query(conn: sqlite3.Connection, query_id: str) -> Query:
    row = conn.execute("SELECT * FROM query WHERE id = ?", (query_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"查询不存在: {query_id}", 404)
    return Query.from_row(row)


def find_by_idempotent_key(conn: sqlite3.Connection, client_request_id: str) -> Query | None:
    """幂等查重：client_request_id = query_id（三键合一，D-22）。"""
    row = conn.execute("SELECT * FROM query WHERE id = ?", (client_request_id,)).fetchone()
    return Query.from_row(row) if row else None


def save_query(
    conn: sqlite3.Connection,
    *,
    query_id: str,
    session_id: str,
    datasource_id: int,
    dialect: str,
    question: str,
) -> Query:
    conn.execute(
        "INSERT INTO query(id, session_id, datasource_id, dialect, question) VALUES (?, ?, ?, ?, ?)",
        (query_id, session_id, datasource_id, dialect, question),
    )
    return get_query(conn, query_id)


def update_query_result(
    conn: sqlite3.Connection,
    query_id: str,
    *,
    status: str | None = None,
    contract: dict | None = None,
    sql_final: str | None = None,
    rounds: int | None = None,
    tokens: int | None = None,
    scan_rows: int | None = None,
    data_max_time: str | None = None,
    duration_ms: int | None = None,
    result_unverified: int | None = None,
    clarify_choice: str | None = None,
    recovered: int | None = None,
) -> None:
    sets: list[str] = []
    params: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if contract is not None:
        sets.append("contract_json = ?")
        params.append(json.dumps(contract, ensure_ascii=False))
    if sql_final is not None:
        sets.append("sql_final = ?")
        params.append(sql_final)
    for col, val in (
        ("rounds", rounds),
        ("tokens", tokens),
        ("scan_rows", scan_rows),
        ("data_max_time", data_max_time),
        ("duration_ms", duration_ms),
        ("result_unverified", result_unverified),
        ("clarify_choice", clarify_choice),
        ("recovered", recovered),
    ):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if not sets:
        return
    params.append(query_id)
    conn.execute(f"UPDATE query SET {', '.join(sets)} WHERE id = ?", params)


def cas_status(conn: sqlite3.Connection, query_id: str, *, expect: tuple[str, ...], to: str) -> bool:
    """CAS 状态迁移（D-05/D-22：resume/stop 互斥；仅 waiting_* 受理裁决）。"""
    marks = ",".join("?" * len(expect))
    cur = conn.execute(
        f"UPDATE query SET status = ? WHERE id = ? AND status IN ({marks})", (to, query_id, *expect)
    )
    return cur.rowcount == 1


def query_running(conn: sqlite3.Connection) -> list[Query]:
    """D03 启动清扫扫描面：status=running 的 query。"""
    rows = conn.execute("SELECT * FROM query WHERE status = 'running'").fetchall()
    return [Query.from_row(r) for r in rows]


def query_waiting(conn: sqlite3.Connection) -> list[Query]:
    rows = conn.execute(
        "SELECT * FROM query WHERE status IN ('waiting_clarify','waiting_confirm')"
    ).fetchall()
    return [Query.from_row(r) for r in rows]


# ---------- result_preview（密级：真实数据行） ----------


def save_preview(
    conn: sqlite3.Connection,
    *,
    query_id: str,
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    truncated: bool,
) -> None:
    conn.execute(
        "INSERT INTO result_preview(query_id, columns_json, rows_json, truncated)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(query_id) DO UPDATE SET columns_json = excluded.columns_json,"
        " rows_json = excluded.rows_json, truncated = excluded.truncated",
        (
            query_id,
            json.dumps(columns, ensure_ascii=False),
            json.dumps(rows, ensure_ascii=False, default=str),
            int(truncated),
        ),
    )


def get_preview(conn: sqlite3.Connection, query_id: str) -> ResultPreview | None:
    row = conn.execute("SELECT * FROM result_preview WHERE query_id = ?", (query_id,)).fetchone()
    return ResultPreview.from_row(row) if row else None


# ---------- query_event（事件溯源） ----------


def append_event(
    conn: sqlite3.Connection,
    *,
    query_id: str,
    event_type: str,
    payload: dict[str, Any],
    max_payload_kb: int = 16,
) -> int:
    """事件落库：seq 单调递增（同连接内 MAX+1，配合事务由 runner 保证串行）。

    返回分配的 seq。超限截断并附 truncated:true（C0）——截断产出必须是合法 JSON
    （保留 truncated/original_bytes/preview 三键），绝不做字节级硬切。
    result 批量行不入此表（D-06）。

    F12：并发写（stop 的 stopped 事件与运行中图的 emit 走各自连接）时，两个
    连接可读出相同 MAX(seq) → INSERT 撞 PK(query_id,seq)，或 WAL 快照冲突
    （locked/busy）。捕获后 rollback 重算 MAX+1 重试（≤3 次）——此前该竞态
    让 stop API 500(E5000) 且停止事件丢失。生产唯一调用方（runner._make_emit）
    为独立连接单语句事务，rollback 安全；调用方若在更大事务内混写，须自行保证串行。
    """
    text = json.dumps(payload, ensure_ascii=False, default=str)
    limit = max_payload_kb * 1024
    if len(text.encode("utf-8")) > limit:
        skeleton = {"truncated": True, "original_bytes": len(text.encode("utf-8")), "preview": ""}
        budget = limit - len(json.dumps(skeleton, ensure_ascii=False).encode("utf-8")) - 8
        preview = text[: max(0, budget)]
        while (
            preview
            and len(json.dumps({**skeleton, "preview": preview}, ensure_ascii=False).encode("utf-8")) > limit
        ):
            preview = preview[: len(preview) // 2]
        text = json.dumps({**skeleton, "preview": preview}, ensure_ascii=False)
    last_error: sqlite3.Error | None = None
    for _attempt in range(3):
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM query_event WHERE query_id = ?", (query_id,)
            ).fetchone()
            seq = int(row[0])
            conn.execute(
                "INSERT INTO query_event(query_id, seq, type, payload) VALUES (?, ?, ?, ?)",
                (query_id, seq, event_type, text),
            )
            return seq
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
            last_error = exc
            conn.rollback()  # 清失败语句/快照，下一轮重算 seq
    raise last_error  # type: ignore[misc]  # 3 次均撞冲突——真并发故障，抛出让上层 500


def query_events(conn: sqlite3.Connection, query_id: str, after_seq: int = 0) -> list[QueryEvent]:
    """事件重放：after_seq=0 全量自 seq=1；否则增量（Last-Event-ID 接缝）。"""
    rows = conn.execute(
        "SELECT * FROM query_event WHERE query_id = ? AND seq > ? ORDER BY seq", (query_id, after_seq)
    ).fetchall()
    return [QueryEvent.from_row(r) for r in rows]


def last_seq(conn: sqlite3.Connection, query_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM query_event WHERE query_id = ?", (query_id,)
    ).fetchone()
    return int(row[0])


# ---------- E5 会话消息快照（C1.1-E5 冻结包络，DAO 聚合防 N+1） ----------


def query_session_snapshot(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """E5 快照：query + result_preview + query_event 三次往返聚合（不依赖 checkpoint）。

    冻结包络（修复回路 P0-1，前后端唯一权威形状，回写 contracts.md C1.1-E5）：
    ``{session, items, last_seq_by_query}``
    - ``session``：会话行全字段（含 summary/summary_upto 压缩摘要，R-2 横幅数据源）；
    - ``items``：消息平铺（含 preview/clarify_choice/recovered；preview 即
      result_preview 表内容，前端 hydrate 时映射进结果表，P1-1）；
    - ``last_seq_by_query``：``{query_id: 已持久化最大 seq}`` 续播游标顶层字典
      （SSE Last-Event-ID 依据；不再随 item 内嵌）。
    ``recovered``：D03 启动清扫自动续跑标记（0006 迁移，R-3 绿横幅数据源）。
    """
    from app.store import sessions as store_sessions

    session = store_sessions.get_session(conn, session_id)  # 404 闸门（api 层冗余一道）
    queries = conn.execute(
        "SELECT * FROM query WHERE session_id = ? ORDER BY created_at, id", (session_id,)
    ).fetchall()
    if not queries:
        return {"session": session.model_dump(), "items": [], "last_seq_by_query": {}}
    ids = [r["id"] for r in queries]
    marks = ",".join("?" * len(ids))
    previews = {
        r["query_id"]: r
        for r in conn.execute(f"SELECT * FROM result_preview WHERE query_id IN ({marks})", ids).fetchall()
    }
    seq_rows = conn.execute(
        f"SELECT query_id, MAX(seq) AS last_seq FROM query_event WHERE query_id IN ({marks})"
        " GROUP BY query_id",
        ids,
    ).fetchall()
    last_seqs = {r["query_id"]: int(r["last_seq"]) for r in seq_rows}

    # 过程回放（刷新后历史可见性修复）：从 query_event 重建阶段卡/风险/修正时间线/
    # 校验/澄清选项，随快照随带——前端 hydrate 直接渲染，无需为历史挂 SSE。
    ev_rows = conn.execute(
        f"SELECT query_id, seq, type, payload FROM query_event WHERE query_id IN ({marks})"
        " ORDER BY query_id, seq",
        ids,
    ).fetchall()
    proc: dict[str, dict[str, Any]] = {}
    for er in ev_rows:
        p = proc.setdefault(
            er["query_id"],
            {
                "stages": [],
                "risks": [],
                "corrections": [],
                "verify": None,
                "clarify_options": None,
                "error": None,
                "reasoning_full": [],  # 各轮思考全文（DSH 轨迹回放）
                "sql_rounds": {},  # {round: sql} 逐轮 SQL（修正差异概览用）
                "retries": [],  # llm_retry 重试记录
                "inbox_notes": [],  # inbox_claimed 引导/注入
                "hit_tables": [],  # meta 事件的命中表（信封组装条目）
            },
        )
        payload = json.loads(er["payload"])
        t = er["type"]
        if t == "error":
            # 终局错误卡回放（刷新后 E2001/E2004 等失败原因+重试入口可见）
            p["error"] = {
                "code": payload.get("code"),
                "message": payload.get("message"),
                "retryable": bool(payload.get("retryable")),
            }
        elif t == "stage":
            st = {
                "stage": payload.get("stage"),
                "round": payload.get("round", 0),
                "status": payload.get("status"),
                "elapsed_ms": payload.get("elapsed_ms"),
            }
            stages = p["stages"]
            idx = next(
                (i for i, x in enumerate(stages) if x["stage"] == st["stage"] and x["round"] == st["round"]),
                -1,
            )
            if idx >= 0:
                stages[idx] = {**stages[idx], **st}  # 同 stage+round 覆盖（start→done）
            else:
                stages.append(st)
        elif t == "risk":
            p["risks"] = payload.get("risks") or []
        elif t == "correction":
            p["corrections"].append(
                {
                    "round": payload.get("round"),
                    "sql": payload.get("sql"),
                    "error": payload.get("error"),
                    "fingerprint": payload.get("fingerprint"),
                }
            )
        elif t == "verify":
            p["verify"] = {
                "checks": payload.get("checks") or [],
                "passed": bool(payload.get("passed")),
                "fallback_used": bool(payload.get("fallback_used")),
            }
        elif t == "clarify_required":
            p["clarify_options"] = payload.get("options")
        elif t == "reasoning_full":
            # 历史事件可能带 16KB 截断标记（truncated/preview）——回退 preview 保可见性
            text = payload.get("text") or (
                f"{payload.get('preview') or ''}\n…（早期事件被截断）" if payload.get("truncated") else ""
            )
            p["reasoning_full"].append({"round": payload.get("round", 0), "text": text})
        elif t == "sql":
            p["sql_rounds"][str(payload.get("round", 0))] = payload.get("sql") or ""
        elif t == "llm_retry":
            p["retries"].append(
                {
                    "attempt": payload.get("attempt"),
                    "delay_ms": payload.get("delay_ms"),
                    "failure_class": payload.get("failure_class"),
                    "route": payload.get("route"),
                }
            )
        elif t == "inbox_claimed":
            p["inbox_notes"].extend(payload.get("items") or [])
        elif t == "meta":
            hit = payload.get("hit_tables") or []
            if hit:
                p["hit_tables"] = hit

    items: list[dict[str, Any]] = []
    for r in queries:
        q = Query.from_row(r)
        preview = previews.get(q.id)
        items.append(
            {
                "process": proc.get(q.id),
                "query_id": q.id,
                "question": q.question,
                "status": q.status,
                "dialect": q.dialect,
                "contract": q.contract,
                "sql_final": q.sql_final,
                "rounds": q.rounds,
                "tokens": q.tokens,
                "scan_rows": q.scan_rows,
                "data_max_time": q.data_max_time,
                "duration_ms": q.duration_ms,
                "result_unverified": bool(q.result_unverified),
                "clarify_choice": q.clarify_choice,
                "recovered": bool(q.recovered),
                "preview": (
                    {
                        "columns": json.loads(preview["columns_json"]),
                        "rows": json.loads(preview["rows_json"]),
                        "truncated": bool(preview["truncated"]),
                    }
                    if preview
                    else None
                ),
                "created_at": q.created_at,
            }
        )
    return {"session": session.model_dump(), "items": items, "last_seq_by_query": last_seqs}


def query_ids_all(conn: sqlite3.Connection) -> set[str]:
    """exports 孤儿清扫用：现存 query id 全集。"""
    return {r[0] for r in conn.execute("SELECT id FROM query").fetchall()}


def active_query_in_session(conn: sqlite3.Connection, session_id: str) -> str | None:
    """会话内最近一条活动查询（running/waiting_*）的 id；无则 None（followup 排队锚点）。"""
    row = conn.execute(
        "SELECT id FROM query WHERE session_id = ?"
        " AND status IN ('running', 'waiting_clarify', 'waiting_confirm')"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return str(row["id"]) if row else None


def recent_rounds(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    exclude_query_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """信封「近期轮次」段数据源：同会话最近 K 轮（排除当前轮），按时间升序返回。

    DSH deriveMessages 等价物：近期轮次逐字注入信封，多轮修复场景模型可见
    上一轮的 question+sql_final，不再把增量约束误判为新需求。
    """
    if limit <= 0 or not session_id:
        return []
    rows = conn.execute(
        "SELECT question, sql_final, status FROM query"
        " WHERE session_id = ? AND id != ?"
        " ORDER BY created_at DESC, id DESC LIMIT ?",
        (session_id, exclude_query_id, limit),
    ).fetchall()
    return [
        {"question": r["question"], "sql_final": r["sql_final"], "status": r["status"]}
        for r in reversed(rows)
    ]
