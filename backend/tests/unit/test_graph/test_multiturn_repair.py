"""Golden 回归：三轮修复翻车场景（2026-10-29 故障复盘 §1）。

场景：R1「查询调度任务X的后续一级依赖」→ 依赖查询 SQL（含 dacp_dataflow_job_depend
JOIN）；R2「我要的是后续一级依赖，不是全部依赖」；R3「调度实例信息需要加 batch_no」。

修复前：R3 信封里没有任何历史轮次 → 模型把增量约束当全新需求 → 依赖查询整个丢失。
修复后（P0）：R3 信封「近期轮次」段逐字携带 R1/R2 的 question+sql_final，模型必然
看到依赖查询。本测试冻结该不变式（不依赖 LLM：断言信封组装层）。
"""

from __future__ import annotations

import sqlite3

from app.graph import nodes as graph_nodes
from app.ids import new_query_id
from app.llm import prompts as llm_prompts
from app.store import datasources as store_datasources
from app.store import queries as store_queries
from app.store import sessions as store_sessions

_R1_SQL = (
    "SELECT nxt.job_code, nxt.job_label\n"
    "FROM dacp_dataflow_job cur\n"
    "JOIN dacp_dataflow_job_depend d ON d.depend_code = cur.job_code AND d.depend_type = '10'\n"
    "JOIN dacp_dataflow_job nxt ON nxt.job_code = d.job_code\n"
    "WHERE cur.job_label = '调度任务X' AND cur.batch_no = '202610'"
)
_R2_SQL = (
    "SELECT nxt.job_code, nxt.job_label, nxt.batch_no\n"
    "FROM dacp_dataflow_job cur\n"
    "JOIN dacp_dataflow_job_depend d ON d.depend_code = cur.job_code AND d.depend_type = '10'\n"
    "JOIN dacp_dataflow_job nxt ON nxt.job_code = d.job_code\n"
    "WHERE cur.job_code = 'JOB001' AND cur.batch_no = '202610'"
)


def _seed_rounds(tmp_db: sqlite3.Connection) -> tuple[str, int, str]:
    ds_id = store_datasources.save_datasource(
        tmp_db,
        name="ds_repair",
        dialect="mysql",
        mode="online",
        host="127.0.0.1",
        port=3306,
        db_name="d",
        username="u",
        password_enc=None,
    )
    session = store_sessions.save_session(tmp_db, datasource_id=ds_id)
    for q_text, sql in (
        ("查询调度任务X的后续一级依赖", _R1_SQL),
        ("我要的是后续一级依赖，不是全部依赖", _R2_SQL),
    ):
        qid = new_query_id()
        store_queries.save_query(
            tmp_db,
            query_id=qid,
            session_id=session.id,
            datasource_id=ds_id,
            dialect="mysql",
            question=q_text,
        )
        store_queries.update_query_result(tmp_db, qid, status="done", sql_final=sql)
    r3_id = new_query_id()  # 当轮（尚未落库也行，recent_rounds 按 id 排除）
    return session.id, ds_id, r3_id


def test_repair_round_envelope_carries_prior_sql(tmp_db: sqlite3.Connection) -> None:
    """R3 信封近期轮次段必须携带依赖查询 JOIN 与批次过滤（修复回归锚点）。"""
    session_id, ds_id, r3_id = _seed_rounds(tmp_db)
    rounds = store_queries.recent_rounds(tmp_db, session_id, exclude_query_id=r3_id, limit=6)
    assert len(rounds) == 2
    env = llm_prompts.assemble_envelope(
        tmp_db,
        dialect="mysql",
        datasource_id=ds_id,
        question="调度实例信息需要加 batch_no 批次日期",  # R3 裸增量约束
        schema_text="表 dacp_dataflow_job\n表 dacp_dataflow_job_depend",
        dialect_card={"engine": "mysql"},
        recent_rounds=rounds,
    )
    seg = {s["name"]: s["text"] for s in env["segments"]}
    recent = seg["近期轮次"]
    assert "dacp_dataflow_job_depend" in recent  # 依赖 JOIN 结构可见
    assert "depend_type" in recent
    assert "batch_no" in recent  # 批次过滤可见
    assert "我要的是后续一级依赖" in recent  # R2 修复意图可见
    # messages 层同样可见（用户消息聚合段）
    messages = llm_prompts.envelope_to_messages(env)
    user_text = messages[-1]["content"]
    assert "dacp_dataflow_job_depend" in user_text
    assert user_text.rstrip().endswith("调度实例信息需要加 batch_no 批次日期")


def test_repair_retrieval_question_concat(tmp_db: sqlite3.Connection) -> None:
    """R3 检索问题 = 上一轮问题 + 当前句（治检索焦点漂移）。"""
    session_id, _ds_id, r3_id = _seed_rounds(tmp_db)

    class _Ctx:
        query_id = r3_id

    state = {"question": "调度实例信息需要加 batch_no 批次日期", "session_id": session_id}
    retrieval_q = graph_nodes._retrieval_question(tmp_db, state, _Ctx())
    assert "我要的是后续一级依赖" in retrieval_q
    assert "batch_no" in retrieval_q
