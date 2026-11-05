"""图路由纯函数用例（SSOT §5.3 逐行）：不依赖 langgraph 运行时（R-1 spike 前置）。"""

from __future__ import annotations

from app.graph import edges
from app.graph.state import QueryState


def _state(**kw) -> QueryState:
    base: QueryState = {"ds_mode": "online", "round": 0}
    base.update(kw)  # type: ignore[typeddict-item]
    return base


def test_generate_to_clarify_on_high() -> None:
    s = _state(assumptions=[{"text": "收入口径", "impact": "high"}])
    assert edges.after_generate(s, clarify_gate_on=True) == "clarify_gate"
    assert edges.after_generate(s, clarify_gate_on=False) == "validate"


def test_generate_need_tables_resupply_once() -> None:
    s = _state(contract={"need_tables": ["refunds"]}, assumptions=[], resupply_used=False)
    assert edges.after_generate(s, clarify_gate_on=True) == "supply_schema"
    s2 = _state(contract={"need_tables": ["refunds"]}, assumptions=[], resupply_used=True)
    assert edges.after_generate(s2, clarify_gate_on=True) == "validate"  # 仅 1 次


def test_validate_r15_to_end() -> None:
    s = _state(hard_blocked=True)
    assert edges.after_validate(s, write_confirm_on=True, max_rounds=3) == "__end__"


def test_validate_write_to_confirm_gate() -> None:
    s = _state(risks=[{"rule_id": "R06", "level": "red", "message": "m"}], write_confirmed=False)
    assert edges.after_validate(s, write_confirm_on=True, max_rounds=3) == "confirm_gate"
    assert edges.after_validate(s, write_confirm_on=False, max_rounds=3) == "execute"


def test_validate_offline_to_end() -> None:
    s = _state(ds_mode="offline", risks=[])
    assert edges.after_validate(s, write_confirm_on=True, max_rounds=3) == "__end__"


def test_execute_engine_error_to_correct() -> None:
    s = _state(needs_correct=True, round=0)
    assert edges.after_execute(s, max_rounds=3) == "correct"
    s3 = _state(needs_correct=True, round=3)
    assert edges.after_execute(s3, max_rounds=3) == "__end__"  # 轮数上限


def test_verify_fallback_to_correct_then_end() -> None:
    s = _state(needs_correct=True, round=1)
    assert edges.after_verify(s, max_rounds=3) == "correct"
    assert edges.after_verify(_state(needs_correct=False), max_rounds=3) == "__end__"


def test_confirm_cancel_to_end() -> None:
    assert edges.after_confirm_gate(_state(gate_decision="cancelled")) == "__end__"
    assert edges.after_confirm_gate(_state(gate_decision="approved")) == "execute"


def test_correct_terminal_or_back_validate() -> None:
    assert edges.after_correct(_state(status="failed")) == "__end__"
    assert edges.after_correct(_state(status="running")) == "validate"
