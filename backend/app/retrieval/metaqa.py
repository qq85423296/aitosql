"""元数据问题直通（meta fast-path）。

「库里有哪些表 / 各表是干什么的」这类问题是**库概况问答**，不是业务取数：
答案就在已摄取的元数据里。走 SQL 生成是三重浪费——information_schema 元查询
必中 R13（不在元数据白名单）→ 修正回路振荡 → E2004；且回答慢、还可能幻觉。

本模块：启发式识别 → 从 table_meta / relation 确定性渲染答案（0 次 LLM 调用）。
generate 节点在样例缓存之前调用；命中则以 contract.reasoning 承载答案直通 done。

识别必须保守：宁可漏判（走正常生成），不可误判（吞掉真实取数意图）。
"""

from __future__ import annotations

import re
import sqlite3

# 命中其一即视为元数据问题（词形刻意收窄，只匹配「问表清单/表用途」类）
_META_RES = [
    re.compile(p)
    for p in (
        r"哪些表",
        r"什么表",
        r"表清单",
        r"几张表",
        r"多少[张个]?表",
        r"所有表",
        r"表[都又还]?是?干什[么嘛]",
        r"表[的都有啥].{0,4}用途",
        r"介绍[一下]?[这些各]?表",
        r"表结构.{0,6}有哪些",
        r"tables\s+(are|do)\s+there",
    )
]

# 取数意图排除词：出现则不判元问题（防「各表上个月的销售额」被误吞）
_DATA_INTENT = re.compile(
    r"多少|销售额|订单量|金额|数量|统计|汇总|趋势|环比|同比|排名|top|平均|最大|最小|占比"
)


def is_meta_question(question: str) -> bool:
    """保守判定（启发式兜底，仅 AI 不可用时启用）：元词形命中 且 无取数意图词。"""
    q = question.strip()
    if not q or len(q) > 80:  # 长问句大概率是复合取数问题
        return False
    if _DATA_INTENT.search(q):
        return False
    return any(r.search(q) for r in _META_RES)


def has_data_intent(question: str) -> bool:
    """明显取数意图（直接放行正常生成链路，不花 AI 分类调用）。"""
    return bool(_DATA_INTENT.search(question))


def build_metadata(conn: sqlite3.Connection, datasource_id: int) -> dict | None:
    """库概况元数据载荷（AI 整理的输入）；无表时 None。"""
    conn.row_factory = sqlite3.Row
    ds = conn.execute(
        "SELECT name, dialect, mode, version FROM datasource WHERE id=?", (datasource_id,)
    ).fetchone()
    tables = conn.execute(
        "SELECT name, comment_db, comment_custom, row_estimate, field_count, partition_type "
        "FROM table_meta WHERE datasource_id=? ORDER BY name",
        (datasource_id,),
    ).fetchall()
    if not tables:
        return None
    relations = conn.execute(
        "SELECT from_table, from_col, to_table, to_col, status, confidence "
        "FROM relation WHERE datasource_id=? ORDER BY status, id",
        (datasource_id,),
    ).fetchall()
    return {
        "datasource": {
            "name": ds["name"],
            "dialect": ds["dialect"],
            "mode": "在线" if ds["mode"] == "online" else "离线",
            "version": ds["version"],
        },
        "tables": [
            {
                "name": t["name"],
                "comment": t["comment_custom"] or t["comment_db"] or None,
                "rows": t["row_estimate"],
                "fields": t["field_count"],
                "partition": t["partition_type"],
            }
            for t in tables
        ],
        "relations": [
            {
                "from": f"{r['from_table']}.{r['from_col']}",
                "to": f"{r['to_table']}.{r['to_col']}",
                "status": r["status"],
                "confidence": r["confidence"],
            }
            for r in relations
        ],
    }


def render_meta_answer(conn: sqlite3.Connection, datasource_id: int) -> str | None:
    """从已摄取元数据渲染库概况；无表时返回 None（回退正常生成链路）。"""
    conn.row_factory = sqlite3.Row
    ds = conn.execute("SELECT name, dialect, mode FROM datasource WHERE id=?", (datasource_id,)).fetchone()
    tables = conn.execute(
        "SELECT name, comment_db, comment_custom, row_estimate, field_count, partition_type "
        "FROM table_meta WHERE datasource_id=? ORDER BY name",
        (datasource_id,),
    ).fetchall()
    if not tables:
        return None

    rel_count = conn.execute(
        "SELECT COUNT(*) c FROM relation WHERE datasource_id=? AND status='confirmed'", (datasource_id,)
    ).fetchone()["c"]
    pending_count = conn.execute(
        "SELECT COUNT(*) c FROM relation WHERE datasource_id=? AND status='pending'", (datasource_id,)
    ).fetchone()["c"]

    def _fmt_rows(n: int | None) -> str:
        if n is None:
            return "行数未知"
        if n >= 10_000:
            return f"约 {n / 10_000:g} 万行"
        return f"{n} 行"

    lines = [
        f"当前数据源「{ds['name']}」（{ds['dialect']} · {'在线' if ds['mode'] == 'online' else '离线'}）"
        f"共 {len(tables)} 张表：",
        "",
    ]
    for i, t in enumerate(tables, 1):
        comment = t["comment_custom"] or t["comment_db"] or "（暂无注释，可在数据源页补充）"
        parts = [comment]
        parts.append(_fmt_rows(t["row_estimate"]))
        if t["field_count"]:
            parts.append(f"{t['field_count']} 字段")
        if t["partition_type"]:
            parts.append(f"按{'月' if t['partition_type'] == 'month' else '日'}分区")
        lines.append(f"{i}. {t['name']} — {'，'.join(parts)}")
    lines.append("")
    rel_note = f"已登记表关系 {rel_count} 条"
    if pending_count:
        rel_note += f"（另有 {pending_count} 条推断关系待确认）"
    lines.append(rel_note + "；字段级详情见右侧 Schema 树或数据源页表详情。")
    lines.append("")
    lines.append(
        "说明：库概况类问题直接取自已摄取元数据，不生成 SQL；"
        "要查具体数据（如「各渠道上月销售额」）直接问我即可。"
    )
    return "\n".join(lines)
