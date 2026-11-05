"""AI 补全引擎单测（方案 v1.1 §4 五关校验 / §5 准确性管线）。

覆盖：JSON 解析兜底、comment/std_bind 校验（幻觉防火墙 + 置信度钳制）、
表级注释采纳映射（C6.8+）、编辑后采纳、采样画像聚合。
"""

from __future__ import annotations

import json

from app.ai.completion import (
    parse_json_array,
    profile_table,
    validate_comment_items,
    validate_std_bind_items,
)
from app.store import db as store_db
from app.store import suggestions as store_suggestions


def test_parse_json_array_tolerates_prose() -> None:
    raw = '好的，以下是结果：\n[{"a": 1}]\n希望对你有帮助'
    assert parse_json_array(raw) == [{"a": 1}]
    assert parse_json_array("不是 JSON") == []
    assert parse_json_array('{"not": "array"}') == []


def test_validate_comment_items_hallucination_firewall() -> None:
    items = [
        {"target": "col:id", "suggestion": "主键", "confidence": 0.95, "reason": "整型自增"},
        {"target": "col:ghost", "suggestion": "幻觉字段", "confidence": 0.9, "reason": "x"},
        {"target": "table", "suggestion": "订单主表", "confidence": 0.8, "reason": "订单语义"},
        {"target": "col:id", "suggestion": "", "confidence": 0.5, "reason": "空"},
    ]
    out = validate_comment_items(items, "orders", {"id", "amount"})
    assert [(i["column"], i["suggested"]) for i in out] == [("id", "主键"), ("", "订单主表")]
    # 无证据：confidence 被钳到 0.6 上限
    assert all(i["confidence"] <= 0.6 for i in out)


def test_validate_std_bind_items_requires_catalog_match() -> None:
    items = [
        {"column": "amount", "standard": "金额（含税）", "confidence": 0.95, "reason": "金额语义"},
        {"column": "amount", "standard": "臆造标准", "confidence": 0.9, "reason": "x"},
        {"column": "ghost", "standard": "金额（含税）", "confidence": 0.9, "reason": "x"},
    ]
    out = validate_std_bind_items(items, "orders", {"amount"}, {"金额（含税）"})
    assert len(out) == 1 and out[0]["standard"] == "金额（含税）"


def _fresh_db(tmp_path):
    db_path = tmp_path / "app.db"
    store_db.run_migrations(db_path)
    conn = store_db.connect(db_path)
    conn.execute("INSERT INTO datasource(name, dialect, mode) VALUES ('t', 'mysql', 'offline')")
    conn.execute("INSERT INTO table_meta(datasource_id, name) VALUES (1, 'orders')")
    conn.execute("INSERT INTO column_meta(table_id, name, type) VALUES (1, 'id', 'bigint')")
    conn.commit()
    return conn


def test_accept_table_level_comment(tmp_path) -> None:
    """C6.8+：comment.column="" → tables.comment_custom。"""
    conn = _fresh_db(tmp_path)
    sid = store_suggestions.save_suggestion(
        conn,
        datasource_id=1,
        kind="comment",
        payload={"table": "orders", "column": "", "suggested": "订单主表"},
        confidence=0.8,
    )
    store_suggestions.accept_suggestions(conn, [sid])
    row = conn.execute("SELECT comment_custom FROM table_meta WHERE id = 1").fetchone()
    assert row["comment_custom"] == "订单主表"
    conn.close()


def test_accept_with_edits(tmp_path) -> None:
    """编辑后采纳：edits 文本替换 payload.suggested 落库。"""
    conn = _fresh_db(tmp_path)
    sid = store_suggestions.save_suggestion(
        conn,
        datasource_id=1,
        kind="comment",
        payload={"table": "orders", "column": "id", "suggested": "主键"},
        confidence=0.8,
    )
    store_suggestions.accept_suggestions(conn, [sid], edits={sid: "订单 ID（人工修订）"})
    row = conn.execute("SELECT comment_custom FROM column_meta WHERE id = 1").fetchone()
    assert row["comment_custom"] == "订单 ID（人工修订）"
    conn.close()


def test_run_id_recorded_and_filtered(tmp_path) -> None:
    conn = _fresh_db(tmp_path)
    store_suggestions.save_suggestion(
        conn,
        datasource_id=1,
        kind="comment",
        payload={"table": "orders", "column": "id", "suggested": "x"},
        run_id=42,
    )
    store_suggestions.save_suggestion(
        conn, datasource_id=1, kind="comment", payload={"table": "orders", "column": "id", "suggested": "y"}
    )
    by_run = store_suggestions.query_suggestions(conn, 1, run_id=42)
    assert len(by_run) == 1 and by_run[0].run_id == 42
    conn.close()


def test_profile_table_aggregates() -> None:
    rows = [("paid", 10), ("paid", 20), (None, 5), ("done", 1)]

    def q(sql, params):
        return rows

    prof = profile_table(q, "mysql", "orders", ["status"], limit=50)
    assert prof["status"]["top_values"][0] == "paid"
    assert prof["status"]["nulls"] == 1
    assert prof["status"]["cardinality"] == 2


def test_payload_reason_persisted(tmp_path) -> None:
    """依据随建议落库（§5-2：reason 入 payload）。"""
    conn = _fresh_db(tmp_path)
    sid = store_suggestions.save_suggestion(
        conn,
        datasource_id=1,
        kind="comment",
        payload={"table": "orders", "column": "id", "suggested": "主键", "reason": "整型自增"},
        confidence=0.9,
        run_id=7,
    )
    sug = store_suggestions.get_suggestion(conn, sid)
    assert sug.payload["reason"] == "整型自增"
    assert json.loads(json.dumps(sug.payload))["reason"] == "整型自增"
    conn.close()
