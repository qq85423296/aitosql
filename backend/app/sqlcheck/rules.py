"""AST 规则 R01-R14 + R15 五断言硬阻断（SSOT §9.1 逐行 / design D-03/D-04）。

R15 断言形态以 `.roundtable/evidence/probe_r2_out.txt`（sqlglot 30.18 实测）为准：
- B1 多语句：parse() 过滤 None 后语句数 ≠1（`SELECT 1;;SELECT 2` → [Select,None,Select]）；
- B2 解析失败 fail-closed：INTO OUTFILE/DUMPFILE 全语序 / LOAD DATA [LOCAL] INFILE /
  INSTALL PLUGIN / DO / HANDLER（KILL QUERY 实测亦 PARSE_FAIL，落此断言）；
- B3 exp.Into 显式断言：唯一可解析形态 `INTO @var`，枚举文件变体防升级漂移；
- B4 exp.Describe 且 style=ANALYZE 一律阻断（含包纯 SELECT）；
- B5 契约外语句族：CALL→exp.Command 由 B5 拦；USE/SET/KILL/GRANT/SYSTEM 可解析族
  留 R01 红 + 确认门兜底。
分方言文案 = SSOT §12.7 R15 错误文案五模板（E3010 正文）。
"""

from __future__ import annotations

import hashlib
from typing import Any, TypedDict

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.sqlcheck import dialects as dialect_mod

# ---------- 类型 ----------

DIALECT_MAP = {"mysql": "mysql", "doris": "mysql", "clickhouse": "clickhouse"}


class RiskItem(TypedDict):
    rule_id: str  # R01-R15
    level: str  # 'red' | 'yellow'
    message: str  # 用户可读（分方言文案）


class MetaView(TypedDict, total=False):
    """元数据视图（store/datasources.build_meta_view 产出，显式参数传入）。"""

    columns: dict[str, str]
    row_estimate: int
    partition_cols: list[str]
    pk_cols: list[str]


BIG_TABLE_ROWS = 1_000_000  # R09/R12 大表阈值（row_estimate > 100 万）
NEST_LIMIT = 3  # R11 子查询嵌套上限

# ---------- R15 分方言文案（SSOT §12.7 原文五模板） ----------

R15_MESSAGES = {
    "B1": "检测到多条语句。为保证可复核性，每次只执行一条 SQL，请拆分后分别提问",
    "B2": "语句无法安全解析（可能含文件读写/插件/存储过程类语法），已阻断",
    "B3_mysql": "INTO OUTFILE/DUMPFILE 会写服务器文件，已阻断；导出请用结果卡「导出 CSV」",
    "B3_doris": "INTO OUTFILE s3:// 属于出境通道，已阻断",
    "B4": "EXPLAIN ANALYZE 会真实执行语句，已禁用；干跑仅用静态 EXPLAIN",
    "B5": "存储过程/DO 调用不透明、无法校验，已硬阻断",
}


def _d(dialect: str) -> str:
    return DIALECT_MAP.get(dialect, dialect)


def _r15_message(assertion: str, dialect: str) -> str:
    if assertion == "B3":
        return R15_MESSAGES["B3_doris"] if dialect == "doris" else R15_MESSAGES["B3_mysql"]
    return R15_MESSAGES[assertion]


def hard_block_r15(sql: str, *, dialect: str) -> dict[str, str] | None:
    """R15 五断言（优先级高于 R01-R14）；命中返回 {rule_id, level, message, assertion}。"""
    try:
        statements = [s for s in sqlglot.parse(sql, read=_d(dialect)) if s is not None]
    except ParseError:
        return {"rule_id": "R15", "level": "red", "message": _r15_message("B2", dialect), "assertion": "B2"}
    except Exception:
        return {"rule_id": "R15", "level": "red", "message": _r15_message("B2", dialect), "assertion": "B2"}
    if len(statements) != 1:  # B1（含 0 条与多条；多语句实测 [Select,None,Select]）
        return {"rule_id": "R15", "level": "red", "message": _r15_message("B1", dialect), "assertion": "B1"}
    stmt = statements[0]
    # B3：exp.Into 显式断言（唯一可解析形态 INTO @var；文件变体实测 PARSE_FAIL 归 B2）
    if stmt.find(exp.Into) is not None:
        return {"rule_id": "R15", "level": "red", "message": _r15_message("B3", dialect), "assertion": "B3"}
    # B4：EXPLAIN ANALYZE（Describe.style=ANALYZE，含包纯 SELECT）；
    # clickhouse 方言下 sqlglot 30.18 退化为不透明 Command（实测），Command 形态同步断言
    for describe in stmt.find_all(exp.Describe):
        if str(describe.args.get("style") or "").upper() == "ANALYZE":
            return {
                "rule_id": "R15",
                "level": "red",
                "message": _r15_message("B4", dialect),
                "assertion": "B4",
            }
    for command in stmt.find_all(exp.Command):
        this = str(command.this or "").upper()
        if this == "EXPLAIN" and "ANALYZE" in str(command.args.get("expression") or "").upper():
            return {
                "rule_id": "R15",
                "level": "red",
                "message": _r15_message("B4", dialect),
                "assertion": "B4",
            }
        # B5：契约外语句族 CALL/DO（CALL 解析为不透明 exp.Command）
        if this in {"CALL", "DO"}:
            return {
                "rule_id": "R15",
                "level": "red",
                "message": _r15_message("B5", dialect),
                "assertion": "B5",
            }
    # KILL 实测解析为 exp.Kill（探针环境 PARSE_FAIL；运行时形态变化按同语义
    # 失败关闭归 B2：语句族不受支持即阻断）
    if stmt.find(exp.Kill) is not None:
        return {"rule_id": "R15", "level": "red", "message": _r15_message("B2", dialect), "assertion": "B2"}
    return None


