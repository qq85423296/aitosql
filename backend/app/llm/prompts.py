"""提示词 8 段信封 + 5 步裁剪 + token 估算 + 生成契约解析（design D-09/D-34，BR-12/BR-3）。

**唯一组装器**：设置页组装预览（C4.2）与真实注入同走 `assemble_envelope`；
表级提示词视图的 Schema 段渲染器在 retrieval/supply.render_table_text（同一渲染源）。
裁剪顺序（SSOT §8.3）：①减样例 → ②减枚举(top8→top4) → ③retrieval 重建减表
→ ④减记忆条目（最旧）→ ⑤尾部确定性截断。输出预留 = min(4096, 25% × total_ctx)。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator

from app import tokens as app_tokens
from app.errors import E_BAD_REQUEST, E_CONTRACT_PARSE, BizError
from app.store import datasources as store_datasources
from app.store import memory as store_memory
from app.store import samples as store_samples
from app.store import semantics as store_semantics
from app.store import settings as store_settings

logger = logging.getLogger("aitosql.llm.prompts")

DEFAULT_CTX = 32768
SEG_SYSTEM = "系统设定"
SEG_DIALECT = "方言能力卡"
SEG_MEMORY = "记忆"
SEG_SUMMARY = "会话摘要"
SEG_RECENT = "近期轮次"
SEG_DS_DOC = "数据源口径"
SEG_SCHEMA = "Schema 注入"
SEG_SAMPLES = "样例 few-shot"
SEG_METRICS = "指标口径"
SEG_QUESTION = "用户问题"

# 段注册表（借鉴 DSH dsh-system-prompt：name+order 排序渲染，配置可开关——
# 「prompt.segments」设置 {段名: bool} 控制启停，缺省全开；系统设定/方言卡/用户问题
# 为主干段不可关闭）。新增段只需在此登记 + build_segments 提供文本。
SEGMENT_REGISTRY: list[dict[str, Any]] = [
    {"name": SEG_SYSTEM, "order": 0, "toggleable": False},
    {"name": SEG_DIALECT, "order": 10, "toggleable": False},
    {"name": SEG_MEMORY, "order": 20, "toggleable": True},
    {"name": SEG_SUMMARY, "order": 30, "toggleable": True},
    {"name": SEG_RECENT, "order": 40, "toggleable": True},
    {"name": SEG_DS_DOC, "order": 45, "toggleable": True},
    {"name": SEG_SCHEMA, "order": 50, "toggleable": False},
    {"name": SEG_SAMPLES, "order": 60, "toggleable": True},
    {"name": SEG_METRICS, "order": 70, "toggleable": True},
    {"name": SEG_QUESTION, "order": 100, "toggleable": False},
]
SEGMENT_NAMES = [s["name"] for s in SEGMENT_REGISTRY]
_TOGGLABLE = {s["name"] for s in SEGMENT_REGISTRY if s["toggleable"]}


def resolve_segment_toggles(raw: Any) -> dict[str, bool]:
    """解析「prompt.segments」配置：未知段名/不可关闭段拒绝（组装期 fail-fast）。"""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise BizError(E_BAD_REQUEST, "prompt.segments 应为 {段名: bool} 对象", 422)
    toggles: dict[str, bool] = {}
    for name, enabled in raw.items():
        if name not in SEGMENT_NAMES:
            raise BizError(E_BAD_REQUEST, f"未知信封段名: {name}", 422)
        if name not in _TOGGLABLE and enabled is False:
            raise BizError(E_BAD_REQUEST, f"主干段不可关闭: {name}", 422)
        toggles[str(name)] = bool(enabled)
    return toggles


# ---------- C4.1 生成契约（M3 输出 JSON Schema，SSOT §6 原文口径） ----------


class Assumption(BaseModel):
    """假设条目。模型键名漂移容忍：text 亦接受 assumption/desc/description（实测 glm-5.3 用 assumption）。"""

    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(validation_alias=AliasChoices("text", "assumption", "desc", "description"))
    impact: str = Field(pattern="^(high|low)$")
    kind: str | None = Field(default=None, pattern="^(time|caliber|relation|enum|other)$")

    @field_validator("impact", mode="before")
    @classmethod
    def _norm_impact(cls, v: object) -> str:
        s = str(v or "").strip().lower()
        return "high" if s in ("high", "高") else "low"


class GenerationContract(BaseModel):
    """M3 输出契约：reasoning / assumptions / need_tables / sql / followups。"""

    reasoning: str = Field(max_length=1200)
    assumptions: list[Assumption]
    sql: str
    need_tables: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list, max_length=3)


_SQL_BLOCK_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_contract(raw: str) -> GenerationContract:
    """契约解析：JSON 校验 →（调用方负责原样回喂重试 1 次）→ 正则提取 ```sql 降级。

    降级结果标注「未结构化输出」（degraded=True 由调用方记录，E2002 语义）。
    """
    text = raw.strip()
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return GenerationContract.model_validate(json.loads(text[start:end]))
    except (ValueError, ValidationError) as exc:
        m = _SQL_BLOCK_RE.search(text)
        if m and m.group(1).strip():
            logger.warning("契约解析失败，降级正则提取 SQL（未结构化输出）")
            return GenerationContract(
                reasoning="未结构化输出（契约解析失败降级）",
                assumptions=[],
                sql=m.group(1).strip(),
                need_tables=[],
                followups=[],
            )
        raise BizError(E_CONTRACT_PARSE, "契约解析失败（重试后仍无法提取 SQL）", 500) from exc


# ---------- 段渲染 ----------


def render_system_prompt(template: str, dialect: str) -> str:
    """变量替换：{{dialect}} / {{date}}（YYYY-MM-DD 本地时区，C0）。"""
    return template.replace("{{dialect}}", dialect).replace("{{date}}", datetime.now().strftime("%Y-%m-%d"))


def render_dialect_card(card: dict[str, Any]) -> str:
    """能力卡文本段（supports/forbidden 双层覆盖提示，评审项 22）。"""
    lines = [f"引擎: {card.get('engine', '')} {card.get('detected_version', '')}".rstrip()]
    supports = card.get("supports") or []
    if supports:
        lines.append("支持: " + ", ".join(str(s) for s in supports))
    forbidden = card.get("forbidden") or []
    if forbidden:
        lines.append("禁止: " + "；".join(str(f) for f in forbidden))
    for note in card.get("notes") or []:
        lines.append(f"注: {note}")
    return "\n".join(lines)


def render_memory_segment(memories: list) -> str:
    if not memories:
        return ""
    return "\n".join(f"- [{m.topic}] {m.content}" for m in memories)


def render_samples_segment(samples: list) -> str:
    if not samples:
        return ""
    return "\n\n".join(f"问题: {s.question}\nSQL:\n{s.sql}" for s in samples)


def render_metrics_segment(metrics: list) -> str:
    if not metrics:
        return ""
    lines = []
    for m in metrics:
        line = f"- {m.name}: {m.expr}"
        if m.filter:
            line += f"（过滤: {m.filter}）"  # D-19：filter 值按文本注入，SQL 侧全链路引号
        lines.append(line)
    return "\n".join(lines)


def render_recent_segment(
    rounds: list[dict[str, Any]], *, max_question_chars: int = 200, max_sql_chars: int = 1200
) -> str:
    """近期轮次段（DSH deriveMessages 语义）：最近 K 轮 question+sql_final 逐字注入。

    rounds 按时间升序（旧→新）；每轮渲染为 Q/SQL/结果 三行，超长确定性截断。
    """
    if not rounds:
        return ""
    blocks: list[str] = []
    for i, r in enumerate(rounds, 1):
        question = str(r.get("question") or "").strip()
        if len(question) > max_question_chars:
            question = question[:max_question_chars] + "…"
        sql = str(r.get("sql_final") or "").strip()
        if len(sql) > max_sql_chars:
            sql = sql[:max_sql_chars] + "\n-- …（截断）"
        status = str(r.get("status") or "")
        block = f"[第{i}轮] Q: {question}"
        if sql:
            block += f"\nSQL:\n{sql}"
        block += f"\n结果: {status or '未知'}"
        blocks.append(block)
    return "\n\n".join(blocks)


def render_ds_doc_segment(content: str, *, max_chars: int = 4000) -> str:
    """数据源口径段（P5，借鉴 dsh-agent-instructions 的字节预算语义）：超限确定性截断。"""
    text = (content or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（超出预算截断）"
    return text


def hit_metric_names(conn: sqlite3.Connection, question: str) -> list[str]:
    """指标命中：问题含指标名即注入该口径段（§12.7；未命中不注入）。"""
    names = [m.name for m in store_semantics.query_metrics(conn)]
    return [n for n in names if n and n in question]


# ---------- 信封组装（唯一生成器） ----------


def assemble_envelope(
    conn: sqlite3.Connection,
    *,
    dialect: str,
    datasource_id: int,
    question: str,
    schema_text: str,
    dialect_card: dict[str, Any],
    session_summary: str | None = None,
    recent_rounds: list[dict[str, Any]] | None = None,
    model_ctx: int | None = None,
    schema_rerender: Callable[[int], str] | None = None,
) -> dict[str, Any]:
    """组装 9 段信封 + 预算裁剪；返回 C4.2 形状（segments/total_tokens/budget）。

    schema_rerender(enum_top_n) 为裁剪 ②③ 的重建钩子（由调用方注入，graph 节点经
    retrieval/supply 实现；缺省时②③退化为按表块确定性丢弃）。
    近期轮次段：调用方传入最近 K 轮（升序），裁剪顺序位于减表之后、记忆之前。
    """
    ctx = model_ctx or DEFAULT_CTX
    output_reserved = min(4096, ctx // 4)
    schema_pct = int(store_settings.get_setting(conn, "schema.budget_pct"))
    budget_tokens = ctx - output_reserved

    sys_tpl = str(store_settings.get_setting(conn, "prompt.system"))
    fewshot_n = int(store_settings.get_setting(conn, "sample.fewshot_n"))
    memory_on = bool(store_settings.get_setting(conn, "memory.enabled"))
    memory_inject = bool(store_settings.get_setting(conn, "memory.inject"))
    seg_toggles = resolve_segment_toggles(store_settings.get_setting(conn, "prompt.segments"))
    max_q_chars = int(store_settings.get_setting(conn, "history.max_question_chars"))
    max_sql_chars = int(store_settings.get_setting(conn, "history.max_sql_chars"))
    doc_max_chars = int(store_settings.get_setting(conn, "datasource.instruction_max_chars"))

    samples = store_samples.query_fewshot(conn, datasource_id, fewshot_n, question=question)
    memories = store_memory.query_injectable(conn, datasource_id) if memory_on and memory_inject else []
    metrics = store_semantics.query_metrics_for_inject(conn, hit_metric_names(conn, question))
    ds_doc = store_datasources.get_instruction(conn, datasource_id)
    card_text = render_dialect_card(dialect_card)
    enum_top_n = int(store_settings.get_setting(conn, "schema.enum_top_n"))
    rounds = list(recent_rounds or [])

    state: dict[str, Any] = {
        "samples": list(samples),
        "memories": list(memories),
        "schema": schema_text,
        "enum_top_n": enum_top_n,
        "rounds": rounds,
    }

    def seg_enabled(name: str) -> bool:
        return seg_toggles.get(name, True)

    def build_segments() -> list[dict[str, Any]]:
        texts: dict[str, str] = {
            SEG_SYSTEM: render_system_prompt(sys_tpl, dialect),
            SEG_DIALECT: card_text,
            SEG_MEMORY: render_memory_segment(state["memories"]),
            SEG_SUMMARY: session_summary or "",
            SEG_RECENT: render_recent_segment(
                state["rounds"],
                max_question_chars=max_q_chars,
                max_sql_chars=max_sql_chars,
            ),
            SEG_DS_DOC: render_ds_doc_segment(ds_doc, max_chars=doc_max_chars),
            SEG_SCHEMA: state["schema"],
            SEG_SAMPLES: render_samples_segment(state["samples"]),
            SEG_METRICS: render_metrics_segment(metrics),
            SEG_QUESTION: question,
        }
        return [
            {"name": spec["name"], "text": texts[spec["name"]] if seg_enabled(spec["name"]) else ""}
            for spec in SEGMENT_REGISTRY
        ]

    def total() -> int:
        return sum(app_tokens.estimate_tokens(s["text"]) for s in build_segments())

    # 6 步裁剪（超预算依次执行，SSOT §8.3 + 近期轮次扩展：
    # ①减样例 → ②减枚举(top8→top4) → ③retrieval 重建减表 → ④近期轮次减半
    # → ⑤近期轮次清空 → ⑥减记忆条目（最旧）→ 尾部确定性截断）
    if total() > budget_tokens and state["samples"]:  # ① 减样例
        state["samples"] = state["samples"][:1]
    if total() > budget_tokens and state["samples"]:
        state["samples"] = []
    if total() > budget_tokens:  # ② 减枚举 top8→top4
        if schema_rerender is not None and state["enum_top_n"] > 4:
            state["enum_top_n"] = 4
            state["schema"] = schema_rerender(4)
        else:
            _drop_last_block(state)
    if total() > budget_tokens:  # ③ retrieval 重建减表
        _drop_last_block(state)
    if total() > budget_tokens and len(state["rounds"]) > 1:  # ④ 近期轮次减半（保最新）
        state["rounds"] = state["rounds"][len(state["rounds"]) // 2 :]
    if total() > budget_tokens and state["rounds"]:  # ⑤ 近期轮次清空
        state["rounds"] = []
    if total() > budget_tokens and state["memories"]:  # ⑥ 减记忆（最旧）
        state["memories"] = state["memories"][-1:]
    if total() > budget_tokens and state["memories"]:
        state["memories"] = []
    segments = build_segments()
    if sum(app_tokens.estimate_tokens(s["text"]) for s in segments) > budget_tokens:
        # ⑦ 尾部确定性截断（Schema 段）
        _truncate_schema(segments, budget_tokens, output_reserved)
    if sum(app_tokens.estimate_tokens(s["text"]) for s in segments) > budget_tokens:
        # Drop optional context as complete segments; never silently cut the user's question.
        for name in (SEG_SUMMARY, SEG_METRICS, SEG_RECENT, SEG_MEMORY, SEG_SAMPLES):
            for segment in segments:
                if segment["name"] == name:
                    segment["text"] = ""
            if sum(app_tokens.estimate_tokens(s["text"]) for s in segments) <= budget_tokens:
                break
    if sum(app_tokens.estimate_tokens(s["text"]) for s in segments) > budget_tokens:
        raise BizError("E4420", "固定提示词与问题超出模型上下文预算，请缩短内容或调整模型窗口", 422)
    app_tokens.estimate_segments(segments)
    return {
        "segments": segments,
        "total_tokens": sum(s["tokens"] for s in segments),
        "budget": {"ctx": ctx, "schema_pct": schema_pct, "output_reserved": output_reserved},
    }


def envelope_to_messages(envelope: dict[str, Any]) -> list[dict[str, str]]:
    """信封 → chat messages（系统段合并为 system，其余按注册表顺序并入 user）。

    按段名拣选而非位置切片：段注册表顺序调整不会静默改变消息归属。
    """
    segs = envelope["segments"]
    by_name = {s["name"]: s["text"] for s in segs}
    system_parts = [by_name.get(n) or "" for n in (SEG_SYSTEM, SEG_DIALECT, SEG_MEMORY)]
    system_parts = [t for t in system_parts if t]
    system_seg_names = (SEG_SYSTEM, SEG_DIALECT, SEG_MEMORY, SEG_QUESTION)
    user_parts = [s["text"] for s in segs if s["text"] and s["name"] not in system_seg_names]
    question = by_name.get(SEG_QUESTION) or ""
    messages: list[dict[str, str]] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    messages.append({"role": "user", "content": "\n\n".join([*user_parts, question])})
    return messages


def _drop_last_block(state: dict[str, Any]) -> None:
    """Schema 段按表块（空行分隔）确定性丢弃末块。"""
    blocks = [b for b in state["schema"].split("\n\n") if b.strip()]
    if blocks:
        state["schema"] = "\n\n".join(blocks[:-1])


def _truncate_schema(segments: list[dict[str, Any]], budget_tokens: int, output_reserved: int) -> None:
    """尾部确定性截断：按字符比例裁剪 Schema 段（token 估算 ±15-20% 余量口径）。"""
    others = sum(app_tokens.estimate_tokens(s["text"]) for s in segments if s["name"] != SEG_SCHEMA)
    room = max(budget_tokens - others, 0)
    for segment in segments:
        if segment["name"] == SEG_SCHEMA:
            blocks = segment["text"].split("\n\n")
            while blocks and app_tokens.estimate_tokens("\n\n".join(blocks)) > room:
                blocks.pop()
            segment["text"] = "\n\n".join(blocks)
            break
