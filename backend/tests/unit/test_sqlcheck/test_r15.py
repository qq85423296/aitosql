"""R15 五断言探针复现用例（T1.4 验收锚点；证据 = .roundtable/evidence/probe_r2_out.txt，
sqlglot 30.18 实测）。每条断言 ≥2 用例。
"""

from __future__ import annotations

import importlib.util

import pytest

from app.sqlcheck.rules import R15_MESSAGES, hard_block_r15

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sqlglot") is None,
    reason="sqlglot 未安装（uv sync 后启用）",
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1;; SELECT 2",  # 探针实测：[Select, None, Select]
        "SELECT 1; SELECT 2",
        "DELETE FROM t WHERE id=1; DROP TABLE t",
    ],
)
def test_b1_multi_statement(sql: str) -> None:
    hit = hard_block_r15(sql, dialect="mysql")
    assert hit is not None and hit["assertion"] == "B1"
    assert hit["message"] == R15_MESSAGES["B1"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * INTO OUTFILE '/tmp/x.csv' FROM orders",  # 尾置语序
        "SELECT * FROM orders INTO OUTFILE '/tmp/x.csv'",  # FROM 后语序
        "SELECT * FROM orders INTO DUMPFILE '/tmp/x'",  # DUMPFILE
        "LOAD DATA LOCAL INFILE '/etc/passwd' INTO TABLE users",  # LOAD DATA [LOCAL]
        "DO SLEEP(5)",  # DO → PARSE_FAIL
        "KILL QUERY 123",  # 探针实测 PARSE_FAIL
    ],
)
def test_b2_parse_fail_closed(sql: str) -> None:
    hit = hard_block_r15(sql, dialect="mysql")
    assert hit is not None and hit["assertion"] == "B2"
    assert hit["message"] == R15_MESSAGES["B2"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT amount INTO @v FROM orders WHERE id = 1",  # 探针实测：唯一可解析 exp.Into
    ],
)
def test_b3_into_variable(sql: str) -> None:
    hit = hard_block_r15(sql, dialect="mysql")
    assert hit is not None and hit["assertion"] == "B3"
    assert "导出 CSV" in hit["message"]


def test_b3_doris_wording() -> None:
    hit = hard_block_r15("SELECT amount INTO @v FROM orders", dialect="doris")
    assert hit is not None and hit["assertion"] == "B3"
    assert hit["message"] == R15_MESSAGES["B3_doris"]  # 出境通道文案


@pytest.mark.parametrize("dialect", ["mysql", "doris", "clickhouse"])
def test_b4_explain_analyze(dialect: str) -> None:
    hit = hard_block_r15("EXPLAIN ANALYZE SELECT * FROM orders", dialect=dialect)
    assert hit is not None and hit["assertion"] == "B4"


def test_b4_explain_analyze_wrapping_select() -> None:
    """EXPLAIN ANALYZE 包纯 SELECT 同样阻断（探针形态 Describe.style=ANALYZE）。"""
    hit = hard_block_r15("EXPLAIN ANALYZE SELECT id FROM orders WHERE amount > 100", dialect="doris")
    assert hit is not None and hit["assertion"] == "B4"


@pytest.mark.parametrize(
    "sql",
    [
        "CALL refresh_report()",  # CALL → exp.Command
        "CALL `etl`.`nightly`()",
    ],
)
def test_b5_call(sql: str) -> None:
    hit = hard_block_r15(sql, dialect="mysql")
    assert hit is not None and hit["assertion"] == "B5"
    assert hit["message"] == R15_MESSAGES["B5"]


@pytest.mark.parametrize(
    "sql,dialect",
    [
        ("SELECT id, amount FROM orders WHERE created_at >= '2026-01-01' LIMIT 100", "mysql"),
        ("WITH t AS (SELECT 1) SELECT * FROM t", "doris"),
        ("SELECT uniq(user_id) FROM logs", "clickhouse"),
        ("UPDATE orders SET status='paid' WHERE id=1", "mysql"),  # 写语句非 R15，走 R06
        ("SELECT 1", "mysql"),
    ],
)
def test_pass_normal(sql: str, dialect: str) -> None:
    assert hard_block_r15(sql, dialect=dialect) is None
