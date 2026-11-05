"""修复回路契约测试（06-analysis 第一轮：P0/P1/S 系修复的回归锚点）。

- P0-2：SSE 帧 id 语义——落库事件（seq≥1）写 `id: {qid}:{seq}`，非落库事件
  （result 批量行等，seq=0）**省略 id 行**（前端对无 id 帧旁路幂等直接应用）；
- P0-3：route.* 值格式冻结 `{provider_id}:{model}`（数字 id 引用）——非法形态
  抛 BizError(E4420)，不冒泡裸 ValueError 被误映射 E2001；
- P1-5a：GET /tables/{id}/columns 形状（与表详情包络内 columns 同形）；
- P1-8：memory toggle 请求体/`enabled` 缺省 → 服务端读当前态取反；
- S-5：backup.dir 白名单收敛（越界 422；默认与部署根内路径放行）。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.errors import E_BAD_REQUEST, E_MODEL_DOWN, BizError
from app.store import datasources as store_datasources
from app.store import db as store_db
from app.store import memory as store_memory
from app.store import providers as store_providers
from app.store import settings as store_settings

# ---------- P0-2：SSE 帧 id 语义 ----------


def test_sse_frame_persisted_event_carries_id() -> None:
    from app.api import sse

    frame = sse._frame("q_abc", 3, "stage", {"type": "stage", "stage": "supply"})
    head, data_line = frame.split("\n", 1)
    assert head == "id: q_abc:3"  # 落库事件参与幂等/续播
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["type"] == "stage" and payload["stage"] == "supply"


def test_sse_frame_non_persisted_omits_id() -> None:
    """result 批量行（persist=False → seq=0）：帧不写 id，前端旁路幂等直接应用。"""
    from app.api import sse

    frame = sse._frame("q_abc", 0, "result", {"type": "result", "columns": [], "rows": []})
    assert not frame.startswith("id:")  # 旧写法 id=q:0 会被前端误丢
    assert "\nid:" not in frame
    payload = json.loads(frame.split("\n", 1)[0].removeprefix("data: "))
    assert payload["type"] == "result"


# ---------- P0-3：路由引用值格式冻结 ----------


def _seed_provider(conn: sqlite3.Connection, *, enabled: int = 1) -> int:
    return store_providers.save_provider(
        conn,
        name="ollama-local",
        type_="local",
        protocol="ollama",
        base_url="http://127.0.0.1:11434",
        api_key_enc=None,
        models=["qwen3-32b"],
        enabled=enabled,
    )


def test_resolve_route_accepts_numeric_id_ref(tmp_db: sqlite3.Connection) -> None:
    from app.llm import registry

    pid = _seed_provider(tmp_db)
    profile, model = registry.resolve_route(tmp_db, f"{pid}:qwen3-32b")
    assert profile.id == pid and model == "qwen3-32b"


def test_resolve_route_rejects_name_ref(tmp_db: sqlite3.Connection) -> None:
    """历史事故回归：前端按 provider 名保存（'ollama:qwen3-32b'）曾触发裸
    ValueError → 误报 E2001。冻结后：名称引用为非法形态，E4420 配置类错误。"""
    from app.llm import registry

    _seed_provider(tmp_db)
    with pytest.raises(BizError) as exc_info:
        registry.resolve_route(tmp_db, "ollama:qwen3-32b")
    assert exc_info.value.code == E_BAD_REQUEST  # E4420
    assert exc_info.value.http_status == 400


def test_resolve_route_rejects_malformed(tmp_db: sqlite3.Connection) -> None:
    from app.llm import registry

    _seed_provider(tmp_db)
    for bad in ("", "no-colon", "1:", ":model"):
        with pytest.raises(BizError) as exc_info:
            registry.resolve_route(tmp_db, bad)
        assert exc_info.value.code == E_BAD_REQUEST


def test_resolve_route_disabled_provider(tmp_db: sqlite3.Connection) -> None:
    from app.llm import registry

    pid = _seed_provider(tmp_db, enabled=0)
    with pytest.raises(BizError) as exc_info:
        registry.resolve_route(tmp_db, f"{pid}:qwen3-32b")
    assert exc_info.value.code == E_MODEL_DOWN


# ---------- P1-8：memory toggle 缺省取反 ----------


def test_memory_toggle_defaults_to_flip(tmp_db: sqlite3.Connection) -> None:
    mid = store_memory.save_memory(
        tmp_db, scope="global", datasource_id=None, topic="口径", content="收入=含税"
    )
    flipped = store_memory.toggle_memory(tmp_db, mid, None)  # 无 body 形态
    assert flipped.enabled == 0
    flipped_back = store_memory.toggle_memory(tmp_db, mid, None)
    assert flipped_back.enabled == 1


def test_memory_toggle_explicit_set_idempotent(tmp_db: sqlite3.Connection) -> None:
    mid = store_memory.save_memory(
        tmp_db, scope="global", datasource_id=None, topic="口径", content="收入=含税"
    )
    assert store_memory.toggle_memory(tmp_db, mid, False).enabled == 0
    assert store_memory.toggle_memory(tmp_db, mid, False).enabled == 0  # 幂等设定


# ---------- P1-5a：GET /tables/{id}/columns ----------


async def test_list_columns_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AITOSQL_DATA", str(tmp_path))
    store_db.run_migrations(tmp_path / "app.db")
    with store_db.get_conn() as conn:
        store_settings.ensure_defaults(conn)
        ds_id = store_datasources.save_datasource(
            conn,
            name="t",
            dialect="mysql",
            mode="online",
            host="127.0.0.1",
            port=3306,
            db_name="biz",
            username="u",
            password_enc=None,
        )
        table_id, _ = store_datasources.upsert_table_physical(
            conn, ds_id, name="orders", comment_db=None, row_estimate=100, field_count=1, partition_type=None
        )
        store_datasources.upsert_column_physical(
            conn, table_id, name="id", type_="bigint", comment_db=None, is_pk=1, is_partition=0, is_bucket=0
        )

    from app.api import tables as tables_api

    resp = await tables_api.list_columns(table_id)
    assert resp["code"] == 0
    assert [c["name"] for c in resp["data"]] == ["id"]
    assert "standard_name" in resp["data"][0]  # 与详情包络内 columns 同形
    with pytest.raises(BizError) as exc_info:
        await tables_api.list_columns(999999)
    assert exc_info.value.http_status == 404  # 表不存在 404 闸门


# ---------- S-5：backup.dir 白名单收敛 ----------


def _conn_at(tmp_path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setenv("AITOSQL_DATA", str(tmp_path))
    store_db.run_migrations(tmp_path / "app.db")
    conn = store_db.connect(tmp_path / "app.db")
    store_settings.ensure_defaults(conn)
    return conn


def test_backup_dir_default_within_root(tmp_path, monkeypatch) -> None:
    from app.store import backup as store_backup

    conn = _conn_at(tmp_path, monkeypatch)
    try:
        resolved = store_backup._resolve_backup_dir(conn)  # 默认 data/backups/ 放行
        assert resolved.is_relative_to(tmp_path.parent.resolve())
    finally:
        conn.close()


def test_backup_dir_escape_rejected(tmp_path, monkeypatch) -> None:
    from app.store import backup as store_backup

    conn = _conn_at(tmp_path, monkeypatch)
    try:
        store_settings.save_setting(conn, "backup.dir", "../../../evil-backups")
        with pytest.raises(BizError) as exc_info:
            store_backup._resolve_backup_dir(conn)
        assert exc_info.value.code == E_BAD_REQUEST
        assert exc_info.value.http_status == 422
    finally:
        conn.close()


def test_backup_dir_absolute_escape_rejected(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from app.store import backup as store_backup

    conn = _conn_at(tmp_path, monkeypatch)
    try:
        outside = str(Path(tmp_path).parent.parent.resolve())
        store_settings.save_setting(conn, "backup.dir", outside)
        with pytest.raises(BizError):
            store_backup._resolve_backup_dir(conn)
    finally:
        conn.close()


# ---------- F8：SSE 终态帧枚举契约（done.status ∈ done|failed；stopped 不走 done 帧） ----------


def _fake_ctx(events: list, persisted: list):
    from app.graph.context import NodeContext

    async def emit(type_, payload, persist=True):
        events.append((type_, payload))
        return len(events)

    async def persist_fn(**fields):
        persisted.append(fields)

    async def chat_fn(*a, **k):  # pragma: no cover - 本组用例不触发 LLM
        raise AssertionError("unused")

    return NodeContext(query_id="q_f8", emit=emit, chat_fn=chat_fn, persist_fn=persist_fn, settings={})


async def test_finalize_stopped_skips_done_frame(monkeypatch) -> None:
    """确认门取消：stopped 事件由 confirm_gate 节点发出（终态），_finalize 再发
    done{status:'stopped'} 越出冻结契约——前端曾因此把「已停止」误显「已失败」
    （且随 SSE 分块时序抛硬币）。冻结：stopped 终态不发 done 帧，行态仍落库。"""
    from app.graph import runner as graph_runner

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(graph_runner, "_discard_pending_and_start_followup", _noop)  # 隔离 P6 队列收尾
    events, persisted = [], []
    await graph_runner._finalize("q_f8", _fake_ctx(events, persisted), {"status": "stopped"})
    assert "done" not in [t for t, _ in events]
    assert persisted and persisted[0]["status"] == "stopped"


async def test_finalize_done_emits_done_frame(monkeypatch) -> None:
    """对照锚点：done/failed 终态帧枚举不变（前端契约仅这两种 status）。"""
    from app.graph import runner as graph_runner

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(graph_runner, "_post_done", _noop)  # 隔离标题/记忆副作用
    monkeypatch.setattr(graph_runner, "_discard_pending_and_start_followup", _noop)  # 隔离 P6 队列收尾
    events, persisted = [], []
    await graph_runner._finalize("q_f8", _fake_ctx(events, persisted), {"status": "done"})
    done_frames = [p for t, p in events if t == "done"]
    assert done_frames and done_frames[0]["status"] == "done"

    events2, _ = [], []
    await graph_runner._finalize("q_f8", _fake_ctx(events2, []), {"status": "failed"})
    failed_frames = [p for t, p in events2 if t == "done"]
    assert failed_frames and failed_frames[0]["status"] == "failed"


# ---------- P4：澄清门多选 + 自定义文本（借鉴 dsh-user-questions） ----------


def test_resume_in_effective_choice() -> None:
    """choice（单选兼容）/ choices（多选「、」连接）/ custom（补充叠加）合成口径文本。"""
    from app.api.schemas.queries import ResumeIn

    assert ResumeIn(gate="clarify", choice="按月").effective_choice() == "按月"
    assert ResumeIn(gate="clarify", choices=["按月", "含税"]).effective_choice() == "按月、含税"
    assert ResumeIn(gate="clarify", custom="不含退费").effective_choice() == "不含退费"
    assert (
        ResumeIn(gate="clarify", choices=["按月"], custom="不含退费").effective_choice()
        == "按月；补充：不含退费"
    )
    # 去重 + 空白剔除
    assert (
        ResumeIn(gate="clarify", choice="按月", choices=["按月", " 按天 "]).effective_choice()
        == "按月、按天"
    )
    assert ResumeIn(gate="clarify").effective_choice() == ""
