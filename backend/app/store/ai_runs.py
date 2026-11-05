"""AI 补全任务 DAO（0007；方案 v1.1 §3 生命周期语义）。

- 同数据源单 running 守卫：create 前查 active_run（queued|running），冲突由路由层转 E3101；
- 进度/产出/失败清单增量更新，finish 一次落定终态；
- 判重幂等与建议侧一致：重跑只补「仍缺失」的项（建议侧 payload 判重兜底）。
"""

from __future__ import annotations

import json
import sqlite3

from app.errors import E_NOT_FOUND, BizError
from app.identifiers import require_id
from app.store.models import AiRun


def create_run(
    conn: sqlite3.Connection,
    *,
    datasource_id: int,
    scope_type: str,
    scope_table_id: int | None,
    kinds: list[str],
    options: dict,
) -> int:
    cur = conn.execute(
        "INSERT INTO ai_run(datasource_id, scope_type, scope_table_id, kinds_json, options_json)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            datasource_id,
            scope_type,
            scope_table_id,
            json.dumps(kinds, ensure_ascii=False),
            json.dumps(options, ensure_ascii=False),
        ),
    )
    return require_id(cur.lastrowid)


def get_run(conn: sqlite3.Connection, run_id: int) -> AiRun:
    row = conn.execute("SELECT * FROM ai_run WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"补全任务不存在: {run_id}", 404)
    return AiRun.from_row(row)


def active_run(conn: sqlite3.Connection, datasource_id: int) -> AiRun | None:
    row = conn.execute(
        "SELECT * FROM ai_run WHERE datasource_id = ? AND status IN ('queued','running')"
        " ORDER BY id DESC LIMIT 1",
        (datasource_id,),
    ).fetchone()
    return AiRun.from_row(row) if row else None


def mark_running(conn: sqlite3.Connection, run_id: int, total: int) -> None:
    conn.execute(
        "UPDATE ai_run SET status = 'running', progress_total = ? WHERE id = ?",
        (total, run_id),
    )


def bump_progress(conn: sqlite3.Connection, run_id: int, *, made: dict[str, int]) -> None:
    """单项完成：done+1 并合并 made 计数（增量读改写，量小无争用）。"""
    row = conn.execute("SELECT made_json FROM ai_run WHERE id = ?", (run_id,)).fetchone()
    cur: dict[str, int] = json.loads(row["made_json"]) if row and row["made_json"] else {}
    for k, v in made.items():
        cur[k] = cur.get(k, 0) + v
    conn.execute(
        "UPDATE ai_run SET progress_done = progress_done + 1, made_json = ? WHERE id = ?",
        (json.dumps(cur, ensure_ascii=False), run_id),
    )


def add_failed(conn: sqlite3.Connection, run_id: int, item: dict) -> None:
    row = conn.execute("SELECT failed_json FROM ai_run WHERE id = ?", (run_id,)).fetchone()
    cur: list = json.loads(row["failed_json"]) if row and row["failed_json"] else []
    cur.append(item)
    conn.execute(
        "UPDATE ai_run SET progress_done = progress_done + 1, failed_json = ? WHERE id = ?",
        (json.dumps(cur, ensure_ascii=False), run_id),
    )


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, error: str | None = None) -> None:
    conn.execute(
        "UPDATE ai_run SET status = ?, error = ?, finished_at = datetime('now') WHERE id = ?",
        (status, error, run_id),
    )


def latest_runs(conn: sqlite3.Connection, datasource_id: int, limit: int = 10) -> list[AiRun]:
    rows = conn.execute(
        "SELECT * FROM ai_run WHERE datasource_id = ? ORDER BY id DESC LIMIT ?",
        (datasource_id, limit),
    ).fetchall()
    return [AiRun.from_row(r) for r in rows]
