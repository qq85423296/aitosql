"""golden 用例 pytest 桥（T0.4/T4.1 滚动门槛；need_engine 用例 CI 容器分组启用）。"""

from __future__ import annotations

import importlib.util

import pytest

from tests.golden.runner import load_cases, run_case

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sqlglot") is None,
    reason="sqlglot 未安装（uv sync 后启用）",
)


@pytest.mark.parametrize(
    "case", [c for c in load_cases() if not c.get("need_engine")], ids=lambda c: c.get("id", "unnamed")
)
def test_golden_case(case) -> None:
    result = run_case(case)
    assert result["passed"], f"{result['id']}: {result['detail']}"
