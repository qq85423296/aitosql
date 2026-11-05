"""脱敏规则表用例（C9 / SSOT §12.5）：三出口统一入口。"""

from __future__ import annotations

import importlib.util

import pytest

from app.sqlcheck.clean import mask_error, mask_sql_literals, unicode_clean

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sqlglot") is None,
    reason="sqlglot 未安装（uv sync 后启用）",
)


def test_mask_host_port() -> None:
    out = mask_error("Access denied for user 'admin'@'10.0.0.8:3306'")
    assert "10.0.0.8" not in out
    assert "[HOST]" in out


def test_mask_credentials() -> None:
    out = mask_error("connect: password=Sup3rSecret! user=svc_ro failed")
    assert "Sup3rSecret!" not in out
    assert "[CREDENTIAL]" in out


def test_ip_allowlist_kept() -> None:
    out = mask_error("listening on 127.0.0.1:8321 refused")
    assert "127.0.0.1" in out  # 白名单字面量放行


def test_url_credential_masked() -> None:
    out = mask_error("dial mysql://root:topsecret@10.1.1.2:3306/biz fail")
    assert "topsecret" not in out
    assert "[CREDENTIAL]" in out


def test_mask_sql_literals() -> None:
    out = mask_sql_literals("SELECT * FROM orders WHERE amount > 99.5 AND status = 'paid'", "mysql")
    assert "99.5" not in out and "paid" not in out
    assert "?" in out


def test_mask_sql_literals_parse_fail_passthrough() -> None:
    raw = "SELECT * FROM t WHERE x = 'unterminated"  # 未闭合字符串 → 确定解析失败
    assert mask_sql_literals(raw, "mysql") == raw  # 解析失败原样（R15 B2 另行把关）


def test_unicode_clean() -> None:
    dirty = "SELECT⁠ *​ FROM orders"  # 含 WJ/ZWSP
    assert unicode_clean(dirty) == "SELECT * FROM orders"


# ---------- F2：host 脱敏完整性（E1001 三出口） ----------


def test_mask_tld_whitelist_extended() -> None:
    """TLD 白名单扩列：.org/.dev/.ai/.cloud 等不再原样泄漏。"""
    for host in ("db.internal.example.org", "svc-01.prod.dev", "mysql.dba.ai", "rds.ap-southeast.cloud"):
        out = mask_error(f"dial tcp {host}:3306 timeout")
        assert host not in out
        assert "[HOST]" in out


def test_mask_driver_connect_message_any_host() -> None:
    """pymysql 连接消息结构化提取：任意后缀/任意主机形态（含短主机名）均脱敏。"""
    cases = (
        "(2003, \"Can't connect to MySQL server on 'db-warehouse' (timed out)\")",
        "(2003, \"Can't connect to MySQL server on 'db.internal.example.org' (111)\")",
        "(2005, \"Unknown MySQL server host 'doris-fe-01' (-3)\")",
        "(2013, \"Lost connection to MySQL server at 'db-warehouse:3306'\")",
    )
    for raw in cases:
        out = mask_error(raw)
        for leaked in ("db-warehouse", "db.internal.example.org", "doris-fe-01"):
            assert leaked not in out, f"泄漏: {raw} -> {out}"
        assert "[HOST]" in out


def test_mask_driver_connect_message_allowlist_kept() -> None:
    """驱动消息中的 127.0.0.1/localhost 白名单放行（与 IP 白名单口径一致）。"""
    out = mask_error("(2003, \"Can't connect to MySQL server on '127.0.0.1' (111)\")")
    assert "127.0.0.1" in out


def test_mask_qualified_table_name_not_masked() -> None:
    """SQL 错误里的 db.table 限定名不得误伤——修正回路需要表名回喂 LLM。"""
    out = mask_error("Table 'shop.orders' doesn't exist")
    assert "shop.orders" in out
