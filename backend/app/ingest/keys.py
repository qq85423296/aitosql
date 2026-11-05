"""主键/分区键/分桶键探测（design D-32，三方言语法）。

摄取日志计数口径（FR-D3）：「主键 N / 分区键 M（月 x·日 y）/ 分桶键 K」。
全部 SQL 在 engine 侧执行；本模块只产出探测语句与结果归一化。
"""

from __future__ import annotations

from typing import Any

# ---------- 探测语句（按方言） ----------

_MYSQL_KEYS_SQL = """
SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = %s AND CONSTRAINT_NAME = 'PRIMARY'
ORDER BY TABLE_NAME, ORDINAL_POSITION
"""

_MYSQL_PARTITION_SQL = """
SELECT TABLE_NAME, PARTITION_DESCRIPTION, PARTITION_METHOD
FROM information_schema.PARTITIONS
WHERE TABLE_SCHEMA = %s AND PARTITION_NAME IS NOT NULL
"""

_CH_KEYS_SQL = """
SELECT table, name, is_in_primary_key, is_in_partition_key, is_in_sampling_key
FROM system.columns
WHERE database = %(db)s
"""

_DORIS_KEYS_SQL = _MYSQL_KEYS_SQL  # Doris 兼容 MySQL 协议（information_schema 同形态）


def key_probe_sql(dialect: str) -> str:
    """主键探测语句（参数 = db_name，占位形态随驱动：PyMySQL %s / CH %(db)s）。"""
    return {"mysql": _MYSQL_KEYS_SQL, "doris": _DORIS_KEYS_SQL, "clickhouse": _CH_KEYS_SQL}[dialect]


def partition_probe_sql(dialect: str) -> str | None:
    """分区探测语句；CH 走 _CH_KEYS_SQL 一并返回，无需单独探测。"""
    return {"mysql": _MYSQL_PARTITION_SQL, "doris": _MYSQL_PARTITION_SQL, "clickhouse": None}[dialect]


# ---------- 结果归一化 ----------


def normalize_keys(
    dialect: str, key_rows: list[tuple], partition_rows: list[tuple] | None
) -> dict[str, dict[str, Any]]:
    """归一化为 {table: {pk: [...], partition: [...], bucket: [...]}}。

    Doris 分桶键走 SHOW CREATE TABLE 的调用侧补充（本函数合并 bucket_rows 入参语义，
    由 online.py 在拿到建表语句后回填 `bucket` 键）。
    """
    result: dict[str, dict[str, Any]] = {}
    if dialect == "clickhouse":
        for table, name, is_pk, is_part, is_sample in key_rows:
            slot = result.setdefault(str(table), {"pk": [], "partition": [], "bucket": []})
            if is_pk:
                slot["pk"].append(str(name))
            if is_part:
                slot["partition"].append(str(name))
            if is_sample:
                slot["bucket"].append(str(name))
        return result
    for _table, col, _constraint in key_rows:
        slot = result.setdefault(str(_table), {"pk": [], "partition": [], "bucket": []})
        slot["pk"].append(str(col))
    for row in partition_rows or []:
        table = str(row[0])
        slot = result.setdefault(table, {"pk": [], "partition": [], "bucket": []})
        # 分区列名从 PARTITION_METHOD 表达式无法可靠拆出，列名由调用侧（online）
        # 结合建表语句/列清单比对回填；此处登记「该表存在分区」事实位
        slot.setdefault("_has_partition", True)
    return result


def count_summary(key_map: dict[str, dict[str, Any]], partition_types: dict[str, str | None]) -> str:
    """摄取日志计数（FR-D3 口径）：主键 N / 分区键 M（月 x·日 y）/ 分桶键 K。"""
    pk = sum(len(v["pk"]) for v in key_map.values())
    part = sum(len(v["partition"]) for v in key_map.values()) or sum(
        1 for v in key_map.values() if v.get("_has_partition")
    )
    month = sum(1 for t in partition_types.values() if t == "month")
    day = sum(1 for t in partition_types.values() if t == "day")
    bucket = sum(len(v["bucket"]) for v in key_map.values())
    return f"主键 {pk} / 分区键 {part}（月{month}·日{day}）/ 分桶键 {bucket}"
