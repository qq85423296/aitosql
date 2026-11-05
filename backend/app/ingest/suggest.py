"""AI 建议生产（修复回路 R-1 / FR-D12）：摄取完成后填充建议队列。

两路生产（P5：人裁决前不进提示词——本模块只写 ai_suggestion，status=pending）：
1. 关系推断（规则，零出境）：列名 `{表}_id` / 与他表主键同名同型 → relation 建议；
2. 注释补全（可选，route.ai）：缺注释表/字段批量送小模型 → comment 建议。
   LLM 调用由 api 层注入 chat_fn（D2/D6：ingest 不 import llm）。

判重：同数据源同 kind 同 payload_json 的 pending 建议已存在则跳过（幂等重摄取）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from app.store import datasources as store_datasources
from app.store import suggestions as store_suggestions

logger = logging.getLogger("aitosql.ingest.suggest")


def _exists_pending_or_ignored(conn: sqlite3.Connection, ds_id: int, kind: str, payload: dict) -> bool:
    """判重（忽略不回潮）：pending 或 ignored 的同 payload 建议存在即跳过——

    用户忽略过的建议重跑补全也不再产出（忽略 = 人裁决"别再提"）。"""
    row = conn.execute(
        "SELECT 1 FROM ai_suggestion WHERE datasource_id = ? AND kind = ?"
        " AND status IN ('pending', 'ignored') AND payload_json = ? LIMIT 1",
        (ds_id, kind, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    return row is not None


def _relation_confirmed_or_pending(conn: sqlite3.Connection, ds_id: int, from_ref: str, to_ref: str) -> bool:
    ft, fc = from_ref.split(".", 1)
    tt, tc = to_ref.split(".", 1)
    row = conn.execute(
        "SELECT 1 FROM relation WHERE datasource_id = ? AND from_table = ?"
        " AND from_col = ? AND to_table = ? AND to_col = ? LIMIT 1",
        (ds_id, ft, fc, tt, tc),
    ).fetchone()
    if row is not None:
        return True
    # 已忽略的同向关系建议不再回潮
    payload = {"from": from_ref, "to": to_ref, "via": "name_match"}
    return _exists_pending_or_ignored(conn, ds_id, "relation", payload)


def infer_relation_suggestions(conn: sqlite3.Connection, ds_id: int) -> int:
    """规则关系推断（FR-D12 relation 类）：

    - `{table}_id` 命名惯例：`orders.user_id` → `users.id`（目标表名 = 前缀复数/原样命中）；
    - 与他表主键同名同型：`a.id = b.id` 类（仅当来源列非主键，避免自指噪音）。
    置信度 0.6（名称推断）；已确认/待审/已忽略同向关系与重复建议跳过。
    枢纽列过滤：主键名出现在 >max(3, 25%) 张表（aoto_id/id/create_by 类审计列）
    不参与同名同型推断——否则每个非主键同名列都会长出"指向全库"的假关系星群。
    """
    tables = store_datasources.query_tables(conn, ds_id)
    by_name = {t.name: t for t in tables}
    pk_index: dict[str, list[tuple[str, str]]] = {}  # pk列名 -> [(表, 类型)]
    for t in tables:
        for c in store_datasources.query_columns(conn, t.id):
            if c.is_pk:
                pk_index.setdefault(c.name, []).append((t.name, c.type))
    hub_threshold = max(3, len(tables) // 4)
    hub_pk_names = {name for name, owners in pk_index.items() if len(owners) > hub_threshold}
    if hub_pk_names:
        logger.info(
            "枢纽列过滤 ds=%s: %s（>%d 表共现，不参与关系推断）",
            ds_id,
            sorted(hub_pk_names),
            hub_threshold,
        )
    made = 0
    for t in tables:
        for c in store_datasources.query_columns(conn, t.id):
            if c.is_pk:
                continue
            targets: list[str] = []
            if c.name.endswith("_id") and len(c.name) > 3:
                prefix = c.name[:-3]
                for cand in (prefix, f"{prefix}s", f"{prefix}es"):
                    if cand in by_name and cand != t.name:
                        targets.append(f"{cand}.id")
            for pk_name, owners in pk_index.items():
                if pk_name in hub_pk_names:
                    continue
                if c.name == pk_name:
                    targets.extend(f"{owner}.{pk_name}" for owner, _t in owners if owner != t.name)
            for to_ref in targets:
                from_ref = f"{t.name}.{c.name}"
                if _relation_confirmed_or_pending(conn, ds_id, from_ref, to_ref):
                    continue
                payload = {"from": from_ref, "to": to_ref, "via": "name_match"}
                if _exists_pending_or_ignored(conn, ds_id, "relation", payload):
                    continue
                store_suggestions.save_suggestion(
                    conn, datasource_id=ds_id, kind="relation", payload=payload, confidence=0.6
                )
                made += 1
    if made:
        logger.info("关系推断建议 ds=%s: +%d 条", ds_id, made)
    return made


def build_comment_prompt(items: list[dict[str, Any]]) -> str:
    """注释补全提示词（comment 类建议；items=[{table, columns:[缺注释字段名]}]）。"""
    lines = [
        "请为以下库表补全中文业务注释（表注释与字段注释），"
        "输出严格 JSON 数组，元素形如 "
        '{"table": "...", "comment": "...", '
        '"columns": {"字段名": "注释"}}；只输出 JSON：'
    ]
    for it in items:
        cols = "、".join(it["columns"]) or "（无）"
        lines.append(f"- 表 {it['table']}（缺注释字段：{cols}）")
    return "\n".join(lines)


def parse_comment_suggestions(raw: str) -> list[dict[str, Any]]:
    """LLM 输出 → comment 建议 payload 列表（解析失败返回空，fail-quiet）。"""
    import re

    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    out: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        table = str(item.get("table") or "").strip()
        if not table:
            continue
        if str(item.get("comment") or "").strip():
            out.append({"table": table, "column": "", "suggested": str(item["comment"]).strip()[:200]})
        for col, cmt in (item.get("columns") or {}).items():
            if str(cmt).strip():
                out.append({"table": table, "column": str(col).strip(), "suggested": str(cmt).strip()[:200]})
    return out


def comment_candidates(
    conn: sqlite3.Connection, ds_id: int, *, max_tables: int = 8, max_cols: int = 20
) -> list[dict[str, Any]]:
    """缺注释候选（表注释缺 或 有缺注释字段），限量防 prompt 膨胀。"""
    out: list[dict[str, Any]] = []
    for t in store_datasources.query_tables(conn, ds_id):
        missing = [
            c.name
            for c in store_datasources.query_columns(conn, t.id)
            if not (c.comment_custom or c.comment_db)
        ]
        if not (t.comment_custom or t.comment_db) or missing:
            out.append({"table": t.name, "columns": missing[:max_cols]})
        if len(out) >= max_tables:
            break
    return out


def save_comment_suggestions(conn: sqlite3.Connection, ds_id: int, suggestions: list[dict[str, Any]]) -> int:
    """comment 建议落队（判重幂等；表级建议 column="" 由采纳端按表处理——
    采纳映射当前仅支持字段级（C6.8），表级建议入库但采纳时按字段不存在跳过，
    留待上游契约扩展（TODO 回写 C6.8）。"""
    made = 0
    for s in suggestions:
        if not s.get("column"):  # 表级注释建议本期不产（采纳映射仅字段级）
            continue
        if _exists_pending_or_ignored(conn, ds_id, "comment", s):
            continue
        store_suggestions.save_suggestion(
            conn, datasource_id=ds_id, kind="comment", payload=s, confidence=0.7
        )
        made += 1
    return made