# ---------- auto_limit AST 注入（R04，执行序铁律第 2 步） ----------


def is_pure_select(stmt: exp.Expr) -> bool:
    return isinstance(stmt, exp.Select | exp.Union)


def is_pure_select_sql(sql: str, *, dialect: str) -> bool:
    """纯 SELECT 判定（SQL 串形态；execute CSV 落盘/confirm_gate 共用）。解析失败 → False。"""
    try:
        return is_pure_select(sqlglot.parse_one(sql, read=_d(dialect)))
    except Exception:
        return False


def inject_auto_limit(sql: str, *, dialect: str, limit: int) -> str:
    """纯 SELECT 无 LIMIT 追加（AST 注入，时机先于校验——所验即所执）。
    pretty=True 重序列化：保住展示排版（实测模型单行输出被 AST 塌成一行，2026-10-17）。"""
    tree = sqlglot.parse_one(sql, read=_d(dialect))
    if not is_pure_select(tree):
        return sql
    if tree.args.get("limit") is not None:
        return sql
    tree.set("limit", exp.Limit(expression=exp.Literal.number(str(limit))))
    return tree.sql(dialect=_d(dialect), pretty=True)


def strip_limit(sql: str, *, dialect: str) -> str:
    """AST 去 LIMIT 副本（干跑专用；干跑串永不进 execute，§9.3-2）。"""
    tree = sqlglot.parse_one(sql, read=_d(dialect))
    if tree.args.get("limit") is not None:
        tree.set("limit", None)
    return tree.sql(dialect=_d(dialect))


def normalize_sql(sql: str, *, dialect: str) -> str:
    """标识符归一（normalize_identifiers → qualify_tables）；失败原样返回。
    pretty=True：归一化展示串保持多行排版（SQL 卡可读性，所验即所执串不变语义）。"""
    try:
        from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
        from sqlglot.optimizer.qualify_tables import qualify_tables

        tree = sqlglot.parse_one(sql, read=_d(dialect))
        tree = normalize_identifiers(tree, dialect=_d(dialect))
        tree = qualify_tables(tree)
        return tree.sql(dialect=_d(dialect), pretty=True)
    except Exception:
        return sql


def fingerprint(sql: str, *, dialect: str) -> str:
    """SQL 归一化指纹（振荡检测，D-13）：脱字面值 → 归一 → sha256 短指纹。"""
    from app.sqlcheck import clean

    masked = clean.mask_sql_literals(sql, _d(dialect))
    normalized = normalize_sql(masked, dialect=dialect)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ---------- R01-R14（处置表 = SSOT §9.1 逐行） ----------

_ALLOWED_STMT = (exp.Select, exp.Union, exp.Insert, exp.Update, exp.Delete)


def _stmt(sql: str, dialect: str) -> exp.Expr:
    return sqlglot.parse_one(sql, read=_d(dialect))


def _risk(rule_id: str, level: str, message: str) -> RiskItem:
    return {"rule_id": rule_id, "level": level, "message": message}


