"""意图识别与库概况整理（route.ai 承载，AI 优先、启发式兜底）。

设计（评审项-用户反馈 2026-10-16）：
- 语义识别交给 AI，不写死词形裁决：「问库概况（meta）vs 取数（data）」由
  route.ai 分类；正则仅作 **AI 不可用时的兜底**，绝不在 AI 可用时拍板。
- 明显的取数问题（命中取数意图词）直接放行，不为每个问题都付出一次 AI 调用；
  其余一律由 AI 判定。
- 库概况答案由 AI 基于元数据整理成文（流式可见）；AI 失败回退确定性渲染。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("aitosql.llm.intent")

_CLASSIFY_SYSTEM = (
    "你是查询意图分类器。判断用户问题属于哪类：\n"
    "- meta：库概况问题——问库里有哪些表、某张表/字段是干什么的、结构关系，不需要执行 SQL 查数据\n"
    "- data：取数问题——需要执行 SQL 才能得到答案（统计、筛选、趋势、排名、具体值等）\n"
    '只输出 JSON：{"intent":"meta"} 或 {"intent":"data"}，不要输出任何其他内容。拿不准一律 data。'
)

_COMPOSE_SYSTEM = (
    "你是 AItosql 的库概况讲解助手。根据给定的数据源元数据（JSON）回答用户的库概况问题。\n"
    "要求：\n"
    "1. 中文回答，结构清晰：先总述（几张表、库类型），再逐表编号说明（表名 — 用途/规模/分区）\n"
    "2. 纯文本排版：可用编号、短横线和换行；不要用 markdown 表格\n"
    "3. 严格忠实元数据：没有注释的表明说「暂无注释」，不得编造业务含义\n"
    "4. 结尾给 1~2 个用户可继续追问的取数问题建议（结合表用途）\n"
    "5. 全文控制在 400 字以内"
)


def _parse_intent(text: str) -> str | None:
    """容错解析分类结果：JSON 优先，关键词兜底。"""
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        intent = str(json.loads(text[start:end]).get("intent") or "").strip().lower()
        if intent in ("meta", "data"):
            return intent
    except (ValueError, json.JSONDecodeError, AttributeError):
        pass
    if "meta" in text.lower():
        return "meta"
    return None


async def classify(chat_fn: Any, question: str, *, route_ref: str, on_delta: Any = None) -> str | None:
    """AI 意图分类。返回 meta|data；任何失败（路由未配/端点异常/解析失败）返回 None，
    由调用方回退启发式——AI 优先但不阻塞主链路。"""
    try:
        outcome = await chat_fn(
            route_ref,
            [{"role": "system", "content": _CLASSIFY_SYSTEM}, {"role": "user", "content": question}],
            1024,
            on_delta=on_delta,
        )
        intent = _parse_intent(getattr(outcome, "text", "") or "")
        if intent is None:
            logger.info("意图分类结果无法解析，回退启发式: %r", (outcome.text or "")[:120])
        return intent
    except Exception as exc:
        logger.info("意图分类调用失败（%s），回退启发式", type(exc).__name__)
        return None


async def compose_answer(
    chat_fn: Any, question: str, metadata: dict[str, Any], *, route_ref: str, on_delta: Any = None
) -> str | None:
    """AI 整理库概况答案（流式）；失败返回 None 由调用方回退确定性渲染。"""
    user = json.dumps({"question": question, "metadata": metadata}, ensure_ascii=False)
    try:
        outcome = await chat_fn(
            route_ref,
            [{"role": "system", "content": _COMPOSE_SYSTEM}, {"role": "user", "content": user}],
            4096,
            on_delta=on_delta,
        )
        text = (getattr(outcome, "text", "") or "").strip()
        return text or None
    except Exception as exc:
        logger.info("库概况整理调用失败（%s），回退确定性渲染", type(exc).__name__)
        return None
