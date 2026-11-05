"""执行元信息采集 + CSV 全量落盘 + 瞬态目录治理（D-16 / SSOT §10.5，BR-7）。

- exec_meta 五字段：rows / scan_rows / duration_ms / data_max_time / connection_id；
- data_max_time = 命中表时间列/分区键 MAX（C0）；
- 纯 SELECT 成功时同步全量落盘 `data/exports/{query_id}.csv`
  （UTF-8 带 BOM + RFC4180 引号 + 首行表头，C0）；写操作不产生导出；
- 治理：TTL 24h 定时清理 + 2048MB 超量 LRU + 启动孤儿清扫；exports/ 不入备份。
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app import config
from app.store import db
from app.store import queries as store_queries

logger = logging.getLogger("aitosql.exec.meta")

PREVIEW_LIMIT_BYTES = 50 * 1024  # result_preview.rows_json 截断 50KB（C6.9）


def pack_preview(columns: list[str], rows: list[tuple]) -> tuple[list[dict], list[list], bool]:
    """预览打包：columns 元信息 + 截断 50KB 的 rows_json（truncated 标记）。

    F-1：增量字节累计 O(n)（逐行 dumps 一次并累加 `[`/`]`/逗号开销），
    不再对全量候选数组反复序列化（旧实现 O(n²)，50KB 上限下尾行区间卡顿）。
    """
    col_meta = [{"name": c, "type": ""} for c in columns]
    packed: list[list[Any]] = []
    truncated = False
    used = 2  # "[]"
    for row in rows:
        row_list = list(row)
        row_bytes = len(json.dumps(row_list, ensure_ascii=False, default=str).encode())
        extra = row_bytes + (1 if packed else 0)  # 元素间逗号
        if used + extra > PREVIEW_LIMIT_BYTES:
            truncated = True
            break
        packed.append(row_list)
        used += extra
    return col_meta, packed, truncated


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")  # S-2：标识符白名单


def _quote_ident(name: str) -> str:
    """标识符反引号引用 + 白名单校验（引擎元数据可视作半可信输入）。"""
    if not _IDENT_RE.match(name):
        raise ValueError(f"非法标识符: {name!r}")
    return f"`{name}`"


def collect_data_max_time(
    query_fn: Callable[[str, Any], list[tuple]],
    dialect: str,
    meta_view: dict[str, dict[str, Any]],
    hit_tables: list[str],
) -> str | None:
    """data_max_time = 命中表分区键 MAX（任取首个有分区键的命中表；失败返回 None）。

    S-2：表/列名反引号引用 + 白名单校验（防引擎侧元数据注入拼接）。
    """
    for table in hit_tables:
        info = meta_view.get(table) or {}
        part_cols = info.get("partition_cols") or []
        if not part_cols:
            continue
        try:
            col = _quote_ident(part_cols[0])
            tbl = _quote_ident(table)
        except ValueError:
            logger.info("data_max_time 跳过非法标识符: %s", table)
            continue
        try:
            rows = query_fn(f"SELECT MAX({col}) FROM {tbl}", None)
            if rows and rows[0] and rows[0][0] is not None:
                return str(rows[0][0])[:10]  # YYYY-MM-DD
        except Exception:
            logger.info("data_max_time 采集失败: %s.%s", table, part_cols[0])
    return None


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> Any:
    """CSV 公式注入转义（S-3）：以 = + - @ / 制表回车开头的字符串前补单引号。"""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def dump_csv(query_id: str, columns: list[str], rows: list[tuple], export_dir: Path | None = None) -> Path:
    """全量落盘 CSV：UTF-8 BOM + RFC4180 引号 + 首行表头（写操作不调用本函数）。

    S-3：单元格公式注入转义；S-4：POSIX 下落盘文件 0600（结果数据密级）。
    """
    directory = export_dir or config.exports_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{query_id}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        writer.writerow([_csv_safe(c) for c in columns])
        for row in rows:
            writer.writerow(["" if v is None else _csv_safe(v) for v in row])
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


def sweep_exports(
    export_dir: Path | None = None, *, ttl_h: int = 24, max_total_mb: int = 2048
) -> dict[str, int]:
    """TTL 清理 + 超量 LRU（按 mtime 最旧先删）。"""
    directory = export_dir or config.exports_dir()
    if not directory.is_dir():
        return {"expired": 0, "lru": 0}
    now = time.time()
    files = sorted(directory.glob("*.csv"), key=lambda p: p.stat().st_mtime)
    expired = 0
    kept: list[Path] = []
    for f in files:
        if now - f.stat().st_mtime > ttl_h * 3600:
            f.unlink(missing_ok=True)
            expired += 1
        else:
            kept.append(f)
    total = sum(f.stat().st_size for f in kept)
    lru = 0
    for f in kept:
        if total <= max_total_mb * 1024 * 1024:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)
        lru += 1
    return {"expired": expired, "lru": lru}


def sweep_orphans(export_dir: Path | None = None) -> int:
    """启动孤儿清扫：无对应 query 记录的 csv（D-16）。"""
    directory = export_dir or config.exports_dir()
    if not directory.is_dir():
        return 0
    with db.get_conn() as conn:
        existing = store_queries.query_ids_all(conn)
    removed = 0
    for f in directory.glob("*.csv"):
        if f.stem not in existing:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


async def sweep_orphans_async() -> None:
    """main.py 启动钩子（同步 IO 隔离 D-02）。"""
    import anyio

    removed = await anyio.to_thread.run_sync(sweep_orphans)
    if removed:
        logger.info("exports 孤儿清扫: %d 个", removed)
    await anyio.to_thread.run_sync(sweep_exports)
