"""F1/F16/F17：执行中连接类失败归并 ConnectError（E1001 通道，不进修正回路）、
锁外建连（慢 factory 不串行化整池）、执行期抛错的连接不再回池。"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from app.exec import runner as exec_runner


def _pymysql_op_error(errno: int, message: str) -> Exception:
    """动态造 pymysql 风格 OperationalError（不硬依赖驱动包）。"""
    cls = type("OperationalError", (Exception,), {"__module__": "pymysql.err"})
    return cls(errno, message)


def _ch_op_error(message: str) -> Exception:
    cls = type("OperationalError", (Exception,), {"__module__": "clickhouse_connect.driver.exceptions"})
    return cls(message)


# ---------- F1：_is_connect_failure 分类 ----------


@pytest.mark.parametrize("errno", (2002, 2003, 2006, 2013, 2055))
def test_connect_failure_pymysql_errno(errno: int) -> None:
    assert exec_runner._is_connect_failure(_pymysql_op_error(errno, "lost")) is True


def test_connect_failure_oserror_family() -> None:
    assert exec_runner._is_connect_failure(ConnectionResetError("reset")) is True
    assert exec_runner._is_connect_failure(TimeoutError("read timed out")) is True


def test_connect_failure_clickhouse_operational() -> None:
    assert exec_runner._is_connect_failure(_ch_op_error("EOF")) is True


def test_not_connect_failure_sql_errors() -> None:
    # 1064 语法错误等 SQL 层错误必须留在 EngineError 通道（修正回路依赖）
    assert exec_runner._is_connect_failure(_pymysql_op_error(1064, "syntax")) is False
    assert exec_runner._is_connect_failure(ValueError("x")) is False


# ---------- 假连接（CONNECTION_ID / SELECT 1 ping / 主查询 三态） ----------


class _FakeCursor:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.description: list[tuple] = [("c1",)]
        self._exhausted = False

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        if sql in ("SELECT CONNECTION_ID()", "SELECT 1"):
            self._conn.mode = "meta"
            return
        if self._conn.exc is not None:
            raise self._conn.exc
        self._conn.mode = "rows"

    def fetchmany(self, size: int = 128) -> list[tuple]:
        if self._exhausted:
            return []
        self._exhausted = True
        return self.fetchall()

    def fetchall(self) -> list[tuple]:
        return [(7,)] if self._conn.mode == "meta" else [(1,)]


class _FakeConn:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.mode = "rows"
        self.closed = False

    def cursor(self, cursor_class=None) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_pools():
    exec_runner._POOLS.clear()
    yield
    exec_runner._POOLS.clear()


_MAIN_SQL = "SELECT amount FROM orders"  # 主查询：避开 "SELECT 1"（与 ping 撞语句）


# ---------- F1：run_query 执行中连接错误归并 ConnectError ----------


def test_run_query_mid_exec_connect_error_becomes_connect_error(monkeypatch) -> None:
    conn = _FakeConn(_pymysql_op_error(2013, "Lost connection during query"))
    monkeypatch.setattr(exec_runner, "connect_engine", lambda *a, **k: conn)
    with pytest.raises(exec_runner.ConnectError):
        exec_runner.run_query(_MAIN_SQL, query_id="q_mid", ds={"id": 9101, "dialect": "mysql"}, password=None)


def test_run_query_mid_exec_sql_error_stays_engine_error(monkeypatch) -> None:
    conn = _FakeConn(_pymysql_op_error(1064, "syntax error"))
    monkeypatch.setattr(exec_runner, "connect_engine", lambda *a, **k: conn)
    with pytest.raises(exec_runner.EngineError):
        exec_runner.run_query(_MAIN_SQL, query_id="q_sql", ds={"id": 9102, "dialect": "mysql"}, password=None)


# ---------- F17：执行期抛错的连接不再回池；成功路径照常复用 ----------


def test_broken_connection_not_returned_to_pool(monkeypatch) -> None:
    conn = _FakeConn(_pymysql_op_error(2013, "gone"))
    monkeypatch.setattr(exec_runner, "connect_engine", lambda *a, **k: conn)
    with pytest.raises(exec_runner.ConnectError):
        exec_runner.run_query(
            _MAIN_SQL, query_id="q_broken", ds={"id": 9103, "dialect": "mysql"}, password=None
        )
    assert conn.closed is True  # 坏连接被关闭
    assert exec_runner._POOLS[9103]._idle.empty()  # 且未回池滞留
    # 信号量槽位已归还：再次 acquire（同池）不阻塞
    conn2 = _FakeConn(None)
    monkeypatch.setattr(exec_runner, "connect_engine", lambda *a, **k: conn2)
    exec_runner.run_query(_MAIN_SQL, query_id="q_next", ds={"id": 9103, "dialect": "mysql"}, password=None)


def test_healthy_connection_returned_to_pool(monkeypatch) -> None:
    conn = _FakeConn(None)
    monkeypatch.setattr(exec_runner, "connect_engine", lambda *a, **k: conn)
    exec_runner.run_query(_MAIN_SQL, query_id="q_ok", ds={"id": 9104, "dialect": "mysql"}, password=None)
    assert conn.closed is False
    assert exec_runner._POOLS[9104]._idle.qsize() == 1  # 健康连接回池复用


# ---------- F16：锁外建连——慢 factory 下并发获取不串行 ----------


def test_acquire_concurrent_factories_not_serialized() -> None:
    pool = exec_runner._WorkPool(2)
    peak = 0
    in_flight = 0
    guard = threading.Lock()

    def slow_factory() -> _FakeConn:
        nonlocal peak, in_flight
        with guard:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.2)  # 模拟 connect_timeout 期间
        with guard:
            in_flight -= 1
        return _FakeConn(None)

    results: list[Any] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait(timeout=5)
        results.append(pool.acquire(slow_factory))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(results) == 2
    assert peak == 2  # 两个建连并发进行（旧实现锁内 factory 会串行 → peak==1）
