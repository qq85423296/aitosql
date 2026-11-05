"""verify_result 规则层用例（SSOT §9.2 五行逐项）+ 回退额度判定（D09）。"""

from __future__ import annotations

from app.verify import rules as verify_rules

COLS = [{"name": "cnt", "type": "bigint"}]


def test_all_pass() -> None:
    out = verify_rules.run_rules(
        exec_meta={"rows": 100},
        columns=COLS,
        rows=[[i] for i in range(100)],
        explain_rows=1000,
        partition_pruned=True,
    )
    assert out.passed is True
    assert [c["name"] for c in out.checks] == [
        "行数非零",
        "关键列全NULL",
        "行数偏离估计",
        "汇总对账",
        "分区裁剪",
    ]


def test_zero_rows_fallbackable() -> None:
    out = verify_rules.run_rules(
        exec_meta={"rows": 0}, columns=COLS, rows=[], explain_rows=None, partition_pruned=None
    )
    assert out.passed is False
    assert out.fallback_reason == "行数非零"  # 可消耗回退额度


def test_first_col_all_null() -> None:
    out = verify_rules.run_rules(
        exec_meta={"rows": 3},
        columns=COLS,
        rows=[[None], [None], [None]],
        explain_rows=None,
        partition_pruned=None,
    )
    assert out.passed is False and out.fallback_reason == "关键列全NULL"


def test_deviation_over_1pct() -> None:
    out = verify_rules.run_rules(
        exec_meta={"rows": 5},
        columns=COLS,
        rows=[[i] for i in range(5)],
        explain_rows=100000,
        partition_pruned=None,
    )
    assert out.passed is False and out.fallback_reason == "行数偏离估计"


def test_reconcile_note_only() -> None:
    """汇总对账偏差 >0.1% → unverified 标注（不回退）。"""
    out = verify_rules.run_rules(
        exec_meta={"rows": 1},
        columns=COLS,
        rows=[[110.0]],
        explain_rows=None,
        partition_pruned=None,
        expected_total=100.0,
    )
    assert out.passed is False
    assert out.fallback_reason is None  # 对账类不在回退名单
    recon = next(c for c in out.checks if c["name"] == "汇总对账")
    assert recon["passed"] is False and "偏差" in (recon["note"] or "")


def test_fallback_quota_global_once() -> None:
    """D09：全局共享 1 次——凭证已用即不再放行。"""
    assert verify_rules.decide_fallback(False, "行数非零") is True
    assert verify_rules.decide_fallback(True, "行数非零") is False
    assert verify_rules.decide_fallback(False, None) is False
