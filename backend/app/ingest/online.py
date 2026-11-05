"""在线摄取（design D-32 / BR-1 / FR-D3/D4）：三方言元数据 + SSE 进度 + 续摄 E1002。

执行序：连接探测 → 表清单 → 逐表列/键 → 关系(FK) → FTS 重建（消毒先于 bigram）→ vec 重建。
- 引擎调用经调用方注入的 query_fn（同步，api 层包 to_thread，D-02/D2）；
- 合并策略走 store/datasources.upsert_*_physical（人工字段永不覆盖）；
- 中断：进度持久化在模块级 _PROGRESS（数据源粒度），重试从断点表续摄（E1002）。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any

from app.errors import E_INGEST_INTERRUPT, BizError
from app.ingest import capability, keys
from app.ingest.sanitize import sanitize_row
from app.store import datasources as store_datasources

logger = logging.getLogger("aitosql.ingest.online")

QueryFn = Callable[[str, Any], list[tuple]]
EventCb = Callable[[int, str], None]

_PROGRESS: dict[int, dict[str, Any]] = {}  # ds_id -> {done_tables: [...], total: int}

_TABLES_SQL = {
    "mysql": (
        "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS FROM information_schema.TABLES"
        " WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME"
    ),
    "doris": (
        "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS FROM information_schema.TABLES"
        " WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME"
    ),
    "clickhouse": (
        "SELECT name, comment, total_rows FROM system.tables WHERE database = %(db)s ORDER BY name"
    ),
}

_COLUMNS_SQL = {
    "mysql": (
        "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION"
    ),
    "doris": (
        "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION"
    ),
    "clickhouse": (
        "SELECT name, type, comment FROM system.columns"
        " WHERE database = %(db)s AND table = %(table)s ORDER BY position"
    ),
}

_FK_SQL = {
    "mysql": (
        "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME"
        " FROM information_schema.KEY_COLUMN_USAGE"
        " WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL"
    ),
    "doris": None,  # Doris 无 FK 约束；关系走推断（inferred=pending）
    "clickhouse": None,
}


def _param(dialect: str, db_name: str, table: str | None = None) -> Any:
    if dialect == "clickhouse":
        p: dict[str, Any] = {"db": db_name}
        if table is not None:
            p["table"] = table
        return p
    return (db_name,) if table is None else (db_name, table)


def ingest_datasource(
    conn: sqlite3.Connection,
    ds_id: int,
    *,
    dialect: str,
    db_name: str,
    query_fn: QueryFn,
    event_cb: EventCb,
) -> dict[str, Any]:
    """在线摄取主流程（同步；api 层以 to_thread 承载 + SSE 事件桥接 event_cb）。

    中断抛 BizError(E1002)；重试自断点续摄（_PROGRESS 断点表清单）。
    """
    progress = _PROGRESS.setdefault(ds_id, {"done_tables": [], "total": 0})
    try:
        return _ingest_inner(
            conn,
            ds_id,
            dialect=dialect,
            db_name=db_name,
            query_fn=query_fn,
            event_cb=event_cb,
            progress=progress,
        )
    except BizError:
        raise
    except Exception as exc:
        # S-1：底层异常原文（连接串/主机/驱动栈）不外泄给用户消息；详情记日志
        logger.warning("摄取中断 ds=%s: %s: %s", ds_id, type(exc).__name__, exc)
        raise BizError(
            E_INGEST_INTERRUPT, "摄取中断（可在本进程内重试续摄；详细信息见服务端日志）", 502
        ) from exc
    finally:
        if progress.get("finished"):
            _PROGRESS.pop(ds_id, None)


def _ingest_inner(
    conn: sqlite3.Connection,
    ds_id: int,
    *,
    dialect: str,
    db_name: str,
    query_fn: QueryFn,
    event_cb: EventCb,
    progress: dict[str, Any],
) -> dict[str, Any]:
    event_cb(1, "连接成功，开始摄取表清单")
    version = capability.probe_version(dialect, lambda sql: query_fn(sql, None))
    caps = capability.build_capabilities(dialect, version)
    store_datasources.update_datasource(conn, ds_id, version=version, capabilities=caps)

    table_rows = query_fn(_TABLES_SQL[dialect], _param(dialect, db_name))
    total = len(table_rows)
    progress["total"] = total
    event_cb(5, f"发现 {total} 张表")

    key_map = keys.normalize_keys(
        dialect,
        query_fn(keys.key_probe_sql(dialect), _param(dialect, db_name)),
        query_fn(partition_sql, _param(dialect, db_name))
        if (partition_sql := keys.partition_probe_sql(dialect)) is not None
        else None,
    )
    partition_types: dict[str, str | None] = {}
    done: set[str] = set(progress["done_tables"])
    fts_rows: list[tuple[str, str, str, str]] = []

    for idx, row in enumerate(table_rows, start=1):
        name = str(row[0])
        if name in done:
            continue  # 续摄跳过断点前已完成表
        comment = str(row[1] or "") or None
        row_estimate = int(row[2]) if row[2] is not None else None
        cols = query_fn(_COLUMNS_SQL[dialect], _param(dialect, db_name, name))
        tkeys = key_map.get(name, {"pk": [], "partition": [], "bucket": []})
        partition_type = _infer_partition_type(cols, tkeys)
        partition_types[name] = partition_type
        table_id, _ = store_datasources.upsert_table_physical(
            conn,
            ds_id,
            name=name,
            comment_db=comment,
            row_estimate=row_estimate,
            field_count=len(cols),
            partition_type=partition_type,
            schema_name=db_name or None,  # 在线摄取：库名即模式名（0011）
        )
        keep: set[str] = set()
        for c in cols:
            col_name, col_type = str(c[0]), str(c[1])
            col_comment = str(c[2] or "") or None
            keep.add(col_name)
            store_datasources.upsert_column_physical(
                conn,
                table_id,
                name=col_name,
                type_=col_type,
                comment_db=col_comment,
                is_pk=int(col_name in tkeys["pk"]),
                is_partition=int(
                    col_name in tkeys["partition"]
                    or bool(partition_type and col_name == _partition_col_guess(cols))
                ),
                is_bucket=int(col_name in tkeys["bucket"]),
            )
            s_table, s_col, s_comment = sanitize_row(name, col_name, col_comment)
            fts_rows.append((str(ds_id), s_table, s_col, s_comment))
        vanished = store_datasources.unbind_vanished_columns(conn, table_id, keep)
        if vanished:
            from app.store import samples as store_samples

            marked = store_samples.mark_stale_by_vanished(conn, ds_id, vanished)
            if marked:
                event_cb(0, f"表 {name} 字段消失 {vanished}，{marked} 条样例置 stale")
        done.add(name)
        progress["done_tables"] = sorted(done)
        pct = 5 + int(idx / max(total, 1) * 80)
        event_cb(pct, f"[{idx}/{total}] {name}（{len(cols)} 字段）")

    _ingest_relations(conn, ds_id, dialect, db_name, query_fn, event_cb)
    event_cb(90, keys.count_summary(key_map, partition_types))
    store_datasources.update_datasource(conn, ds_id, touch_ingest=True)
    progress["finished"] = True
    event_cb(100, f"摄取完成：{total} 表（FTS 语料 {len(fts_rows)} 行待重建）")
    return {
        "tables": total,
        "version": version,
        "capabilities": caps,
        "fts_rows": fts_rows,
        "key_summary": keys.count_summary(key_map, partition_types),
    }


def _ingest_relations(
    conn: sqlite3.Connection, ds_id: int, dialect: str, db_name: str, query_fn: QueryFn, event_cb: EventCb
) -> None:
    sql = _FK_SQL.get(dialect)
    if not sql:
        return
    rows = query_fn(sql, _param(dialect, db_name))
    for t, c, rt, rc in rows:
        exists = conn.execute(
            "SELECT 1 FROM relation WHERE datasource_id = ? AND from_table = ?"
            " AND from_col = ? AND to_table = ? AND to_col = ? AND source = 'fk'",
            (ds_id, t, c, rt, rc),
        ).fetchone()
        if not exists:
            store_datasources.save_relation(
                conn, ds_id, from_table=str(t), from_col=str(c), to_table=str(rt), to_col=str(rc), source="fk"
            )
    if rows:
        event_cb(88, f"FK 关系 {len(rows)} 条")


def _infer_partition_type(cols: list[tuple], tkeys: dict[str, Any]) -> str | None:
    """分区粒度推断：有分区事实位时按月/日猜测（名称启发式，人工可改 partition_type）。"""
    if not (tkeys.get("partition") or tkeys.get("_has_partition")):
        return None
    names = " ".join(str(c[0]).lower() for c in cols)
    if any(k in names for k in ("day", "dt", "date_day")):
        return "day"
    return "month"


def _partition_col_guess(cols: list[tuple]) -> str | None:
    for c in cols:
        name = str(c[0]).lower()
        if name in ("dt", "day", "date", "month", "created_date"):
            return str(c[0])
    return None
