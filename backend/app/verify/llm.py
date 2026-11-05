"""verify LLM 层（可选，design D-37 / SSOT §9.2，BR-8 后半 / T1.10）。

出境管控（D07/评审项 47）：local_only（三路全本地）下置灰禁用；
含云路由可开，出境清单单列「结果预览数据行（前 5 行）」红标（E6）；
输入截断 = 前 5 行 × 每格 50 字符。LLM 调用经显式参数注入（verify 不 import llm，D1-补）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

PREVIEW_ROWS_LIMIT = 5
CELL_CHAR_LIMIT = 50


def is_available(feature_verify_llm: bool, local_only: bool) -> bool:
    """开关判定：local_only 置灰禁用（D07）。"""
    return bool(feature_verify_llm) and not local_only


def truncate_preview(columns: list[dict[str, str]], rows: list[list[Any]]) -> dict[str, Any]:
    """输入截断：前 5 行 × 每格 50 字符（E6 出境类目的最小集）。"""
    trimmed_rows = [
        [(str(c)[:CELL_CHAR_LIMIT] if c is not None else None) for c in row]
        for row in rows[:PREVIEW_ROWS_LIMIT]
    ]
    return {"columns": [c.get("name", "") for c in columns], "rows": trimmed_rows}


def build_verify_messages(
    question: str, assumptions: list[dict[str, Any]], preview: dict[str, Any]
) -> list[dict[str, str]]:
    """判断「这份结果能回答这个问题吗」（§9.2 LLM 层）。"""
    assume_text = "\n".join(f"- {a.get('text', '')}" for a in assumptions) or "（无）"
    content = (
        f"问题：{question}\n"
        f"口径假设：\n{assume_text}\n"
        f"结果预览（前 {PREVIEW_ROWS_LIMIT} 行）：{preview}\n"
        '请判断这份结果能回答这个问题吗？只回答 JSON：{"passed": true/false,'
        ' "reason": "..."}'
    )
    return [{"role": "user", "content": content}]


async def verify_with_llm(
    *,
    question: str,
    assumptions: list[dict[str, Any]],
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    chat_fn: Callable[[list[dict[str, str]]], Awaitable[str]],
) -> dict[str, Any]:
    """LLM 层判定：chat_fn 由 graph 节点注入（route.fix 路由）。"""
    import json
    import re

    preview = truncate_preview(columns, rows)
    raw = await chat_fn(build_verify_messages(question, assumptions, preview))
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        return {"passed": bool(data.get("passed")), "reason": str(data.get("reason", ""))}
    except (ValueError, AttributeError):
        # Q-7 fail-closed：不可解析按「不过」处理——调用侧（verify_result 节点）
        # 仅在 passed=True 时覆盖规则层结论，False = 保持规则层判定，零误放行
        return {"passed": False, "reason": "LLM 判定输出不可解析（按规则层结论，fail-closed）"}
