"""双库连接工厂 + migrations 顺序执行器（design D-39）。

- `PRAGMA journal_mode=WAL`、`foreign_keys=ON` 每连接生效；
- 执行器幂等（schema_migrations 登记表），按版本号顺序执行，失败拒启并报迁移号；
- 0001~0004 为 SSOT §3.1-§3.4 原文，0005 为本次新增（D-18 graph_layout）。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app import config

logger = logging.getLogger("aitosql.store.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# T0.1 验收基线：0001~0005 建全的表/视图清单（schema_migrations 为执行器自管表，不计入）
EXPECTED_OBJECTS = (
    # 0001（16 表；plan §5.2 注：SSOT 原文为 16 表，tasks「13 表」系笔误，构建期按原文，回写项 TODO）
    "datasource",
    "table_meta",
    "column_meta",
    "relation",
    "field_standard",
    "dimension_standard",
    "dimension_value",
    "metric_standard",
    "ai_suggestion",
    "session",
    "query",
    "result_preview",
    "clarify_pref",
    "sample",
    "provider",
    "setting",
    # 0002 FTS5 虚拟表
    "schema_fts",
    # 0003 事件溯源
    "query_event",
    # 0004 记忆层
    "memory_log",
    "memory",
    "memory_watermark",
    # 0005 本次新增 D-18
    "graph_layout",
    # 0007 AI 补全任务模型
    "ai_run",
    "execution_attempt",
    "schema_revision",
    # 0010 生成反馈闭环
    "retrieval_feedback",
    # 0012 DSH 对齐（P5 口径文档 / P6 inbox 双队列）
    "datasource_instruction",
    "query_inbox",
)


class MigrationError(Exception):
    """迁移失败：拒启并指明迁移号（D-39）。"""

    def __init__(self, migration: str, cause: Exception) -> None:
        super().__init__(f"迁移 {migration} 执行失败: {cause}")
        self.migration = migration
        self.cause = cause


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """打开 app.db 连接（WAL + 外键 + Row 工厂）。调用方负责关闭。"""
    path = db_path or config.app_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    # temp_store=MEMORY：触发器多行级联的语句日志（delete-on-close 临时文件）
    # 在部分 Windows 环境（杀软过滤驱动）会触发 SQLITE_CANTOPEN；本地单机
    # 数据规模下内存暂存零风险（实测 440 行级联删除由必现 500 变为通过）。
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


@contextmanager
def get_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """连接上下文：自动提交/回滚/关闭。同步 IO 调用侧经 anyio.to_thread 隔离（D-02）。"""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migration_files() -> list[Path]:
    """按版本号升序的迁移文件清单。"""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """sqlite3.Row → dict（Row 非 Mapping，标准转换手法；统一收口避免逐处推导）。"""
    return {k: row[k] for k in row.keys()}  # noqa: SIM118 - Row 非 dict，官方手法


def _statements(script: str) -> Iterator[str]:
    """Split using SQLite's parser, including trigger bodies and quoted semicolons."""
    pending = ""
    for char in script:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if re.sub(r"--[^\n]*|/\*.*?\*/", "", pending, flags=re.S).strip():
        raise ValueError("Migration ends with an incomplete SQL statement")


def run_migrations(db_path: Path | None = None) -> list[str]:
    """Each migration and its ledger entry commit atomically; WAL is set by connect()."""
    applied: list[str] = []
    with get_conn(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "version TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"
        )
        conn.commit()
        for f in migration_files():
            version = f.name
            try:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone():
                    conn.rollback()
                    continue
                for statement in _statements(f.read_text(encoding="utf-8")):
                    clean = re.sub(r"--[^\n]*|/\*.*?\*/", "", statement, flags=re.S).strip()
                    if re.fullmatch(r"PRAGMA\s+journal_mode\s*=\s*WAL\s*;", clean, re.I):
                        continue  # Connection setup owns this non-transactional setting.
                    if re.match(r"(?:BEGIN|COMMIT|END|ROLLBACK|VACUUM|ATTACH|DETACH)\b", clean, re.I):
                        raise ValueError("Migration transaction control belongs to the executor")
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
                conn.commit()
                applied.append(version)
            except Exception as exc:
                conn.rollback()
                raise MigrationError(version, exc) from exc
    return applied


def count_expected_objects(db_path: Path | None = None) -> tuple[int, list[str]]:
    """核对 T0.1 验收：应建表/视图是否齐全。返回 (缺失数, 缺失名单)。"""
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()
    existing = {r[0] for r in rows}
    missing = [n for n in EXPECTED_OBJECTS if n not in existing]
    return len(missing), missing
