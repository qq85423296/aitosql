"""C2 真实引擎集成分组（golden_engine marker，R-10 CI 容器组）。

默认 `pytest -q` 不因 Docker/网络不可用失败：未显式启用时整组 skip。
启用方式（PowerShell 示例）：

    $env:AITOSQL_TEST_ENGINE = "1"
    $env:AITOSQL_TEST_MYSQL_DSN = "mysql://root:secret@127.0.0.1:3306/test"
    $env:AITOSQL_TEST_DORIS_DSN = "doris://root@127.0.0.1:9030/test"
    $env:AITOSQL_TEST_CH_DSN   = "clickhouse://default@127.0.0.1:8123/default"
    pytest -m golden_engine -q

每个引擎覆盖：一个真实查询 + 一个资源/方言行为（预算硬失败 / readonly 生效）。
服务不可达时按用例 skip（不伪装通过）；CI 须以健康检查保证服务就绪。
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import unquote, urlparse

import pytest

from app.exec import runner as exec_runner

pytestmark = pytest.mark.golden_engine

_ENABLED = os.environ.get("AITOSQL_TEST_ENGINE") == "1"
_DSN_ENV = {"mysql": "AITOSQL_TEST_MYSQL_DSN", "doris": "AITOSQL_TEST_DORIS_DSN",
            "clickhouse": "AITOSQL_TEST_CH_DSN"}
_DEFAULT_PORT = {"mysql": 3306, "doris": 9030, "clickhouse": 8123}


def _ds_from_env(dialect: str) -> dict[str, Any]:
    """DSN 环境变量 → datasource dict；未启用/未配置/服务不可达均明确 skip。"""
    if not _ENABLED:
        pytest.skip("真实引擎分组未启用（设 AITOSQL_TEST_ENGINE=1 并提供 DSN 后运行）")
    raw = os.environ.get(_DSN_ENV[dialect])
    if not raw:
        pytest.skip(f"未配置 {_DSN_ENV[dialect]}（{dialect} 真实引擎用例跳过）")
    u = urlparse(raw)
    ds: dict[str, Any] = {
        "id": 990001,
        "dialect": dialect,
        "host": u.hostname or "127.0.0.1",
        "port": u.port or _DEFAULT_PORT[dialect],
        "username": unquote(u.username or ""),
        "db_name": (u.path or "").lstrip("/") or None,
    }
    password = unquote(u.password or "") or None
    try:
        conn = exec_runner.connect_engine(
            ds, password, timeout_s=5, readonly=(dialect == "clickhouse")
        )
    except Exception as exc:
        pytest.skip(f"{dialect} 服务不可达（{type(exc).__name__}）：请先做容器健康检查")
    return {"ds": ds, "password": password, "conn": conn}


@pytest.fixture()
def mysql_env():
    env = _ds_from_env("mysql")
    yield env
    exec_runner._close_quietly(env["conn"])


@pytest.fixture()
def doris_env():
    env = _ds_from_env("doris")
    yield env
    exec_runner._close_quietly(env["conn"])


@pytest.fixture()
def ch_env():
    env = _ds_from_env("clickhouse")
    yield env
    exec_runner._close_quietly(env["conn"])


_ROWS3 = "SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3"


def _assert_budget_hard_fail(env: dict[str, Any], dialect: str) -> None:
    columns, rows = exec_runner.engine_query_with_columns(env["conn"], dialect, _ROWS3, max_rows=10)
    assert columns == ["n"] and [r[0] for r in rows] == [1, 2, 3]
    with pytest.raises(exec_runner.ResourceLimitError):
        exec_runner.engine_query_with_columns(env["conn"], dialect, _ROWS3, max_rows=2)


def test_mysql_query_and_budget(mysql_env) -> None:
    """MySQL：真实 SELECT + 行预算硬失败（引擎 throw/客户端收口，非静默截断）。"""
    _assert_budget_hard_fail(mysql_env, "mysql")


def test_doris_query_and_budget(doris_env) -> None:
    """Doris：真实 SELECT + 行预算硬失败（与 MySQL 兼容执行路径同源验证）。"""
    _assert_budget_hard_fail(doris_env, "doris")


def test_clickhouse_query_and_readonly(ch_env) -> None:
    """ClickHouse：真实 SELECT + readonly=1 在服务端真实生效（getSetting 验证）。"""
    columns, rows = exec_runner.engine_query_with_columns(
        ch_env["conn"], "clickhouse", "SELECT getSetting('readonly') AS r", max_rows=10
    )
    assert columns == ["r"]
    assert rows and int(rows[0][0]) >= 1  # D05：readonly=1 参数真实传递到引擎
