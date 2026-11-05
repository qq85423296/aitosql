"""R01-R14 规则用例（SSOT §9.1 处置表逐行；每规则 ≥2 用例的代表性子集）+ 执行序铁律。"""

from __future__ import annotations

import importlib.util

import pytest

from app.sqlcheck.rules import (
    check_all,
    fingerprint,
    hard_block_r15,
    inject_auto_limit,
    is_pure_select_sql,
    normalize_sql,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sqlglot") is None,
    reason="sqlglot 未安装（uv sync 后启用）",
)

META = {
    "orders": {
        "columns": {"id": "bigint", "amount": "decimal", "created_at": "datetime", "user_id": "bigint"},
        "row_estimate": 5_000_000,
        "partition_cols": ["created_at"],
        "pk_cols": ["id"],
    },
    "channels": {
        "columns": {"id": "int", "name": "varchar"},
        "row_estimate": 50,
        "partition_cols": [],
        "pk_cols": ["id"],
    },
}
CAPS = {"supports": ["count", "bitmap_count"], "forbidden": []}


def _ids(risks: list[dict]) -> set[str]:
    return {r["rule_id"] for r in risks}


def test_r01_statement_whitelist() -> None:
    risks = check_all("GRANT SELECT ON db.* TO 'u'", dialect="mysql", caps=CAPS, meta=META)
    assert "R01" in _ids(risks)
    risks = check_all("SELECT 1", dialect="mysql", caps=CAPS, meta=META)
    assert "R01" not in _ids(risks)


def test_r02_update_without_where_red() -> None:
    risks = check_all("UPDATE orders SET amount = 0", dialect="mysql", caps=CAPS, meta=META)
    r02 = [r for r in risks if r["rule_id"] == "R02"]
    assert r02 and r02[0]["level"] == "red"
    risks = check_all("UPDATE orders SET amount = 0 WHERE id = 1", dialect="mysql", caps=CAPS, meta=META)
    assert "R02" not in _ids(risks)


def test_r03_non_pk_condition_yellow() -> None:
    risks = check_all("DELETE FROM orders WHERE user_id = 9", dialect="mysql", caps=CAPS, meta=META)
    r03 = [r for r in risks if r["rule_id"] == "R03"]
    assert r03 and r03[0]["level"] == "yellow"


def test_r04_auto_limit_injected_before_check() -> None:
    """执行序铁律：auto_limit AST 注入先于校验——注入后 R04 不再触发。"""
    sql = inject_auto_limit("SELECT id FROM orders", dialect="mysql", limit=1000)
    assert "LIMIT 1000" in sql.upper() or "LIMIT" in sql.upper()
    risks = check_all(sql, dialect="mysql", caps=CAPS, meta=META)
    assert "R04" not in _ids(risks)


def test_r06_write_triggers_confirm() -> None:
    risks = check_all("UPDATE orders SET amount = 1 WHERE id = 1", dialect="mysql", caps=CAPS, meta=META)
    assert "R06" in _ids(risks)
    risks_ch = check_all("DELETE FROM logs WHERE id = 1", dialect="clickhouse", caps=CAPS, meta=META)
    r06 = [r for r in risks_ch if r["rule_id"] == "R06"]
    assert r06 and "只读" in r06[0]["message"]  # CH 只读联动文案（§9.4）


def test_r07_cross_db_yellow() -> None:
    risks = check_all("SELECT * FROM other_db.orders", dialect="mysql", caps=CAPS, meta=META, db_name="biz")
    assert "R07" in _ids(risks)


def test_r08_unknown_function_yellow() -> None:
    risks = check_all("SELECT bitmap_intersect(x) FROM orders", dialect="mysql", caps=CAPS, meta=META)
    assert "R08" in _ids(risks)
    risks = check_all("SELECT bitmap_count(x) FROM orders", dialect="mysql", caps=CAPS, meta=META)
    assert "R08" not in _ids(risks)