def check_all(
    sql: str,
    *,
    dialect: str,
    caps: dict[str, Any],
    meta: dict[str, dict[str, Any]],
    ds_mode: str = "online",
    db_name: str | None = None,
    auto_limit: int = 1000,
    scan_warn_rows: int = 5_000_000,
) -> list[RiskItem]:
    """R01-R14 全量检查（R15 由 hard_block_r15 先行，不在此重复）。

    caps = 能力卡（supports/forbidden）；meta = 元数据视图；ds_mode 决定 R13 级别
    （online 红 / offline 黄警照常交付，D04）。
    """
    stmt = _stmt(sql, dialect)
    risks: list[RiskItem] = []
    write = isinstance(stmt, exp.Insert | exp.Update | exp.Delete) or bool(stmt.args.get("replace"))

    # R01 语句类型白名单外（USE/SET/KILL/GRANT/SYSTEM 等可解析族）
    if not isinstance(stmt, _ALLOWED_STMT):
        risks.append(_risk("R01", "red", f"语句类型白名单外（{type(stmt).__name__}），需确认门裁决"))
    # R05 DROP/TRUNCATE/ALTER/GRANT 等 DDL/DCL
    ddl_dcl: tuple[type[exp.Expr], ...] = (
        exp.Drop,
        exp.Alter,
        exp.Grant,
    )  # 30.18 无 exp.AlterTable，ALTER→exp.Alter
    truncate_cls = getattr(exp, "TruncateTable", None)
    if truncate_cls is not None:
        ddl_dcl = (*ddl_dcl, truncate_cls)
    if isinstance(stmt, ddl_dcl):
        risks.append(_risk("R05", "red", "DDL/DCL 操作（DROP/TRUNCATE/ALTER/GRANT），需确认门裁决"))
    if isinstance(stmt, exp.Update | exp.Delete):
        where = stmt.args.get("where")
        # R02 UPDATE/DELETE 无 WHERE
        if where is None:
            risks.append(_risk("R02", "red", "UPDATE/DELETE 无 WHERE，将作用全表"))
        else:
            # R03 未用主键条件
            table_names = [t.name for t in stmt.find_all(exp.Table)]
            pk_cols = {c for t in table_names for c in meta.get(t, {}).get("pk_cols", [])}
            where_cols = {c.name for c in where.find_all(exp.Column)}
            if pk_cols and not (pk_cols & where_cols):
                risks.append(_risk("R03", "yellow", "UPDATE/DELETE 未使用主键条件（附影响行数估算）"))
    # R06 非纯 SELECT（写操作，含 REPLACE INTO）→ 触发 confirm_gate
    if write:
        msg = "写操作（非纯 SELECT，含 REPLACE INTO），触发确认门"
        if dialect == "clickhouse":
            msg = (
                "当前数据源为只读（ch_readonly=1），执行将被服务端拒绝；"
                "如需写操作请至设置页关闭该数据源只读开关"
            )  # §9.4 CH 只读联动文案
        risks.append(_risk("R06", "red", msg))
    # R04 纯 SELECT 无 LIMIT（auto_limit AST 注入先行；此处作信息提示）
    if is_pure_select(stmt) and stmt.args.get("limit") is None:
        risks.append(_risk("R04", "yellow", f"纯 SELECT 无 LIMIT，已自动追加 LIMIT {auto_limit}"))
    tables = list(stmt.find_all(exp.Table))
    # R07 访问未注册数据源/跨库
    for t in tables:
        if t.db and db_name and t.db != db_name:
            risks.append(_risk("R07", "yellow", f"跨库访问 {t.db}.{t.name}（当前库 {db_name}）"))
    # R13 表/字段不在元数据（幻觉标识符；先归一后比对由 validate 序保证）
    unknown_tables = [t.name for t in tables if meta and t.name not in meta]
    r13_level = "red" if ds_mode == "online" else "yellow"
    for name in unknown_tables:
        risks.append(
            _risk(
                "R13",
                r13_level,
                f"表 {name} 不在元数据（疑似幻觉标识符）"
                + ("；离线库降级黄警照常交付" if ds_mode != "online" else ""),
            )
        )
    if meta and not unknown_tables:
        # Q-8：别名感知字段核查（修复前 `t.find_all(exp.Column)` 对 Table 节点恒为空
        # ——列不挂在 Table 子树，旧实现为死代码；现按语句级遍历 + 别名映射归因）
        alias_map: dict[str, str] = {}
        for t in tables:
            alias_map.setdefault(t.name, t.name)
            if t.alias:
                alias_map.setdefault(t.alias, t.name)
        # 投影输出别名（SELECT ... AS x）：ORDER BY/GROUP BY 对别名的裸引用不是真实字段，
        # 误报实例：SELECT SUM(amount) AS total_amount ... ORDER BY total_amount →
        # 「字段 fin_monthly.total_amount 不在元数据」。全语句范围别名并集保守跳过。
        output_aliases: set[str] = set()
        for sel in stmt.find_all(exp.Select):
            for e in sel.expressions:
                if getattr(e, "alias", None):
                    output_aliases.add(e.alias)
        seen_cols: set[tuple[str, str]] = set()
        for c in stmt.find_all(exp.Column):
            if not c.table and c.name in output_aliases:
                continue  # 输出别名引用，跳过
            ref: str | None
            if c.table:
                ref = alias_map.get(c.table)
                if ref is None:
                    continue  # 子查询别名等无法归因（保守跳过，不报假警）
            elif len(tables) == 1:
                ref = tables[0].name
            else:
                continue  # 多表裸列无法归因
            if c.name == "*":
                continue
            known_cols = meta.get(ref, {}).get("columns", {})
            if known_cols and c.name not in known_cols and (ref, c.name) not in seen_cols:
                seen_cols.add((ref, c.name))
                risks.append(_risk("R13", r13_level, f"字段 {ref}.{c.name} 不在元数据"))
    # R08 函数不在能力卡 supports
    for fn in dialect_mod.check_functions(stmt, caps):
        risks.append(_risk("R08", "yellow", f"函数 {fn} 不在 {dialect} 能力卡 supports，建议替代写法"))
    has_star = any(isinstance(s, exp.Star) for s in stmt.find_all(exp.Column)) or any(
        isinstance(e, exp.Star) for e in (stmt.expressions if isinstance(stmt, exp.Select) else [])
    )
    for t in tables:
        info = meta.get(t.name) or {}
        rows_est = int(info.get("row_estimate") or 0)
        # R09 大表 SELECT *
        if has_star and rows_est > BIG_TABLE_ROWS:
            risks.append(_risk("R09", "yellow", f"大表 {t.name}（约 {rows_est} 行）SELECT *，建议列裁剪"))
        # R12 大表 LIKE 前缀通配
        if rows_est > BIG_TABLE_ROWS:
            for like in stmt.find_all(exp.Like):
                lit = like.args.get("expression")
                if isinstance(lit, exp.Literal) and str(lit.this).startswith("%"):
                    risks.append(_risk("R12", "yellow", f"大表 {t.name} LIKE 前缀通配，可能全表扫描"))
        # R14 分区表 WHERE 未含分区键
        part_cols = info.get("partition_cols") or []
        if part_cols:
            where = stmt.args.get("where")
            where_cols = {c.name for c in where.find_all(exp.Column)} if where is not None else set()
            if not (set(part_cols) & where_cols):
                risks.append(_risk("R14", "yellow", f"分区表 {t.name} WHERE 未含分区键，可能全分区扫描"))
    # R10 JOIN 缺 ON（笛卡尔积；NATURAL→method、ARRAY/CROSS→kind 实测字段）
    for join in stmt.find_all(exp.Join):
        kind = str(join.args.get("kind") or "").upper()
        method = str(join.args.get("method") or "").upper()
        if kind in {"CROSS", "ARRAY"} or method == "NATURAL":
            continue
        if join.args.get("on") is None and join.args.get("using") is None:
            risks.append(_risk("R10", "red", "JOIN 缺 ON（笛卡尔积），进修正回路"))
    # R11 子查询嵌套 >3 层
    depth = _subquery_depth(stmt)
    if depth > NEST_LIMIT:
        risks.append(_risk("R11", "yellow", f"子查询嵌套 {depth} 层 >{NEST_LIMIT}，建议 CTE 改写"))
    return risks


