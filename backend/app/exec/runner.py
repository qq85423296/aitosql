"""执行运行器（design D-36 / SSOT §5.2 execute、§5.4-4/6/7）。

- 工作连接池：query.pool_size=2/数据源（线程安全队列）；管理连接随用随建随关、
  不经 validate/R15，供 KILL QUERY 寻址；
- 先 `SELECT CONNECTION_ID()` 记入 exec_meta 供 stop→KILL 寻址；
- 超时矩阵：query.timeout_s=30（语句）/ ch_client_timeout_s=35（CH 客户端）；
- CH readonly=1 强制（query.ch_readonly=true，D05）；CH 无 KILL 通道 = 客户端断开；
- 全部同步 IO 由调用侧（graph 节点）经 anyio.to_thread 承载。
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aitosql.exec.runner")


class EngineError(Exception):
    """引擎执行错误（消息在调用方脱敏——sqlcheck/clean.py 三出口统一）。"""


class ResourceLimitError(Exception):
    """Query exceeded configured result/time budget."""


class ConnectError(Exception):
    """数据源连接失败（host 不可达/超时/拒连；非 SQL 错误，不进修正回路，
    由 execute 节点映射 E1001 友好文案——实测占位库报 E5000 裸驱动元组）。"""


# F1 修复：执行中连接类失败同样归并 E1001 通道（LLM 无法修正断连，
# 进修正回路只会白烧轮次后以 E2004 收场，且全程误导性「修正中」）。
# pymysql 连接类 errno：2002 socket 拒连 / 2003 不可达 / 2006 gone away /
# 2013 lost during query（含读写超时包装）/ 2055 lost at packet。
_CONNECT_ERRNOS = frozenset({2002, 2003, 2006, 2013, 2055})


def _is_connect_failure(exc: BaseException) -> bool:
    """执行中连接类失败判定（不 import 驱动，保持惰性——按异常族与模块名识别）。

    覆盖：OSError 全族（ConnectionError/TimeoutError/socket.gaierror/ssl.SSLError，
    执行上下文中 OSError 皆网络）；pymysql OperationalError 的连接 errno；
    clickhouse-connect 客户端层 OperationalError（网络/超时包装；服务端错误为
    DatabaseError，不在此列，仍走 EngineError 修正回路）。
    """
    if isinstance(exc, OSError):
        return True
    cls = type(exc)
    name, mod = cls.__name__, (cls.__module__ or "")
    if name == "OperationalError" and mod.startswith("pymysql"):
        errno = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        return errno in _CONNECT_ERRNOS
    if name == "OperationalError" and mod.startswith("psycopg"):
        return True  # psycopg OperationalError = 连接类失败（SQL 错误在 psycopg.errors.*）
    return name == "OperationalError" and mod.startswith("clickhouse_connect")


@dataclass
class ExecOutcome:
    """execute 产物：exec_meta 五字段 + 结果集（预览/落盘由 meta.py 消费）。"""

    columns: list[dict[str, str]]
    rows: list[tuple]
    exec_meta: dict[str, Any] = field(default_factory=dict)


# ---------- 连接工厂（同步驱动，惰性 import） ----------


def _connect_pymysql(ds: dict[str, Any], password: str | None, *, timeout_s: int) -> Any:
    import pymysql

    default_port = 9030 if ds.get("dialect") == "starrocks" else 3306
    return pymysql.connect(
        host=ds.get("host") or "127.0.0.1",
        port=int(ds.get("port") or default_port),
        user=ds.get("username") or "",
        password=password or "",
        database=ds.get("db_name") or None,
        connect_timeout=max(1, min(10, timeout_s)),
        read_timeout=timeout_s,
        write_timeout=timeout_s,
        charset="utf8mb4",
        autocommit=True,
    )


def _connect_postgres(ds: dict[str, Any], password: str | None, *, timeout_s: int) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - 环境缺驱动时的明确指引
        raise ConnectError("未安装 PostgreSQL 驱动：pip install 'psycopg[binary]'") from exc
    return psycopg.connect(
        host=ds.get("host") or "127.0.0.1",
        port=int(ds.get("port") or 5432),
        user=ds.get("username") or "postgres",
        password=password or "",
        dbname=ds.get("db_name") or "postgres",
        connect_timeout=max(1, min(10, timeout_s)),
        options=f"-c statement_timeout={max(1, timeout_s) * 1000}",
        autocommit=True,
    )


def _connect_hive(ds: dict[str, Any], password: str | None, *, timeout_s: int) -> Any:
    try:
        from pyhive import hive as pyhive_hive
    except ImportError as exc:  # pragma: no cover
        raise ConnectError("未安装 Hive 驱动：pip install pyhive thrift thrift_sasl") from exc
    auth = "NONE"
    if password:
        # LDAP 认证需 puresasl（纯 Python）；缺失时明确报错而非静默挂起
        try:
            import puresasl  # noqa: F401
            auth = "LDAP"
        except ImportError as exc:
            raise ConnectError(
                "Hive LDAP 认证需 puresasl（pip install puresasl）；无认证集群请留空密码"
            ) from exc
    conn = pyhive_hive.connect(
        host=ds.get("host") or "127.0.0.1",
        port=int(ds.get("port") or 10000),
        username=ds.get("username") or None,
        password=password or None,
        database=ds.get("db_name") or "default",
        auth=auth,
    )
    # 语句级超时：Hive 无服务端 statement_timeout，客户端 socket 超时收口
    with contextlib.suppress(Exception):
        conn._transport._socket.setTimeout(timeout_s * 1000)  # thrift TSocket(ms)
    return conn


def _connect_clickhouse(ds: dict[str, Any], password: str | None, *, timeout_s: int, readonly: bool) -> Any:
    import clickhouse_connect

    settings: dict[str, Any] = {}
    if readonly:
        settings["readonly"] = 1  # CH readonly=1 强制（D05）
    return clickhouse_connect.get_client(
        host=ds.get("host") or "127.0.0.1",
        port=int(ds.get("port") or 8123),
        username=ds.get("username") or "default",
        password=password or "",
        database=ds.get("db_name") or None,
        send_receive_timeout=timeout_s,
        connect_timeout=max(1, min(10, timeout_s)),
        settings=settings,
    )


def connect_engine(
    ds: dict[str, Any], password: str | None, *, timeout_s: int = 30, readonly: bool = False
) -> Any:
    """按方言建连接（ds = datasource 行 dict，password 已解密，仅内存）。"""
    dialect = ds["dialect"]
    if dialect in ("mysql", "doris", "starrocks"):
        return _connect_pymysql(ds, password, timeout_s=timeout_s)
    if dialect == "postgresql":
        return _connect_postgres(ds, password, timeout_s=timeout_s)
    if dialect == "hive":
        return _connect_hive(ds, password, timeout_s=timeout_s)
    return _connect_clickhouse(ds, password, timeout_s=timeout_s, readonly=readonly)


def engine_query(conn: Any, dialect: str, sql: str, params: Any = None) -> list[tuple]:
    """统一查询执行：返回行元组列表（列名经 cursor.description / result.column_names）。"""
    if dialect in ("mysql", "doris", "starrocks", "postgresql", "hive"):
        with conn.cursor() as cur:
            cur.execute(sql, params) if params else cur.execute(sql)
            return list(cur.fetchall())
    result = conn.query(sql, parameters=params)
    return [tuple(r) for r in result.result_rows]


def engine_query_with_columns(
    conn: Any,
    dialect: str,
    sql: str,
    *,
    max_rows: int = 10000,
    max_bytes: int = 16 * 1024 * 1024,
    deadline: float | None = None,
) -> tuple[list[str], list[tuple]]:
    import json

    rows: list[tuple] = []
    used = 0

    def collect(batch: Iterable[tuple]) -> None:
        nonlocal used
        for row in batch:
            if deadline is not None and time.monotonic() >= deadline:
                raise ResourceLimitError("Result deadline exceeded")
            used += len(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
            if len(rows) >= max_rows or used > max_bytes:
                raise ResourceLimitError("Result row/byte budget exceeded")
            rows.append(tuple(row))

    if dialect in ("mysql", "doris", "starrocks"):
        import pymysql

        with conn.cursor(pymysql.cursors.SSCursor) as cur:
            try:
                cur.execute(sql)
                columns = [d[0] for d in cur.description or []]
                while batch := cur.fetchmany(128):
                    collect(batch)
            except BaseException:
                _close_quietly(conn)
                raise
        return columns, rows
    if dialect in ("postgresql", "hive"):
        # psycopg3 命名游标 = 服务端游标（autocommit 下可用）；hive 普通游标分批拉取
        cur_cm = (
            conn.cursor(name=f"aitosql_{uuid.uuid4().hex[:12]}", withhold=False)
            if dialect == "postgresql"
            else conn.cursor()
        )
        cur = cur_cm
        try:
            if dialect == "postgresql":
                cur.itersize = 128
            cur.execute(sql)
            columns = [d.name if hasattr(d, "name") else d[0] for d in cur.description or []]
            while batch := cur.fetchmany(128):
                collect(batch)
        except BaseException:
            _close_quietly(conn)
            raise
        finally:
            _close_quietly(cur)
        return columns, rows
    seconds = max(1, int((deadline or time.monotonic() + 30) - time.monotonic()))
    with conn.query_rows_stream(
        sql,
        settings={
            "max_execution_time": seconds,
            "max_result_rows": max_rows,
            "max_result_bytes": max_bytes,
            "result_overflow_mode": "throw",
        },
    ) as stream:
        columns = list(stream.source.column_names)
        for row in stream:
            collect([row])
    return columns, rows


def connection_id(conn: Any, dialect: str) -> str | None:
    """连接寻址 ID 采集（KILL/取消寻址；CH/Hive 无此概念，用客户端占位注明）。"""
    if dialect == "clickhouse":
        return f"ch-client:{uuid.uuid4()}"
    if dialect == "hive":
        return f"hive-client:{uuid.uuid4()}"
    try:
        probe = "SELECT pg_backend_pid()" if dialect == "postgresql" else "SELECT CONNECTION_ID()"
        rows = engine_query(conn, dialect, probe)
        return str(rows[0][0]) if rows and rows[0] else None
    except Exception as exc:
        # F4：静默 None 会让 stop 退化为 no_connection_id 且排障无迹——留告警
        logger.warning("CONNECTION_ID 采集失败（stop 将无 KILL 能力）: %s: %s", type(exc).__name__, exc)
        return None


# ---------- 工作连接池（pool_size=2/数据源；F-4 治理：并发上限 + 失效重连 + 变更失效） ----------


class _WorkPool:
    def __init__(self, size: int) -> None:
        self.size = max(size, 1)
        self._sem = threading.BoundedSemaphore(self.size)
        self._idle: queue.Queue[Any] = queue.Queue(maxsize=self.size)
        self._closed = False
        self._lock = threading.Lock()

    def acquire(
        self,
        factory: Callable[[], Any],
        check: Callable[[Any], bool] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        deadline = time.monotonic() + max(0.0, timeout)
        if not self._sem.acquire(timeout=max(0.0, timeout)):
            raise TimeoutError("Connection pool wait deadline exceeded")
        try:
            while True:
                if self._closed or time.monotonic() >= deadline:
                    raise TimeoutError("Pool closed or acquisition deadline exceeded")
                try:
                    conn = self._idle.get_nowait()
                except queue.Empty:
                    conn = factory()
                    if self._closed or time.monotonic() >= deadline:
                        _close_quietly(conn)
                        raise TimeoutError("Connection acquisition deadline exceeded") from None
                    return conn
                if check is None or check(conn):
                    return conn
                _close_quietly(conn)
        except BaseException:
            self._sem.release()
            raise

    def release(self, conn: Any, *, broken: bool = False) -> None:
        try:
            with self._lock:
                if broken or self._closed:
                    _close_quietly(conn)
                else:
                    self._idle.put_nowait(conn)
        except queue.Full:
            _close_quietly(conn)
        finally:
            self._sem.release()

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
            while True:
                try:
                    _close_quietly(self._idle.get_nowait())
                except queue.Empty:
                    return


_POOLS: dict[int, _WorkPool] = {}
_POOLS_LOCK = threading.Lock()
_RUNNING: dict[str, dict[str, Any]] = {}  # query_id -> {ds, dialect, connection_id, conn}


def _close_quietly(conn: Any) -> None:
    with contextlib.suppress(Exception):
        conn.close()


def _pool_for(ds_id: int, pool_size: int) -> _WorkPool:
    with _POOLS_LOCK:
        old = _POOLS.get(ds_id)
        if old is not None and old.size != max(pool_size, 1):
            old.close_all()
            _POOLS.pop(ds_id)
        if ds_id not in _POOLS:
            _POOLS[ds_id] = _WorkPool(pool_size)
        return _POOLS[ds_id]


def invalidate_pool(ds_id: int) -> None:
    """数据源 PATCH/DELETE 时调用：关闭该源全部空闲池连接（防旧连接混用，F-4）。"""
    with _POOLS_LOCK:
        pool = _POOLS.pop(ds_id, None)
    if pool is not None:
        pool.close_all()


def _ping(conn: Any, dialect: str) -> bool:
    """复用连接活性探测（SELECT 1；失败由调用方重建）。"""
    try:
        engine_query(conn, dialect, "SELECT 1")
        return True
    except Exception:
        return False


# ---------- 主链路 ----------


def run_query(
    sql: str,
    *,
    query_id: str,
    ds: dict[str, Any],
    password: str | None,
    timeout_s: int = 30,
    ch_client_timeout_s: int = 35,
    ch_readonly: bool = True,
    pool_size: int = 2,
    max_rows: int = 10000,
    max_bytes: int = 16 * 1024 * 1024,
) -> ExecOutcome:
    """执行终串（所验即所执；同步，调用侧 to_thread）。

    超时矩阵：mysql/doris 语句 timeout_s=30；CH 客户端 ch_client_timeout_s=35。
    """
    dialect = ds["dialect"]
    deadline = time.monotonic() + max(1, timeout_s)
    effective_timeout = ch_client_timeout_s if dialect == "clickhouse" else timeout_s
    pool = _pool_for(ds["id"], pool_size)

    def factory() -> Any:
        return connect_engine(
            ds,
            password,
            timeout_s=max(1, min(effective_timeout, int(deadline - time.monotonic()))),
            readonly=(dialect == "clickhouse"),
        )

    try:
        conn = pool.acquire(
            factory, check=lambda c: _ping(c, dialect), timeout=max(0.0, deadline - time.monotonic())
        )
    except Exception as exc:
        # 建连/活性探测失败发生在执行 try 块之外：翻译为 ConnectError，
        # 避免裸驱动异常逃逸成 E5000（2026-10-17 实测）
        raise ConnectError(str(exc)) from exc
    conn_id: str | None = None
    started = time.monotonic()
    watchdog: threading.Timer | None = None
    broken = False  # F17：执行期抛错 → 连接不再回池
    try:
        conn_id = connection_id(conn, dialect)
        _RUNNING[query_id] = {"ds": ds, "dialect": dialect, "connection_id": conn_id, "conn": conn}
        watchdog = threading.Timer(
            max(0.01, deadline - time.monotonic()), lambda: kill_query(query_id, password=password)
        )
        watchdog.daemon = True
        watchdog.start()
        columns, rows = engine_query_with_columns(
            conn, dialect, sql, max_rows=max_rows, max_bytes=max_bytes, deadline=deadline
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        return ExecOutcome(
            columns=[{"name": c, "type": ""} for c in columns],
            rows=rows,
            exec_meta={"rows": len(rows), "duration_ms": duration_ms, "connection_id": conn_id},
        )
    except (EngineError, ResourceLimitError):
        broken = True
        raise
    except ConnectError:
        broken = True
        raise
    except Exception as exc:
        broken = True
        # F1：执行中连接类失败（断线 2006/2013、读写超时、socket 错误）归并
        # ConnectError → E1001 通道（不进修正回路）；其余维持 EngineError。
        if _is_connect_failure(exc):
            raise ConnectError(str(exc)) from exc
        raise EngineError(str(exc)) from exc
    finally:
        if watchdog is not None:
            watchdog.cancel()
        _RUNNING.pop(query_id, None)
        pool.release(conn, broken=broken)


def kill_query(query_id: str, *, password: str | None = None) -> dict[str, Any]:
    """stop → 管理连接 KILL（随用随建随关，不经 validate/R15）。

    MySQL `KILL QUERY {id}` / Doris `KILL {id}`；CH readonly=1 无 KILL 通道 =
    客户端断开（服务端查询可能继续至 profile 上限，UI 注明，SSOT §5.4-4）。
    """
    running = _RUNNING.get(query_id)
    if running is None:
        return {"killed": False, "reason": "not_running"}
    dialect = running["dialect"]
    if dialect in ("clickhouse", "hive"):
        _close_quietly(running["conn"])  # 客户端断开（两方言无服务端 KILL 通道）
        return {
            "killed": False,
            "reason": f"{dialect}_no_kill_channel",
            "note": f"{dialect} 无 KILL 通道，已断开客户端连接，服务端可能继续执行",
        }
    ds = running["ds"]
    conn_id = running["connection_id"]
    if not conn_id:
        return {"killed": False, "reason": "no_connection_id"}
    admin = None
    try:
        admin = connect_engine(ds, password, timeout_s=10)
        if dialect == "postgresql":
            rows = engine_query(admin, dialect, "SELECT pg_cancel_backend(%s)", (int(conn_id),))
            cancelled = bool(rows and rows[0] and rows[0][0])
            return {"killed": cancelled, "connection_id": conn_id}
        kill_sql = f"KILL QUERY {conn_id}" if dialect == "mysql" else f"KILL {conn_id}"
        with admin.cursor() as cur:
            cur.execute(kill_sql)
        return {"killed": True, "connection_id": conn_id}
    except Exception as exc:
        logger.warning("KILL 失败: %s", type(exc).__name__)
        return {"killed": False, "reason": type(exc).__name__}
    finally:
        if admin is not None:
            _close_quietly(admin)
