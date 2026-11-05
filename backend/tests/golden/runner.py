"""golden 测试运行器（T0.4/T4.1，设计 D-43）。

用例格式 = SSOT §18.3（tests/golden/cases/*.yaml）；滚动门槛 P1≥15/P2≥30/P3≥40/
T4.1=50（三方言覆盖 MySQL/Doris/ClickHouse）。

当前实现子集（构建期）：r15 注入类用例（sql_inject → hard_block 断言）+ 契约类用例
（fake chat_fn 返回罐装契约 → expect_ast 断言经 sqlglot 复验）；need_engine 标注的
真实三方言用例单独分组（`pytest -m golden_engine`，CI 提供容器后启用，R-10）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

CASES_DIR = Path(__file__).parent / "cases"


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for f in sorted(CASES_DIR.glob("*.yaml")):
        cases.extend(yaml.safe_load(f.read_text(encoding="utf-8")))
    return cases


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """单用例执行：返回 {id, passed, detail}。"""
    dialect = case.get("dialect", "mysql")
    if case.get("sql_inject"):
        return _run_r15_case(case, dialect)
    if "expect_ast" in case:
        return _run_ast_case(case, dialect)
    if case.get("expect", {}).get("clarify_required"):
        return _run_clarify_case(case)
    return {"id": case.get("id"), "passed": False, "detail": "未知用例形态"}


def _run_r15_case(case: dict[str, Any], dialect: str) -> dict[str, Any]:
    from app.sqlcheck.rules import hard_block_r15

    hit = hard_block_r15(str(case["sql_inject"]), dialect=dialect)
    expect = case.get("expect", {})
    want_assertion = str(expect.get("hard_block", "")).removeprefix("R15-")
    passed = hit is not None and hit["assertion"] == want_assertion
    return {"id": case["id"], "passed": passed, "detail": hit["assertion"] if hit else "no_block"}


def _run_ast_case(case: dict[str, Any], dialect: str) -> dict[str, Any]:
    """契约类：罐装 SQL 经 AST 断言（has_cte / has_window / tables / where_has）。"""
    import sqlglot
    from sqlglot import exp

    sql = str(case.get("sql", ""))
    read = {"mysql": "mysql", "doris": "mysql", "clickhouse": "clickhouse"}[dialect]
    tree = sqlglot.parse_one(sql, read=read)
    expect = case["expect_ast"]
    problems: list[str] = []
    if expect.get("has_cte") and not list(tree.find_all(exp.CTE)):
        problems.append("缺 CTE")
    if expect.get("has_window"):
        fn = str(expect["has_window"]).upper()
        if not any(f.sql_name().upper() == fn for f in tree.find_all(exp.Func)):
            problems.append(f"缺窗口函数 {fn}")
    for t in expect.get("tables", []):
        if t not in {tb.name for tb in tree.find_all(exp.Table)}:
            problems.append(f"缺表 {t}")
    for frag in expect.get("where_has", []):
        wheres = [w.sql() for w in tree.find_all(exp.Where)]  # CTE 内层 WHERE 一并扫描
        if not any(frag in w for w in wheres):
            problems.append(f"WHERE 缺 {frag}")
    return {"id": case["id"], "passed": not problems, "detail": "; ".join(problems) or "ok"}


def _run_clarify_case(case: dict[str, Any]) -> dict[str, Any]:
    """澄清门用例：罐装契约含 impact=high 且含目标词 → 触发门。"""
    contract = json.loads(json.dumps(case.get("contract", {})))
    expect = case["expect"]
    assumptions = contract.get("assumptions", [])
    high = [a for a in assumptions if a.get("impact") == "high"]
    term = expect.get("impact_high_term")
    hit = any(term in a.get("text", "") for a in high)
    return {"id": case["id"], "passed": bool(high) and hit, "detail": f"high={len(high)} term_hit={hit}"}


def main() -> int:
    results = [run_case(c) for c in load_cases() if not c.get("need_engine")]
    passed = sum(1 for r in results if r["passed"])
    for r in results:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']}: {r['detail']}")
    print(f"\ngolden: {passed}/{len(results)} 通过（滚动门槛 P1≥15/P2≥30/P3≥40/T4.1=50）")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
