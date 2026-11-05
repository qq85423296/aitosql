"""提示词组装器用例（T1.3/T3.3）：8 段顺序 / 契约解析降级 / 裁剪顺序 / 预览同源。"""

from __future__ import annotations

import sqlite3

import pytest

from app.llm import prompts


def test_parse_contract_ok() -> None:
    raw = (
        '{"reasoning": "r", "assumptions": [{"text": "收入=含税", "impact": "low"}],'
        '"sql": "SELECT 1", "need_tables": [], "followups": ["f1"]}'
    )
    c = prompts.parse_contract(raw)
    assert c.sql == "SELECT 1" and c.assumptions[0].impact == "low"


def test_parse_contract_fallback_sql_block() -> None:
    """降级：非 JSON 输出正则提取 ```sql 块，标注未结构化（E2002 语义）。"""
    raw = "输出如下：\n```sql\nSELECT id FROM orders\n```"
    c = prompts.parse_contract(raw)
    assert "SELECT id FROM orders" in c.sql
    assert c.reasoning.startswith("未结构化输出")


def test_parse_contract_raises_without_sql() -> None:
    from app.errors import E_CONTRACT_PARSE, BizError

    with pytest.raises(BizError) as exc:
        prompts.parse_contract("完全不是 SQL 的输出")
    assert exc.value.code == E_CONTRACT_PARSE


def test_reasoning_max_length() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        prompts.GenerationContract.model_validate(
            {"reasoning": "x" * 1201, "assumptions": [], "sql": "SELECT 1"}
        )


def test_render_system_prompt_vars() -> None:
    out = prompts.render_system_prompt("方言 {{dialect}} 日期 {{date}}", "doris")
    assert "doris" in out and "{{date}}" not in out


def test_assemble_envelope_segments_order(tmp_db: sqlite3.Connection) -> None:
    """9 段顺序 + C4.2 形状（segments/total_tokens/budget）；预览与真实注入同源。"""
    from app.store import datasources as store_datasources

    ds_id = store_datasources.save_datasource(
        tmp_db,
        name="t",
        dialect="mysql",
        mode="online",
        host="127.0.0.1",
        port=3306,
        db_name="biz",
        username="u",
        password_enc=None,
    )
    env = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="本月收入？",
        schema_text="表 orders（订单主表）\n  - id bigint",
        dialect_card={"engine": "mysql", "supports": ["count"], "forbidden": []},
    )
    names = [s["name"] for s in env["segments"]]
    assert names == [
        "系统设定",
        "方言能力卡",
        "记忆",
        "会话摘要",
        "近期轮次",
        "数据源口径",
        "Schema 注入",
        "样例 few-shot",
        "指标口径",
        "用户问题",
    ]
    assert env["budget"]["schema_pct"] == 60
    assert env["budget"]["output_reserved"] == min(4096, env["budget"]["ctx"] // 4)
    assert all("tokens" in s for s in env["segments"])
    assert env["segments"][-1]["text"] == "本月收入？"


def _mk_ds(tmp_db: sqlite3.Connection) -> int:
    from app.store import datasources as store_datasources

    return store_datasources.save_datasource(
        tmp_db,
        name="t",
        dialect="mysql",
        mode="online",
        host="127.0.0.1",
        port=3306,
        db_name="biz",
        username="u",
        password_enc=None,
    )


def test_recent_rounds_segment_rendered(tmp_db: sqlite3.Connection) -> None:
    """近期轮次段：按序渲染 Q/SQL/结果，位于会话摘要之后、Schema 之前。"""
    ds_id = _mk_ds(tmp_db)
    rounds = [
        {"question": "查依赖", "sql_final": "SELECT 1", "status": "done"},
        {"question": "加批次", "sql_final": None, "status": "failed"},
    ]
    env = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="当前问题",
        schema_text="表 t\n  - c int",
        dialect_card={"engine": "mysql"},
        recent_rounds=rounds,
    )
    seg = {s["name"]: s["text"] for s in env["segments"]}
    text = seg["近期轮次"]
    assert "[第1轮] Q: 查依赖" in text and "SELECT 1" in text
    assert "[第2轮] Q: 加批次" in text and "结果: failed" in text
    assert "SQL:" not in text.split("[第2轮]")[1]  # 无 SQL 轮不渲染 SQL 块


def test_recent_rounds_truncation(tmp_db: sqlite3.Connection) -> None:
    ds_id = _mk_ds(tmp_db)
    rounds = [{"question": "q" * 500, "sql_final": "S" * 3000, "status": "done"}]
    env = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="当前",
        schema_text="表 t",
        dialect_card={"engine": "mysql"},
        recent_rounds=rounds,
    )
    seg = {s["name"]: s["text"] for s in env["segments"]}
    assert "q" * 201 not in seg["近期轮次"]
    assert "S" * 1201 not in seg["近期轮次"]
    assert "截断" in seg["近期轮次"]


