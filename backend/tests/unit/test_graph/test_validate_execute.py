"""B1 查询与 SQL 安全回归：validate 执行序铁律 + 确认门 SQL 绑定 + 所验即所执。

锁定 SSOT D-03 执行序（清洗 → R15 → auto_limit AST 注入）与三条铁律：
- R15 硬阻断命中：不得进入 LLM 修正、不得触碰数据库执行；
- 注入阶段自身失败：归并 R15-B2 硬阻断（fail-closed），不以裸异常冒泡；
- 确认门/执行只消费 validate 终串：批准绑定的 SQL 发生任何变化即拒绝执行。

隔离说明：tmp_env 把 AITOSQL_DATA 指到 tmp_path；引擎调用全部 mock，
不连接任何真实数据库；chat_fn 以 AssertionError 武装，LLM 被调用即测试失败。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.errors import E_CONFLICT, BizError
from app.exec import runner as exec_runner
from app.graph import nodes as graph_nodes
from app.graph.context import NodeContext
from app.ids import new_query_id
from app.store import datasources as store_datasources
from app.store import db as store_db
from app.store import queries as store_queries
from app.store import sessions as store_sessions
from app.store import settings as store_settings


@pytest.fixture()
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AITOSQL_DATA", str(data))
    db_path = data / "app.db"
    store_db.run_migrations(db_path)
    conn = store_db.connect(db_path)
    store_settings.ensure_defaults(conn)
    conn.commit()
    yield conn
    conn.close()


def _mk_query(conn: sqlite3.Connection) -> tuple[int, str]:
    ds_id = store_datasources.save_datasource(
        conn,
        name="ds_b1",
        dialect="mysql",
        mode="online",
        host="127.0.0.1",
        port=3306,
        db_name="d",
        username="u",
        password_enc=None,
    )
    session = store_sessions.save_session(conn, datasource_id=ds_id)
    qid = new_query_id()
    store_queries.save_query(
        conn, query_id=qid, session_id=session.id, datasource_id=ds_id,
        dialect="mysql", question="查收入",
    )
    conn.commit()
    return ds_id, qid


class _Recorder:
    """事件/持久化记录器；chat_fn 武装为断言（LLM 被调用即失败）。"""

    def __init__(self, qid: str, ds_id: int, settings: dict[str, Any] | None = None) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.persisted: list[dict[str, Any]] = []
        self.ctx = NodeContext(
            query_id=qid,
            emit=self._emit,
            chat_fn=self._chat_fn,
            persist_fn=self._persist,
            settings=settings or {},
            datasource={"id": ds_id, "dialect": "mysql", "mode": "online", "db_name": "d"},
            password=None,
            db_path=None,
        )

    async def _emit(self, event_type: str, payload: dict[str, Any], persist: bool = True) -> int:
        self.events.append((event_type, payload))
        return len(self.events)

    async def _chat_fn(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("R15/门控路径不得调用 LLM")

    async def _persist(self, **fields: Any) -> None:
        self.persisted.append(fields)

    def payloads(self, event_type: str) -> list[dict[str, Any]]:
        return [p for t, p in self.events if t == event_type]


# ---------- validate：执行序与 R15 fail-closed ----------


async def test_validate_r15_hit_skips_inject_llm_and_engine(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R15 命中（多语句）：inject_auto_limit/LLM/执行全部不得触及，failed 交付。"""
    ds_id, qid = _mk_query(tmp_env)
    rec = _Recorder(qid, ds_id)

    def _forbidden_inject(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("R15 命中后不得进入 LIMIT AST 注入")

    monkeypatch.setattr(graph_nodes.sql_rules, "inject_auto_limit", _forbidden_inject)
    state = {"sql": "SELECT 1; DROP TABLE orders", "dialect": "mysql", "ds_mode": "online", "round": 0}
    out = await graph_nodes.validate(state, ctx=rec.ctx)

    assert out["hard_blocked"] is True and out["status"] == "failed"
    assert out["risks"][0]["rule_id"] == "R15"
    errors = rec.payloads("error")
    assert errors and errors[0]["code"] == "E3010" and errors[0]["retryable"] is False
    assert any(p.get("status") == "failed" for p in rec.persisted)


@pytest.mark.parametrize("sql", ["", "   ", "  ;  ", "-- 仅注释\n"])
async def test_validate_empty_sql_hard_blocked(
    tmp_env: sqlite3.Connection, sql: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空 SQL/纯分隔符/纯注释：R15 fail-closed（B1 族），不得进入后续阶段。"""
    ds_id, qid = _mk_query(tmp_env)
    rec = _Recorder(qid, ds_id)

    def _forbidden_inject(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("空 SQL 不得进入 LIMIT AST 注入")

    monkeypatch.setattr(graph_nodes.sql_rules, "inject_auto_limit", _forbidden_inject)
    out = await graph_nodes.validate(
        {"sql": sql, "dialect": "mysql", "ds_mode": "online", "round": 0}, ctx=rec.ctx
    )
    assert out["hard_blocked"] is True and out["status"] == "failed"
    assert rec.payloads("error")[0]["code"] == "E3010"


async def test_validate_inject_failure_maps_to_b2(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """注入阶段自身失败：归并 R15-B2 硬阻断（fail-closed），不以裸异常冒泡为 E2001。"""
    ds_id, qid = _mk_query(tmp_env)
    rec = _Recorder(qid, ds_id)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("AST 注入内部解析失败")

    monkeypatch.setattr(graph_nodes.sql_rules, "inject_auto_limit", _boom)
    out = await graph_nodes.validate(
        {"sql": "SELECT amount FROM orders", "dialect": "mysql", "ds_mode": "online", "round": 0},
        ctx=rec.ctx,
    )
    assert out["hard_blocked"] is True and out["status"] == "failed"
    assert out["risks"][0]["rule_id"] == "R15"
    assert rec.payloads("error")[0]["code"] == "E3010"


async def test_validate_order_clean_r15_inject(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """执行序铁律：unicode_clean → hard_block_r15 → inject_auto_limit（离线免干跑）。"""
    ds_id, qid = _mk_query(tmp_env)
    rec = _Recorder(qid, ds_id, settings={"query.auto_limit": 1000})
    calls: list[str] = []

    real_clean = graph_nodes.sql_clean.unicode_clean
    real_r15 = graph_nodes.sql_rules.hard_block_r15
    real_inject = graph_nodes.sql_rules.inject_auto_limit

    def spy_clean(sql: str) -> str:
        calls.append("clean")
        return real_clean(sql)

    def spy_r15(sql: str, *, dialect: str) -> Any:
        calls.append("r15")
        return real_r15(sql, dialect=dialect)

    def spy_inject(sql: str, *, dialect: str, limit: int) -> str:
        calls.append("inject")
        return real_inject(sql, dialect=dialect, limit=limit)

    monkeypatch.setattr(graph_nodes.sql_clean, "unicode_clean", spy_clean)
    monkeypatch.setattr(graph_nodes.sql_rules, "hard_block_r15", spy_r15)
    monkeypatch.setattr(graph_nodes.sql_rules, "inject_auto_limit", spy_inject)

    out = await graph_nodes.validate(
        {"sql": "SELECT amount FROM orders", "dialect": "mysql", "ds_mode": "offline", "round": 0},
        ctx=rec.ctx,
    )
    assert calls == ["clean", "r15", "inject"]
    assert "hard_blocked" not in out
    assert "LIMIT" in out["sql"].upper()  # auto_limit 已注入终串


# ---------- confirm_gate / execute：所验即所执 ----------


async def test_confirm_gate_binds_exact_sql_and_tamper_rejected(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """批准绑定确认时点的精确 SQL；执行串发生任何变化（含尾随空格）即拒绝执行。"""
    ds_id, qid = _mk_query(tmp_env)
    rec = _Recorder(qid, ds_id, settings={"query.write_confirm": True})
    approved_sql = "UPDATE orders SET status = 'paid' WHERE id = 1"

    monkeypatch.setattr(graph_nodes, "interrupt", lambda payload: {"approved": True})
    gate = await graph_nodes.confirm_gate(
        {"sql": approved_sql, "dialect": "mysql", "round": 0, "risks": []}, ctx=rec.ctx
    )
    assert gate["write_confirmed"] is True and gate["gate_decision"] == "approved"

    run_query_calls: list[str] = []
    monkeypatch.setattr(
        exec_runner, "run_query", lambda sql, **kw: run_query_calls.append(sql) or None
    )
    tampered = approved_sql + " "  # 任何字节级变化都算另一语句
    with pytest.raises(BizError) as exc_info:
        await graph_nodes.execute(
            {"sql": tampered, "dialect": "mysql", "ds_mode": "online", "datasource_id": ds_id, "round": 0},
            ctx=rec.ctx,
        )
    assert exc_info.value.code == E_CONFLICT
    assert run_query_calls == []  # 拒绝发生在触库之前


async def test_execute_consumes_validated_sql_verbatim(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """执行只消费 validate 终串：交给引擎的 SQL 与 state['sql'] 逐字节一致。"""
    ds_id, qid = _mk_query(tmp_env)
    rec = _Recorder(qid, ds_id, settings={"query.timeout_s": 5})
    final_sql = "SELECT amount FROM orders LIMIT 1000"  # validate 产出终串（已注入 LIMIT）
    captured: list[str] = []

    def fake_run_query(sql: str, **kw: Any) -> exec_runner.ExecOutcome:
        captured.append(sql)
        return exec_runner.ExecOutcome(
            columns=[{"name": "amount", "type": ""}], rows=[(1,)],
            exec_meta={"rows": 1, "duration_ms": 1},
        )

    def _no_extra_connect(*args: Any, **kwargs: Any) -> Any:
        raise exec_runner.ConnectError("测试环境无引擎（附加采集降级）")

    monkeypatch.setattr(exec_runner, "run_query", fake_run_query)
    # 附加 data_max_time 采集的二次建连直接失败降级，不触真实引擎
    monkeypatch.setattr(exec_runner, "connect_engine", _no_extra_connect)

    out = await graph_nodes.execute(
        {"sql": final_sql, "dialect": "mysql", "ds_mode": "online", "datasource_id": ds_id, "round": 0},
        ctx=rec.ctx,
    )
    assert captured == [final_sql]  # 所验即所执：无确认后隐式重写
    assert out["exec_meta"]["rows"] == 1


async def test_execute_connect_error_never_enters_correction(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连接故障映射 E1001 终态：不进 SQL 自动修正回路（LLM 修不了断连）。"""
    ds_id, qid = _mk_query(tmp_env)
    rec = _Recorder(qid, ds_id, settings={"query.timeout_s": 5})

    def _down(sql: str, **kw: Any) -> Any:
        raise exec_runner.ConnectError("connect timed out")

    monkeypatch.setattr(exec_runner, "run_query", _down)
    out = await graph_nodes.execute(
        {"sql": "SELECT 1 LIMIT 1", "dialect": "mysql", "ds_mode": "online",
         "datasource_id": ds_id, "round": 0},
        ctx=rec.ctx,
    )
    assert out["status"] == "failed"
    assert "needs_correct" not in out  # 连接故障不得进修正回路
    errors = rec.payloads("error")
    assert errors and errors[0]["code"] == "E1001"


async def test_execute_write_unapproved_never_runs(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写确认开启且未批准：执行栅栏在触库前拒绝（E4090），引擎零调用。"""
    ds_id, qid = _mk_query(tmp_env)
    rec = _Recorder(qid, ds_id, settings={"query.write_confirm": True})
    run_query_calls: list[str] = []
    monkeypatch.setattr(
        exec_runner, "run_query", lambda sql, **kw: run_query_calls.append(sql) or None
    )
    with pytest.raises(BizError) as exc_info:
        await graph_nodes.execute(
            {"sql": "UPDATE orders SET status = 'x'", "dialect": "mysql", "ds_mode": "online",
             "datasource_id": ds_id, "round": 0},
            ctx=rec.ctx,
        )
    assert exc_info.value.code == E_CONFLICT
    assert run_query_calls == []


async def test_confirm_gate_cancel_stops_without_approval(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消：status=stopped、无批准记录，后续执行必被栅栏拒绝。"""
    ds_id, qid = _mk_query(tmp_env)
    rec = _Recorder(qid, ds_id, settings={"query.write_confirm": True})
    sql = "DELETE FROM orders WHERE id = 1"

    monkeypatch.setattr(graph_nodes, "interrupt", lambda payload: {"approved": False})
    gate = await graph_nodes.confirm_gate(
        {"sql": sql, "dialect": "mysql", "round": 0, "risks": []}, ctx=rec.ctx
    )
    assert gate["gate_decision"] == "cancelled" and gate["write_confirmed"] is False
    assert any(p.get("status") == "stopped" for p in rec.persisted)

    monkeypatch.setattr(
        exec_runner, "run_query", lambda s, **kw: pytest.fail("取消后不得触库")
    )
    with pytest.raises(BizError):
        await graph_nodes.execute(
            {"sql": sql, "dialect": "mysql", "ds_mode": "online", "datasource_id": ds_id, "round": 0},
            ctx=rec.ctx,
        )
