"""_make_chat 重试事件回归测试（修复回路 A2：_on_retry 去重后行为锁定）。

锁定两条重试路径的事件契约，防止「删除重复定义」式修复破坏重试轨迹：
- EMPTY_RESPONSE（思考吃满输出预算）：max_tokens 自动翻倍重试，落 llm_retry(attempt=0) 事件；
- 普通可重试失败（TIMEOUT）：走 chat_with_retry 策略退避，落 llm_retry(attempt≥1) 事件；
- query_id=None（会话压缩等无查询场景）：重试照常但静默不落事件。

隔离说明：同 test_inbox.py——tmp_env 把 AITOSQL_DATA 指到 tmp_path，
get_conn(None) 落到临时库，绝不碰真实数据目录；适配器/路由解析全部 mock，
不发起任何真实模型调用。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.errors import E_MODEL_DOWN, BizError
from app.graph import runner as graph_runner
from app.ids import new_query_id
from app.llm import retry as llm_retry
from app.store import datasources as store_datasources
from app.store import db as store_db
from app.store import queries as store_queries
from app.store import sessions as store_sessions
from app.store import settings as store_settings

ROUTE_REF = "1:fake-model"


@pytest.fixture()
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """路径隔离的临时数据目录（get_conn() 无参也指向它）。"""
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


def _mk_query(conn: sqlite3.Connection) -> str:
    ds_id = store_datasources.save_datasource(
        conn,
        name="ds_chat",
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
    conn.commit()  # 释放写事务：事件落库走独立连接（WAL 单写者）
    return qid


class _FakeAdapter:
    """按脚本失败/成功的假适配器；记录每次调用的 max_tokens。"""

    def __init__(self, failures: list[BizError]) -> None:
        self._failures = list(failures)
        self.calls: list[int] = []

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4096,
        on_delta: Any = None,
        timeout_s: float = 900,
    ) -> Any:
        self.calls.append(max_tokens)
        if self._failures:
            raise self._failures.pop(0)
        return type("Outcome", (), {"text": "SELECT 1", "tokens": 3})()


def _err(failure_class: str) -> BizError:
    return BizError(E_MODEL_DOWN, "模型端点无响应", 502, data={"failure_class": failure_class})


def _patch_route(monkeypatch: pytest.MonkeyPatch, adapter: _FakeAdapter) -> None:
    from app.llm import providers as llm_providers
    from app.llm import registry as llm_registry

    monkeypatch.setattr(llm_registry, "resolve_route", lambda conn, ref: (object(), "fake-model"))
    monkeypatch.setattr(llm_providers, "get_adapter", lambda profile: adapter)


def _retry_events(conn: sqlite3.Connection, qid: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT payload FROM query_event WHERE query_id = ? AND type = 'llm_retry' ORDER BY seq",
        (qid,),
    ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


async def test_empty_response_bumps_budget_and_emits(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EMPTY_RESPONSE：预算 100→200 自动翻倍，落 attempt=0/delay_ms=0 扩预算事件。"""
    qid = _mk_query(tmp_env)
    adapter = _FakeAdapter([_err("EMPTY_RESPONSE")])
    _patch_route(monkeypatch, adapter)

    chat_fn = graph_runner._make_chat(None, query_id=qid)
    out = await chat_fn(ROUTE_REF, [{"role": "user", "content": "q"}], 100)

    assert out.text == "SELECT 1"
    assert adapter.calls == [100, 200]  # 扩预算后重试成功
    events = _retry_events(tmp_env, qid)
    assert len(events) == 1
    assert events[0]["failure_class"] == "EMPTY_RESPONSE"
    assert events[0]["attempt"] == 0 and events[0]["delay_ms"] == 0  # 扩预算重试的固定标记
    assert events[0]["route"] == ROUTE_REF


async def test_normal_retry_emits_before_sleep(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TIMEOUT：走策略退避重试，先落 llm_retry(attempt=1) 事件后等待。"""
    qid = _mk_query(tmp_env)
    adapter = _FakeAdapter([_err("TIMEOUT")])
    _patch_route(monkeypatch, adapter)

    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(llm_retry.anyio, "sleep", fake_sleep)

    chat_fn = graph_runner._make_chat(None, query_id=qid)
    out = await chat_fn(ROUTE_REF, [{"role": "user", "content": "q"}], 100)

    assert out.text == "SELECT 1"
    assert adapter.calls == [100, 100]  # 普通重试不改预算
    events = _retry_events(tmp_env, qid)
    assert len(events) == 1
    assert events[0]["failure_class"] == "TIMEOUT"
    assert events[0]["attempt"] == 1 and events[0]["delay_ms"] > 0
    assert len(sleeps) == 1  # 先落事件后等待：事件已持久化才发生 sleep


async def test_retry_without_query_id_stays_silent(
    tmp_env: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """query_id=None（压缩路由）：重试正常进行但不落事件、不抛异常。"""
    adapter = _FakeAdapter([_err("TIMEOUT")])
    _patch_route(monkeypatch, adapter)

    async def fake_sleep(s: float) -> None:
        return None

    monkeypatch.setattr(llm_retry.anyio, "sleep", fake_sleep)

    chat_fn = graph_runner._make_chat(None)  # 无 query_id：会话压缩摘要路径
    out = await chat_fn(ROUTE_REF, [{"role": "user", "content": "q"}], 100)

    assert out.text == "SELECT 1"
    assert adapter.calls == [100, 100]
    n = tmp_env.execute("SELECT COUNT(*) FROM query_event").fetchone()[0]
    assert n == 0  # 静默：无查询归属的事件不落库