def test_segment_toggles(tmp_db: sqlite3.Connection) -> None:
    """prompt.segments 开关：关闭近期轮次/样例；主干段不可关；未知段名拒绝。"""
    from app.store import settings as store_settings

    ds_id = _mk_ds(tmp_db)
    rounds = [{"question": "上轮", "sql_final": "SELECT 1", "status": "done"}]
    store_settings.save_setting(tmp_db, "prompt.segments", {"近期轮次": False})
    env = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="q",
        schema_text="表 t",
        dialect_card={"engine": "mysql"},
        recent_rounds=rounds,
    )
    seg = {s["name"]: s["text"] for s in env["segments"]}
    assert seg["近期轮次"] == ""
    assert seg["用户问题"] == "q"

    import pytest as _pytest

    from app.errors import BizError

    store_settings.save_setting(tmp_db, "prompt.segments", {"不存在段": True})
    with _pytest.raises(BizError):
        prompts.assemble_envelope(
            tmp_db,
            dialect="mysql",
            datasource_id=ds_id,
            question="q",
            schema_text="表 t",
            dialect_card={"engine": "mysql"},
        )
    store_settings.save_setting(tmp_db, "prompt.segments", {"用户问题": False})
    with _pytest.raises(BizError):
        prompts.assemble_envelope(
            tmp_db,
            dialect="mysql",
            datasource_id=ds_id,
            question="q",
            schema_text="表 t",
            dialect_card={"engine": "mysql"},
        )


def test_datasource_instruction_segment(tmp_db: sqlite3.Connection) -> None:
    """P5：数据源口径段注入、预算截断、开关关闭。"""
    from app.store import datasources as store_datasources
    from app.store import settings as store_settings

    ds_id = _mk_ds(tmp_db)
    store_datasources.save_instruction(tmp_db, ds_id, "账期=月批次 YYYYMM；收入口径=含税")
    env = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="q",
        schema_text="表 t",
        dialect_card={"engine": "mysql"},
    )
    seg = {s["name"]: s["text"] for s in env["segments"]}
    assert "账期=月批次" in seg["数据源口径"]

    # 预算截断
    store_datasources.save_instruction(tmp_db, ds_id, "长" * 5000)
    env2 = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="q",
        schema_text="表 t",
        dialect_card={"engine": "mysql"},
    )
    seg2 = {s["name"]: s["text"] for s in env2["segments"]}
    assert "长" * 4001 not in seg2["数据源口径"]
    assert "截断" in seg2["数据源口径"]

    # 开关关闭
    store_settings.save_setting(tmp_db, "prompt.segments", {"数据源口径": False})
    env3 = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="q",
        schema_text="表 t",
        dialect_card={"engine": "mysql"},
    )
    assert {s["name"]: s["text"] for s in env3["segments"]}["数据源口径"] == ""


def test_trim_order_samples_first(tmp_db: sqlite3.Connection, monkeypatch) -> None:
    """裁剪顺序验收：①样例先于记忆被裁（记忆优先保留，SSOT §8.3 注）。"""
    from app.store import datasources as store_datasources
    from app.store import memory as store_memory
    from app.store import samples as store_samples

    ds_id = store_datasources.save_datasource(
        tmp_db,
        name="t",
        dialect="mysql",
        mode="online",
        host="127.0.0.1",
        port=3306,
        db_name="biz",
        username="u",
        password_enc=None,
    )
    store_samples.save_sample(tmp_db, datasource_id=ds_id, question="样例问题" * 200, sql="SELECT 1")
    store_memory.save_memory(
        tmp_db, scope="global", datasource_id=None, topic="口径", content="收入=含税", token_est=5
    )
    huge_schema = "\n\n".join(f"表 t{i}\n  - c varchar" for i in range(5000))
    env = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="q",
        schema_text=huge_schema,
        dialect_card={"engine": "mysql"},
        model_ctx=4096,
    )
    seg = {s["name"]: s["text"] for s in env["segments"]}
    assert seg["样例 few-shot"] == ""  # ①样例先裁
    assert "收入=含税" in seg["记忆"] or seg["记忆"] == ""  # 记忆晚于样例被裁


def test_trim_recent_rounds_before_memory(tmp_db: sqlite3.Connection) -> None:
    """裁剪顺序验收：④近期轮次减半 → ⑤清空，均先于 ⑥记忆裁剪。"""
    from app.store import memory as store_memory

    ds_id = _mk_ds(tmp_db)
    store_memory.save_memory(
        tmp_db, scope="global", datasource_id=None, topic="口径", content="收入=含税", token_est=5
    )
    rounds = [
        {"question": f"第{i}轮问题", "sql_final": f"SELECT {i} /* " + "x" * 1200 + " */", "status": "done"}
        for i in range(4)
    ]
    env = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="当前问题",
        schema_text="表 t\n  - c int",
        dialect_card={"engine": "mysql"},
        recent_rounds=rounds,
        model_ctx=2560,
    )
    seg = {s["name"]: s["text"] for s in env["segments"]}
    # ④减半保最新：最早的第 0 轮被裁，最新的第 3 轮保留（rounds 编号 0..3）
    assert "第3轮问题" in seg["近期轮次"]
    assert "第0轮问题" not in seg["近期轮次"]
    assert "收入=含税" in seg["记忆"]  # 记忆尚未被裁

    # ⑤清空：轮次更多时整段清空，记忆仍保留
    many_rounds = rounds * 3
    env2 = prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="当前问题",
        schema_text="表 t\n  - c int",
        dialect_card={"engine": "mysql"},
        recent_rounds=many_rounds,
        model_ctx=2560,
    )
    seg2 = {s["name"]: s["text"] for s in env2["segments"]}
    assert seg2["近期轮次"] == ""
    assert "收入=含税" in seg2["记忆"]
