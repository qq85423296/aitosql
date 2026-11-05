"""离线三通道（design D-32 / FR-D13 / T2.6）：DDL 入库 / CSV 模板 / AI 草稿。

分层裁决（构建期定型，回写 design D-32）：DDL 文本解析（sqlglot）收口在
`sqlcheck/ddl.py`（D3：sqlglot 唯一 import 点）；本模块只消费解析产物（显式参数 D2），
不 import sqlglot 与兄弟模块。api 层编排：parse_ddl → 预览 → commit_parsed_tables。
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from typing import Any

from app.errors import E_BAD_REQUEST, BizError
from app.ingest.sanitize import sanitize_text
from app.store import datasources as store_datasources


def commit_parsed_tables(
    conn: sqlite3.Connection, ds_id: int, tables: list[dict[str, Any]]
) -> dict[str, int]:
    """DDL 入库：解析产物（{name, comment, columns:[{name,type,comment}]}）落库。

    复用在线摄取同一合并策略（只刷物理属性，人工字段不覆盖）。
    部分失败已在解析期标红（errors 由 sqlcheck/ddl 返回），本函数仅处理成功部分。
    """
    saved_tables = 0
    saved_columns = 0
    for t in tables:
        table_id, _created = store_datasources.upsert_table_physical(
            conn,
            ds_id,
            name=sanitize_text(t["name"]),
            comment_db=sanitize_text(t.get("comment")),
            row_estimate=None,
            field_count=len(t.get("columns", [])),
            partition_type=None,
            schema_name=sanitize_text(t.get("schema")) or None,
        )
        saved_tables += 1
        keep: set[str] = set()
        for c in t.get("columns", []):
            name = sanitize_text(c["name"])
            keep.add(name)
            store_datasources.upsert_column_physical(
                conn,
                table_id,
                name=name,
                type_=sanitize_text(c.get("type", "")),
                comment_db=sanitize_text(c.get("comment")),
                is_pk=int(bool(c.get("is_pk"))),
                is_partition=int(bool(c.get("is_partition"))),
                is_bucket=int(bool(c.get("is_bucket"))),
            )
            saved_columns += 1
        vanished = store_datasources.unbind_vanished_columns(conn, table_id, keep)
        if vanished:
            from app.store import samples as store_samples

            store_samples.mark_stale_by_vanished(conn, ds_id, vanished)
    store_datasources.update_datasource(conn, ds_id, touch_ingest=True)
    return {"tables": saved_tables, "columns": saved_columns}


# 元数据字典模板（11 列中文表头，必填 8 列）——行 = 某表一个字段
DICT_HEADERS = (
    "表模式名",
    "表英文名",
    "表中文名",
    "表业务描述",
    "字段序号",
    "字段英文名",
    "字段中文名",
    "字段类型",
    "字段业务描述",
    "是否主键",
    "是否分区",
)
DICT_REQUIRED = (
    "表模式名",
    "表英文名",
    "表中文名",
    "字段序号",
    "字段英文名",
    "字段中文名",
    "字段类型",
    "是否主键",
)
# 表级四列支持 Excel 合并单元格导出形态：留空沿用上一行
_DICT_TABLE_COLS = ("表模式名", "表英文名", "表中文名", "表业务描述")
# 英文名标识符校验：字母/数字/下划线、不以数字开头（拦截「自增生成」之类占位文本）
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _yn(value: str) -> int | None:
    """是否列解析：1/0/是/否/true/false/y/n（空 → None 由调用方按必填性处置）。"""
    v = value.strip().lower()
    if v in ("1", "是", "true", "y", "yes"):
        return 1
    if v in ("0", "否", "false", "n", "no"):
        return 0
    return None


def csv_dict_template_to_tables(csv_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """元数据字典模板 → (多表解析产物, 行级错误)。

    按 (表模式名, 表英文名) 分组、字段序号升序；必填列缺失/非法 → 行级错误不阻断他行；
    表头缺必填列 → BizError（整文件致命）。表模式名落库 table_meta.schema_name（0011：
    问数 SQL 限定前缀）。注释映射：中文名｜业务描述（无描述则仅中文名）。
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows or not any(cell.strip() for cell in rows[0]):
        raise BizError(E_BAD_REQUEST, "CSV 模板为空或缺少表头行", 422)
    header = [cell.strip().lstrip("﻿") for cell in rows[0]]
    index = {name: i for i, name in enumerate(header)}
    missing = [name for name in DICT_REQUIRED if name not in index]
    if missing:
        raise BizError(E_BAD_REQUEST, f"模板表头缺少必填列：{'、'.join(missing)}", 422)

    def cell(row: list[str], name: str) -> str:
        i = index.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    carry = {name: "" for name in _DICT_TABLE_COLS}
    for line_no, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue  # 全空行跳过
        for name in _DICT_TABLE_COLS:
            value = cell(row, name)
            if value:
                carry[name] = value
        table_name = carry["表英文名"]
        ctx = f"第{line_no}行（{table_name or '未知表'}）"
        row_errors = [
            f"{ctx} 缺少必填项 {name}"
            for name in ("表模式名", "表英文名", "表中文名")
            if not carry[name]
        ]
        if table_name and not _IDENT_RE.match(table_name):
            row_errors.append(f"{ctx} 表英文名须为英文标识符（字母/数字/下划线）：{table_name}")
        seq_raw = cell(row, "字段序号")
        try:
            seq = int(seq_raw)
        except ValueError:
            row_errors.append(f"{ctx} 字段序号须为整数：{seq_raw or '（空）'}")
            seq = 0
        col_name = cell(row, "字段英文名")
        col_comment = cell(row, "字段中文名")
        col_type = cell(row, "字段类型")
        for label, value in (("字段英文名", col_name), ("字段中文名", col_comment), ("字段类型", col_type)):
            if not value:
                row_errors.append(f"{ctx} 缺少必填项 {label}")
        if col_name and not _IDENT_RE.match(col_name):
            row_errors.append(f"{ctx} 字段英文名须为英文标识符（字母/数字/下划线）：{col_name}")
        pk = _yn(cell(row, "是否主键"))
        if pk is None:
            row_errors.append(f"{ctx} 是否主键须填 1/0（或是/否）：{cell(row, '是否主键') or '（空）'}")
        part_raw = cell(row, "是否分区")
        partition = 0 if not part_raw else _yn(part_raw)
        if partition is None:
            row_errors.append(f"{ctx} 是否分区须填 1/0（或是/否）：{part_raw}")
            partition = 0
        if row_errors:
            errors.extend({"row": line_no, "table": table_name, "message": m} for m in row_errors)
            continue
        key = (carry["表模式名"], table_name)
        group = groups.setdefault(
            key,
            {
                "schema": carry["表模式名"],
                "name": table_name,
                "comment": _join_comment(carry["表中文名"], carry["表业务描述"]),
                "columns": [],
            },
        )
        group["columns"].append(
            {
                "seq": seq,
                "name": col_name,
                "type": col_type,
                "comment": _join_comment(col_comment, cell(row, "字段业务描述")),
                "is_pk": pk,
                "is_partition": partition,
            }
        )
    tables: list[dict[str, Any]] = []
    for group in groups.values():
        cols = sorted(group["columns"], key=lambda c: int(c["seq"]))
        for c in cols:
            del c["seq"]
        group["columns"] = cols
        tables.append(group)
    return tables, errors


def _join_comment(name: str, desc: str) -> str:
    """中文名｜业务描述 合并（无描述或同名则仅中文名）。"""
    if desc and desc != name:
        return f"{name}｜{desc}"
    return name


def build_ai_draft_prompt(hint: str) -> str:
    """AI 草稿通道：生成 DDL 草稿的提示词（LLM 调用由 api 层经 route.ai 发起）。"""
    return (
        "请根据以下描述生成建表 DDL（仅输出 DDL 文本，逐表一条 CREATE TABLE，"
        "字段附 COMMENT）：\n" + hint.strip()
    )
