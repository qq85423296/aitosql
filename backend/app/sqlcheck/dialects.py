"""能力卡比对（design D-35/D-41，R08）。

能力卡 = configs/dialects/{dialect}.yaml（doris.yaml 默认 = SSOT §8.2 原文）；
R08：函数不在方言能力卡 supports → 黄警（建议替代写法）。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml
from sqlglot import exp

from app import config


@lru_cache(maxsize=8)
def load_card(dialect: str) -> dict[str, Any]:
    """加载方言能力卡；缺失文件返回最小兜底（与 ingest/capability.py 同文件源）。"""
    path = config.dialects_config_dir() / f"{dialect}.yaml"
    if not path.is_file():
        return {"engine": dialect, "detected_version": "", "supports": [], "notes": [], "forbidden": []}
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def explain_form(dialect: str) -> str | None:
    """干跑形态矩阵（SSOT §9.3 扩展）：mysql/json · doris/starrocks 文本 ·
    clickhouse estimate · postgresql json · hive 无稳定行数通道（none）。"""
    return {
        "mysql": "json",
        "doris": "text",
        "starrocks": "text",
        "clickhouse": "estimate",
        "postgresql": "json",
        "hive": None,
    }.get(dialect)


def check_functions(tree: exp.Expr, card: dict[str, Any]) -> list[str]:
    """R08：收集不在能力卡 supports 的函数名（大小写不敏感）。

    误报修复（实测 2026-10-16）：sqlglot 的逻辑/比较/算术运算子（exp.And、
    exp.EQ、exp.Add…）同样是 exp.Func 子类，会被误当「函数 AND」——
    用运算子类块名单排除，只审计真正的 SQL 函数调用。
    """
    supports = {str(s).lower() for s in (card.get("supports") or [])}
    unknown: set[str] = set()
    for func in tree.find_all(exp.Func):
        if isinstance(func, _OPERATOR_CLASSES):
            continue
        name = func.sql_name().lower()
        # 通用标量函数白名单（跨方言基线，不占能力卡名额）
        if name in _BASELINE_FUNCTIONS or name in supports:
            continue
        unknown.add(func.sql_name())
    return sorted(unknown)


# 非「函数」的 exp.Func 子类（逻辑/比较/算术运算子与包装结构）
_OPERATOR_CLASSES: tuple[type[exp.Expression], ...] = (
    exp.And,
    exp.Or,
    exp.Xor,
    exp.Not,
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Is,
    exp.In,
    exp.Like,
    exp.ILike,
    exp.Between,
    exp.Exists,
    exp.Add,
    exp.Sub,
    exp.Mul,
    exp.Div,
    exp.Mod,
    exp.Pow,
    exp.Neg,
    exp.Paren,
)


_BASELINE_FUNCTIONS = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "abs",
    "round",
    "floor",
    "ceil",
    "concat",
    "substring",
    "length",
    "upper",
    "lower",
    "trim",
    "replace",
    "coalesce",
    "ifnull",
    "nullif",
    "case",
    "cast",
    "now",
    "date",
    "year",
    "month",
    "day",
    "datediff",
    "date_format",
    "group_concat",
    "row_number",
    "rank",
    "dense_rank",
    "lag",
    "lead",
    "first_value",
    "last_value",
    "like",
    "in",
    "exists",
    "between",
    "if",
    "distinct",
    # sqlglot 规范化内部名（DATE(x)→TS_OR_DS_TO_DATE 等；模型写的是 DATE，
    # 审计对象应是原始语义而非解析器内部表示，实测误报修复）
    "ts_or_ds_to_date",
    "ts_or_ds_add",
    "ts_or_ds_diff",
    "ts_or_ds_to_datetime",
}
