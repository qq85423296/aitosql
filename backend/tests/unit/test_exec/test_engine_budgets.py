"""B2 查询资源边界：engine_query_with_columns 行数/字节/deadline 硬失败契约。

铁律：预算触顶必须显式失败（ResourceLimitError），不得静默截断伪装成功；
超限或执行异常后连接必须关闭，不得回收到可复用连接池；
ClickHouse 服务端预算与 readonly=1 参数以 stub 验证（本机不启动真实引擎，
真实引擎兼容性验证见 C2 分组，标记为未在本轮验证）。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.exec import runner as exec_runner


@pytest.fixture(autouse=True)
def _clean_pools():
    exec_runner._POOLS.clear()
    yield
    exec_runner._POOLS.clear()


# ---------- MySQL/Doris 路径假连接（SSCursor 流式） ----------


class _FakeMyCursor:
    def __init__(self, conn: _FakeMyConn) -> None:
        self._conn = conn
        self.description: list[tuple] = []
        self._batches: list[list[tuple]] = []

    def __enter__(self) -> _FakeMyCursor:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        if sql == "SELECT CONNECTION_ID()":
            self.description = [("CONNECTION_ID()",)]
            self._batches = [[(7,)]]
            return
        if self._conn.exc is not None:
            raise self._conn.exc
        self.description = [(c,) for c in self._conn.cols]
        self._batches = list(self._conn.batch_script)

    def fetchmany(self, size: int = 128) -> list[tuple]:
        return self._batches.pop(0) if self._batches else []

    def fetchall(self) -> list[tuple]:
        out = [r for b in self._batches for r in b]
        self._batches = []
        return out


class _FakeMyConn:
    """batch_script：fetchmany 逐批吐行；exc：execute 期抛错。"""

    def __init__(
        self,
        *,
        cols: list[str] | None = None,
        batch_script: list[list[tuple]] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.cols = cols or ["c1"]
        self.batch_script = batch_script if batch_script is not None else [[(1,)]]
        self.exc = exc
        self.closed = False
        self.cursor_classes: list[Any] = []

    def cursor(self, cursor_class: Any = None) -> _FakeMyCursor:
        self.cursor_classes.append(cursor_class)
        return _FakeMyCursor(self)

    def close(self) -> None:
        self.closed = True


# ---------- 预算硬失败：行数 / 字节 / deadline ----------


def test_mysql_max_rows_hard_fail_and_conn_closed() -> None:
    conn = _FakeMyConn(batch_script=[[(1,), (2,)], [(3,)]])
    with pytest.raises(exec_runner.ResourceLimitError, match="row/byte budget"):
        exec_runner.engine_query_with_columns(conn, "mysql", "SELECT c1 FROM t", max_rows=2)
    assert conn.closed is True  # 超限连接关闭，不回池


def test_mysql_max_bytes_hard_fail_and_conn_closed() -> None:
    conn = _FakeMyConn(batch_script=[[("x" * 100,)]])
    with pytest.raises(exec_runner.ResourceLimitError, match="row/byte budget"):
        exec_runner.engine_query_with_columns(conn, "doris", "SELECT c1 FROM t", max_bytes=50)
    assert conn.closed is True


def test_mysql_deadline_hard_fail_no_partial_success() -> None:
    conn = _FakeMyConn(batch_script=[[(1,), (2,)]])
    with pytest.raises(exec_runner.ResourceLimitError, match="deadline"):
        exec_runner.engine_query_with_columns(
            conn, "mysql", "SELECT c1 FROM t", deadline=time.monotonic() - 1
        )
    assert conn.closed is True


def test_mysql_within_budget_succeeds_unbuffered() -> None:
    import pymysql

    conn = _FakeMyConn(cols=["a", "b"], batch_script=[[(1, "x")], [(2, "y")]])
    columns, rows = exec_runner.engine_query_with_columns(
        conn, "mysql", "SELECT a, b FROM t", max_rows=10, max_bytes=1024
    )
    assert columns == ["a", "b"] and rows == [(1, "x"), (2, "y")]
    assert conn.closed is False  # 成功路径连接可回收
    # MySQL/Doris 兼容路径走 SSCursor 无缓冲流式拉取（内存不收全量）
    assert pymysql.cursors.SSCursor in conn.cursor_classes


def test_mysql_exec_error_closes_conn_and_propagates() -> None:
    conn = _FakeMyConn(exc=ValueError("engine boom"))
    with pytest.raises(ValueError, match="engine boom"):
        exec_runner.engine_query_with_columns(conn, "mysql", "SELECT c1 FROM t")
    assert conn.closed is True


# ---------- ClickHouse 路径：服务端预算 + readonly 参数传递（stub） ----------


class _FakeCHStream:
    def __init__(self, rows: list[tuple], cols: list[str]) -> None:
        self._rows = rows
        self.source = SimpleNamespace(column_names=cols)

    def __enter__(self) -> _FakeCHStream:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def __iter__(self):
        return iter(self._rows)


class _FakeCHConn:
    def __init__(self, rows: list[tuple], cols: list[str]) -> None:
        self._rows = rows
        self._cols = cols
        self.stream_calls: list[tuple[str, dict[str, Any]]] = []

    def query_rows_stream(self, sql: str, *, settings: dict[str, Any]) -> _FakeCHStream:
        self.stream_calls.append((sql, settings))
        return _FakeCHStream(self._rows, self._cols)


def test_clickhouse_server_side_budget_settings() -> None:
    conn = _FakeCHConn(rows=[(1, "x")], cols=["a", "b"])
    deadline = time.monotonic() + 20
    columns, rows = exec_runner.engine_query_with_columns(
        conn, "clickhouse", "SELECT a, b FROM t", max_rows=5, max_bytes=4096, deadline=deadline
    )
    assert columns == ["a", "b"] and rows == [(1, "x")]
    _sql, settings = conn.stream_calls[0]
    # 服务端预算收口：超预算由引擎 throw，客户端不做静默截断
    assert settings["max_result_rows"] == 5
    assert settings["max_result_bytes"] == 4096
    assert settings["result_overflow_mode"] == "throw"
    assert 1 <= settings["max_execution_time"] <= 20


def test_clickhouse_readonly_setting_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    import clickhouse_connect

    captured: list[dict[str, Any]] = []

    def fake_get_client(**kwargs: Any) -> object:
        captured.append(kwargs)
        return object()

    monkeypatch.setattr(clickhouse_connect, "get_client", fake_get_client)
    ds = {"dialect": "clickhouse", "host": "127.0.0.1", "port": 8123, "username": "u", "db_name": "d"}
    exec_runner.connect_engine(ds, "pw", timeout_s=10, readonly=True)
    exec_runner.connect_engine(ds, None, timeout_s=10, readonly=False)
    assert captured[0]["settings"] == {"readonly": 1}  # D05：CH readonly=1 强制
    assert captured[1]["settings"] == {}


# ---------- run_query：预算失败连接不入池；建连失败映射 ConnectError ----------


def test_run_query_budget_breach_discards_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeMyConn(batch_script=[[(1,), (2,)]])
    monkeypatch.setattr(exec_runner, "connect_engine", lambda *a, **k: conn)
    with pytest.raises(exec_runner.ResourceLimitError):
        exec_runner.run_query(
            "SELECT c1 FROM t", query_id="q_bud", ds={"id": 9201, "dialect": "mysql"},
            password=None, max_rows=1,
        )
    assert conn.closed is True  # 超预算连接关闭
    assert exec_runner._POOLS[9201]._idle.empty()  # 不回收到可复用池


def test_run_query_acquire_failure_maps_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: Any, **k: Any) -> Any:
        raise TimeoutError("Connection pool wait deadline exceeded")

    monkeypatch.setattr(exec_runner, "connect_engine", _boom)
    with pytest.raises(exec_runner.ConnectError):
        exec_runner.run_query(
            "SELECT c1 FROM t", query_id="q_acq", ds={"id": 9202, "dialect": "mysql"}, password=None
        )
