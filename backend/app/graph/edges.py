"""条件边（SSOT §5.3 逐行，design D-31）。

```
supply_schema ─▶ generate ─▶ [有 high 假设且开关开] ? clarify_gate ─resume─▶ generate
                           └否─▶ validate ─▶ [R15 命中] → END（failed，交付原 SQL+原因）
                                          └▶ [可修正错误] ? correct ─▶ validate
                                          └▶ [非纯 SELECT 未确认] ? confirm_gate ─resume─┐
                                                        └▶ execute ─▶ [引擎错误] ? correct
                                             └▶ verify_result ─▶ [不过且有回退额度] ? correct
                                                                                       └▶ END
correct 终止（round≥3 / 振荡）→ END（failed，交付最近 SQL + 标注）
need_tables：R13（online）且 resupply_used=false → supply_schema（补表）→ generate
```
"""

from __future__ import annotations

from typing import Literal

from app.graph.state import QueryState
from app.sqlcheck.rules import has_write_risk

END = "__end__"

Route = Literal[
    "supply_schema",
    "generate",
    "clarify_gate",
    "validate",
    "confirm_gate",
    "execute",
    "verify_result",
    "correct",
    "__end__",
]


def after_generate(state: QueryState, *, clarify_gate_on: bool) -> str:
    """generate 之后：元数据直通 → END；need_tables 二次供给（仅 1 次）→ 澄清门 → validate。"""
    if state.get("meta_answer"):
        return END  # 元数据问题直通：答案已在 contract.reasoning，跳过校验/执行
    contract = state.get("contract") or {}
    if contract.get("need_tables") and not state.get("resupply_used") and state.get("ds_mode") == "online":
        return "supply_schema"
    if (
        clarify_gate_on
        and not state.get("clarify_choice")
        and any(a.get("impact") == "high" for a in state.get("assumptions") or [])
    ):
        return "clarify_gate"
    return "validate"


def after_clarify_gate(state: QueryState) -> str:
    """澄清裁决后重进 generate（自定义已文本追加 question）。"""
    return "generate"


def after_validate(state: QueryState, *, write_confirm_on: bool, max_rounds: int) -> str:
    """validate 之后：R15 硬阻断 → END(failed)；可修正 → correct；
    写操作未确认 → confirm_gate；离线库 → END（generate_only）；其余 → execute。

    轮数耗尽分支（needs_correct 且 round≥max_rounds）直接 END：终态由
    runner._finalize 依据 needs_correct 残留映射 **failed** 并先落 E2004
    error 事件（P1-14 修复：不得以 done 静默交付）。
    """
    if state.get("hard_blocked"):
        return END
    if state.get("needs_correct") and int(state.get("round", 0)) < max_rounds:
        return "correct"
    if state.get("needs_correct"):
        return END  # 轮数耗尽 → _finalize 落 failed + E2004（见上注）
    if (
        has_write_risk(list(state.get("risks") or []))
        and write_confirm_on
        and not state.get("write_confirmed")
    ):
        return "confirm_gate"
    if state.get("ds_mode") != "online":
        return END  # 离线库只生成不执行（generate_only）
    return "execute"


def after_confirm_gate(state: QueryState) -> str:
    if state.get("gate_decision") == "approved" or state.get("write_confirmed"):
        return "execute"
    return END  # 取消 → stopped（节点已落 stopped 事件）


def after_execute(state: QueryState, *, max_rounds: int) -> str:
    """execute 之后：节点内终态（连接失败等）→ END；可修正（引擎错误）且有轮数
    → correct；轮数耗尽 → END（_finalize 落 failed + E2004）；离线库 → END；其余 → verify_result。"""
    if state.get("status") == "failed":
        return END  # 连接失败等节点内终态（error 事件已落，不进 verify）
    if state.get("needs_correct"):
        if int(state.get("round", 0)) < max_rounds:
            return "correct"
        return END
    if state.get("ds_mode") != "online":
        return END
    return "verify_result"


def after_verify(state: QueryState, *, max_rounds: int) -> str:
    """不过且有回退额度 → correct；其余 → END（unverified 照常交付）。

    注：needs_correct 残留且轮数耗尽时，_finalize 映射 failed + E2004（P1-14）。
    """
    if state.get("needs_correct") and int(state.get("round", 0)) < max_rounds:
        return "correct"
    return END


def after_correct(state: QueryState) -> str:
    """correct 终止（failed）→ END；否则回 validate（所验即所执：新 SQL 重过校验）。"""
    if state.get("status") == "failed":
        return END
    return "validate"