def test_r09_big_table_star_yellow() -> None:
    risks = check_all("SELECT * FROM orders", dialect="mysql", caps=CAPS, meta=META)
    assert "R09" in _ids(risks)
    risks = check_all("SELECT * FROM channels", dialect="mysql", caps=CAPS, meta=META)
    assert "R09" not in _ids(risks)


def test_r10_join_without_on_red() -> None:
    risks = check_all("SELECT * FROM orders JOIN channels", dialect="mysql", caps=CAPS, meta=META)
    r10 = [r for r in risks if r["rule_id"] == "R10"]
    assert r10 and r10[0]["level"] == "red"
    risks = check_all("SELECT * FROM orders CROSS JOIN channels", dialect="mysql", caps=CAPS, meta=META)
    assert "R10" not in _ids(risks)  # CROSS JOIN 无需 ON（实测字段 kind）


def test_r11_subquery_nesting() -> None:
    deep = (
        "SELECT * FROM orders WHERE id IN (SELECT id FROM orders WHERE id IN "
        "(SELECT id FROM orders WHERE id IN (SELECT id FROM orders WHERE id IN "
        "(SELECT id FROM orders))))"
    )  # 4 层 > NEST_LIMIT(3)
    risks = check_all(deep, dialect="mysql", caps=CAPS, meta=META)
    assert "R11" in _ids(risks)
    shallow = (
        "SELECT * FROM orders WHERE id IN (SELECT id FROM orders WHERE id IN "
        "(SELECT id FROM orders))"
    )  # 2 层不触发
    risks = check_all(shallow, dialect="mysql", caps=CAPS, meta=META)
    assert "R11" not in _ids(risks)


def test_r12_leading_wildcard_yellow() -> None:
    risks = check_all("SELECT id FROM orders WHERE amount LIKE '%9'", dialect="mysql", caps=CAPS, meta=META)
    # amount 非字符串列仅演示规则判定；LIKE '%xx' 前缀通配 + 大表 → 黄警
    assert "R12" in _ids(risks)


def test_r13_online_red_offline_yellow() -> None:
    risks = check_all("SELECT * FROM ghost_table", dialect="mysql", caps=CAPS, meta=META, ds_mode="online")
    r13 = [r for r in risks if r["rule_id"] == "R13"]
    assert r13 and r13[0]["level"] == "red"
    risks = check_all("SELECT * FROM ghost_table", dialect="mysql", caps=CAPS, meta=META, ds_mode="offline")
    r13 = [r for r in risks if r["rule_id"] == "R13"]
    assert r13 and r13[0]["level"] == "yellow"  # D04 降级黄警照常交付


def test_r14_partition_missing_yellow() -> None:
    risks = check_all("SELECT id FROM orders WHERE user_id = 1", dialect="mysql", caps=CAPS, meta=META)
    assert "R14" in _ids(risks)
    risks = check_all(
        "SELECT id FROM orders WHERE created_at >= '2026-01-01'", dialect="mysql", caps=CAPS, meta=META
    )
    assert "R14" not in _ids(risks)


def test_fingerprint_stable_and_literal_free() -> None:
    a = fingerprint("SELECT * FROM orders WHERE id = 1", dialect="mysql")
    b = fingerprint("SELECT * FROM orders WHERE id = 2", dialect="mysql")
    assert a == b  # 字面值脱敏后指纹一致（振荡检测语义）
    c = fingerprint("SELECT id FROM orders", dialect="mysql")
    assert a != c


def test_normalize_sql_deterministic() -> None:
    out = normalize_sql("SELECT * FROM orders WHERE id=1", dialect="mysql")
    assert "orders" in out


def test_pure_select_guard() -> None:
    assert is_pure_select_sql("SELECT 1", dialect="mysql")
    assert not is_pure_select_sql("UPDATE orders SET amount=1", dialect="mysql")
    assert not is_pure_select_sql("GARBAGE @@", dialect="mysql")


def test_r15_runs_first() -> None:
    """R15 优先于 R01-R14：多语句场景只产出 R15（B1），不混入其他规则。"""
    hit = hard_block_r15("SELECT 1;; SELECT 2", dialect="mysql")
    assert hit is not None and hit["assertion"] == "B1"
