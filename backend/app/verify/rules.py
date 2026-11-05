"""verify_result 规则层（SSOT §9.2 逐行，全过才 pass）。

| 检查 | 不过处理 |
| 行数为 0 | 消耗回退额度（放宽最近一个枚举/范围过滤）；仍 0 → unverified |
| 关键列（SELECT 首列）全 NULL | 消耗回退额度 |
| 行数 < EXPLAIN 估计的 1% | 消耗回退额度（疑似条件过严） |
| 汇总对账（可行时） | 偏差 >0.1% → unverified 标注 |
| 分区裁剪确认 | 仅记录，不回退 |

回退额度全局共享 1 次（D09）：以 QueryState.verify_fallback_used 为准，
本模块不自行计数——decide_fallback 只看额度凭证。
"""

from __future__ import annotations

from typing import Any, TypedDict


class VerifyCheck(TypedDict):
    name: str
    passed: bool
    note: str | None


class VerifyOutcome:
    def __init__(self, checks: list[VerifyCheck], passed: bool, fallback_reason: str | None) -> None:
        self.checks = checks
        self.passed = passed
        self.fallback_reason = fallback_reason  # 首个可回退失败项（进 error_history）


# 可消耗回退额度的检查项（其余仅记录/标注）
_FALLBACKABLE = {"行数非零", "关键列全NULL", "行数偏离估计"}


def run_rules(
    *,
    exec_meta: dict[str, Any],
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    explain_rows: int | None,
    partition_pruned: bool | None,
    expected_total: float | None = None,
    aggregated: bool = False,
) -> VerifyOutcome:
    """规则层 5 检查。

    exec_meta: {rows, scan_rows, duration_ms, data_max_time}；explain_rows 来自干跑；
    partition_pruned：R14 侧记录的「WHERE 是否命中分区键」（None=非分区表，仅记录）；
    expected_total：契约侧对账期望值（可行时才对账，缺省跳过并注明）。
    """
    checks: list[VerifyCheck] = []
    rows_n = int(exec_meta.get("rows") or 0)

    # 1) 行数为 0
    checks.append({"name": "行数非零", "passed": rows_n > 0, "note": None if rows_n > 0 else "结果 0 行"})
    # 2) 关键列（SELECT 首列）全 NULL
    first_col_all_null = bool(rows) and all(not r or r[0] is None for r in rows)
    checks.append(
        {
            "name": "关键列全NULL",
            "passed": not first_col_all_null,
            "note": "首列全 NULL" if first_col_all_null else None,
        }
    )
    # 3) 行数 < EXPLAIN 估计的 1%（聚合查询结果行数与扫描估计天然不可比，跳过——
    #    实测误报：GROUP BY 渠道 4 行 vs 扫描估计 2520 → 白耗一轮修正）
    deviation_hit = (
        not aggregated and explain_rows is not None and explain_rows > 0 and rows_n < explain_rows * 0.01
    )
    checks.append(
        {
            "name": "行数偏离估计",
            "passed": not deviation_hit,
            "note": (
                f"行数 {rows_n} < 估计 {explain_rows} 的 1%，疑似条件过严"
                if deviation_hit
                else ("聚合查询，偏离检查不适用" if aggregated else None)
            ),
        }
    )
    # 4) 汇总对账（可行时）：偏差 >0.1% → unverified 标注（不回退）
    if expected_total is not None and expected_total > 0:
        actual = _first_numeric(rows)
        if actual is not None:
            dev = abs(actual - expected_total) / expected_total
            checks.append(
                {
                    "name": "汇总对账",
                    "passed": dev <= 0.001,
                    "note": None if dev <= 0.001 else f"偏差 {dev:.4%}",
                }
            )
        else:
            checks.append({"name": "汇总对账", "passed": True, "note": "结果非数值汇总，跳过"})
    else:
        checks.append({"name": "汇总对账", "passed": True, "note": "无对账基线，跳过"})
    # 5) 分区裁剪确认（仅记录，不回退）
    checks.append(
        {
            "name": "分区裁剪",
            "passed": True,
            "note": (
                "WHERE 命中分区键"
                if partition_pruned
                else ("未命中分区键（可能全分区扫描）" if partition_pruned is False else "非分区表，跳过")
            ),
        }
    )

    passed = all(c["passed"] for c in checks)
    fallback_reason = next(
        (c["name"] for c in checks if not c["passed"] and c["name"] in _FALLBACKABLE), None
    )
    return VerifyOutcome(checks, passed, fallback_reason)


def decide_fallback(verify_fallback_used: bool, fallback_reason: str | None) -> bool:
    """回退额度判定：全局唯一额度（D09）；无凭证且无额度 → False。"""
    return bool(fallback_reason) and not verify_fallback_used


def _first_numeric(rows: list[list[Any]]) -> float | None:
    for row in rows:
        if row and isinstance(row[0], int | float):
            return float(row[0])
    return None