def _subquery_depth(node: exp.Expr, depth: int = 0) -> int:
    best = depth
    for sub in node.find_all(exp.Subquery):
        best = max(best, _subquery_depth(sub.this, depth + 1))
    return best


def has_write_risk(risks: list[RiskItem]) -> bool:
    """R06 命中判定（confirm_gate 触发条件）。"""
    return any(r["rule_id"] == "R06" for r in risks)


def has_correctable_risk(risks: list[RiskItem]) -> bool:
    """可修正错误判定（R10/R13-online → correct 回路）。"""
    return any(r["rule_id"] in ("R10", "R13") and r["level"] == "red" for r in risks)


def is_aggregated(sql: str) -> bool:
    if not sql:
        return False
    try:
        tree = sqlglot.parse_one(sql)
        return any(tree.find_all(exp.Group)) or any(tree.find_all(exp.AggFunc))
    except Exception:
        return False


def tables_in_sql(sql: str, *, dialect: str) -> list[str]:
    """语句引用的物理表名（去重、字典序确定性输出；CTE 名不算物理表，解析失败 → []）。

    生成反馈闭环（retrieval_feedback 回写）与审计日志共用；sqlglot 唯一入口契约（D3）
    要求提取逻辑收口在 sqlcheck，调用方（graph/api）拿名字列表即可。
    """
    try:
        tree = sqlglot.parse_one(sql, read=_d(dialect))
    except Exception:
        return []
    cte_names = {c.alias for c in tree.find_all(exp.CTE)}
    out: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name
        if name and name not in cte_names:
            out.add(name)
    return sorted(out)
