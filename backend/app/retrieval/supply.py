"""schema 供给（design D-07/D-33 / SSOT §5.2 supply_schema，BR-2）。

执行序：库表 ≤30 或估算 ≤预算 → full；否则双路召回（FTS top-12 + 向量 top-4，
FTS 命中 ≥6 表不触发向量）→ RRF k=60 按表去重 → `rrf_score DESC, table_name ASC`
确定性排序 → 预算装配；模式切换滞后带 55-65% 保持上次模式；检索失败降级 full 截断；
need_tables 二次供给仅 1 次（resupply_used 由图状态持有，本函数纯供给）。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from app import tokens as app_tokens
from app.retrieval import feedback, index, keyword, vector
from app.store import datasources as store_datasources
from app.store import settings as store_settings
from app.text import lexical_terms, sanitize_text

logger = logging.getLogger("aitosql.retrieval.supply")

RRF_K = 60
FULL_TABLE_THRESHOLD = 30  # 库表 ≤30 → full 模式
FTS_SKIP_VECTOR_MIN = 6  # FTS 命中 ≥6 表不触发向量
HYSTERESIS_LO = 55  # 滞后带下沿（%）
HYSTERESIS_HI = 65  # 滞后带上沿（%）


@dataclass
class SupplyResult:
    """字段名对齐 QueryState（design 段 5 签名）。"""

    schema_text: str
    hit_tables: list[str] = field(default_factory=list)
    schema_mode: str = "full"  # full|retrieval
    trace: dict = field(default_factory=dict)


def render_table_text(
    conn: sqlite3.Connection,
    ds_id: int,
    table_name: str,
    *,
    enum_top_n: int,
    partition_hint: bool,
    question: str = "",
    wide_threshold: int = 0,
    wide_keep: int = 0,
    pruned: dict[str, dict[str, int]] | None = None,
) -> str:
    """单表注入文本：注释（人工优先）+ 字段行 + 键标注 + 维度枚举（≤top_n）。

    宽表（字段数 > wide_threshold）按问题相关性裁剪（演进 #2 列级召回）：
    必留主键/分区键/分桶键/标准绑定列/已确认关联键，其余按词项重叠取 top。
    """
    table = store_datasources.get_table_by_name(conn, ds_id, table_name)
    if table is None:
        return ""
    cols = store_datasources.query_columns(conn, table.id)
    wide_total = 0
    if wide_threshold > 0 and len(cols) > wide_threshold:
        wide_total = len(cols)
        keep_ids = _wide_prune(
            conn, ds_id, table_name, cols, question=question, keep=max(1, wide_keep)
        )
        cols = [c for c in cols if c.id in keep_ids]
        if pruned is not None:
            pruned[table_name] = {"total": wide_total, "kept": len(cols)}
    # 0011：有模式名时标题限定 schema.table——模型据此生成正确的模式名前缀
    qualified = f"{table.schema_name}.{table.name}" if table.schema_name else table.name
    title = f"表 {sanitize_text(qualified)}"
    comment = sanitize_text(table.comment_custom or table.comment_db)
    if comment:
        title += f"（{comment}）"
    lines = [title]
    for c in cols:
        line = f"  - {c.name} {c.type}"
        cmt = sanitize_text(c.comment_custom or c.comment_db)
        if cmt:
            line += f"  -- {cmt}"
        badges = []
        if c.is_pk:
            badges.append("主键")
        if c.is_partition:
            grain = {"month": "按月", "day": "按日"}.get(table.partition_type or "", "")
            badges.append(f"分区键·{grain}，查询大表请带时间范围" if grain and partition_hint else "分区键")
        if c.is_bucket:
            badges.append("分桶键")
        if badges:
            line += f"  [{'|'.join(badges)}]"
        if c.field_standard_id is not None:
            standard = conn.execute(
                "SELECT * FROM field_standard WHERE id=?", (c.field_standard_id,)
            ).fetchone()
            if standard:
                line += f" 标准:{sanitize_text(standard['name'])} 单位:{sanitize_text(standard['unit'])}"
                dimension = standard["dimension_standard_id"]
                if dimension is not None:
                    values = conn.execute(
                        "SELECT value,label FROM dimension_value "
                        "WHERE dimension_id=? ORDER BY sort,id LIMIT ?",
                        (dimension, max(0, enum_top_n)),
                    ).fetchall()
                    line += " 枚举:" + ",".join(
                        f"{sanitize_text(v[0])}={sanitize_text(v[1])}" for v in values
                    )
        lines.append(line)
    if partition_hint and table.partition_type:
        lines.append(f"  [分区表·{table.partition_type} 粒度：WHERE 必须包含分区键]")
    if wide_total:
        lines.append(f"  [宽表：共 {wide_total} 列，按问题相关性注入 {len(cols)} 列]")
    return "\n".join(lines)


def _wide_prune(
    conn: sqlite3.Connection,
    ds_id: int,
    table_name: str,
    cols: list,
    *,
    question: str,
    keep: int,
) -> set[int]:
    """宽表字段裁剪：必留键/标准/关联列，其余按问题词项重叠取 top（确定性排序）。"""
    mandatory = {
        c.id
        for c in cols
        if c.is_pk or c.is_partition or c.is_bucket or c.field_standard_id is not None
    }
    rel_rows = conn.execute(
        "SELECT from_table, from_col, to_table, to_col FROM relation"
        " WHERE datasource_id=? AND status='confirmed' AND (from_table=? OR to_table=?)",
        (ds_id, table_name, table_name),
    ).fetchall()
    join_cols: set[str] = set()
    for from_t, from_c, to_t, to_c in rel_rows:
        if from_t == table_name:
            join_cols.add(from_c)
        if to_t == table_name:
            join_cols.add(to_c)
    mandatory |= {c.id for c in cols if c.name in join_cols}
    wanted = lexical_terms(question)
    scored: list[tuple[int, int, int, int]] = []
    for c in cols:
        if c.id in mandatory:
            continue
        comment = (c.comment_custom or c.comment_db) or ""
        overlap = len(wanted & lexical_terms(f"{c.name} {c.type} {comment}"))
        if overlap:
            scored.append((-overlap, -(1 if comment else 0), c.id, c.id))
    scored.sort()
    budget = max(0, keep - len(mandatory))
    return mandatory | {item[3] for item in scored[:budget]}


def _rrf_fuse(
    fts_hits: list[tuple[str, float]],
    vec_hits: list[tuple[str, float]],
    boosts: dict[str, float] | None = None,
    *,
    boost_weight: float = 0.2,
) -> list[str]:
    """RRF k=60 融合 + 生成反馈乘性加权：score *= (1 + w·log1p(success_count))。

    排序键 (score DESC, table_name ASC) 保持确定性（验收锚点）；log 刻度使
    高频成功表优势收敛，不会永久压制新召回表（演进 #1）。
    """
    score: dict[str, float] = {}
    for rank, (name, _s) in enumerate(fts_hits, start=1):
        score[name] = score.get(name, 0.0) + 1.0 / (RRF_K + rank)
    for rank, (name, _d) in enumerate(vec_hits, start=1):
        score[name] = score.get(name, 0.0) + 1.0 / (RRF_K + rank)
    if boosts:
        for name, boost in boosts.items():
            if name in score and boost > 0:
                score[name] *= 1.0 + boost_weight * boost
    return sorted(score, key=lambda n: (-score[n], n))


# F-2：auto 模式全量 token 估算缓存（指纹 = 表/字段计数 + MAX(updated_at) + 渲染参数；
# upsert/update 均刷新 updated_at，指纹变化即失效）。个人版单进程，指纹变化时全清防膨胀。
_EST_CACHE: dict[tuple, int] = {}


def _meta_fingerprint(
    conn: sqlite3.Connection, ds_id: int, *, enum_top_n: int, partition_hint: bool
) -> tuple:
    revision = index.status(conn, ds_id).get("revision", -1)
    return (ds_id, revision, enum_top_n, partition_hint)


def _estimate_full_tokens(
    conn: sqlite3.Connection, ds_id: int, *, enum_top_n: int, partition_hint: bool
) -> int:
    fp = _meta_fingerprint(conn, ds_id, enum_top_n=enum_top_n, partition_hint=partition_hint)
    key = (ds_id, fp)
    cached = _EST_CACHE.get(key)
    if cached is not None:
        return cached
    tables = store_datasources.query_tables(conn, ds_id)
    tokens = sum(
        app_tokens.estimate_tokens(
            render_table_text(conn, ds_id, t.name, enum_top_n=enum_top_n, partition_hint=partition_hint)
        )
        for t in tables
    )
    _EST_CACHE.clear()
    _EST_CACHE[key] = tokens
    return tokens


def supply(
    conn: sqlite3.Connection,
    *,
    question: str,
    datasource_id: int,
    budget_tokens: int,
    mode_setting: str = "auto",
    last_mode: str | None = None,
    need_tables: list[str] | None = None,
) -> SupplyResult:
    enum_n = int(store_settings.get_setting(conn, "schema.enum_top_n"))
    hint = bool(store_settings.get_setting(conn, "feature.partition_hint"))
    wide_threshold = int(store_settings.get_setting(conn, "schema.wide_table_columns"))
    wide_keep = int(store_settings.get_setting(conn, "schema.wide_table_keep"))
    names = sorted(t.name for t in store_datasources.query_tables(conn, datasource_id))
    required = list(dict.fromkeys(n for n in need_tables or [] if n in names))
    mode = mode_setting
    if mode == "auto":
        estimate = _estimate_full_tokens(conn, datasource_id, enum_top_n=enum_n, partition_hint=hint)
        ratio = estimate / max(1, budget_tokens) * 100
        if (len(names) <= FULL_TABLE_THRESHOLD and estimate <= budget_tokens) or ratio <= HYSTERESIS_LO:
            mode = "full"
        elif ratio < HYSTERESIS_HI:
            mode = last_mode or "retrieval"
        else:
            mode = "retrieval"
    fts_hits: list[tuple[str, float]] = []
    vec_hits: list[tuple[str, float]] = []
    boost_map: dict[str, float] = {}
    fallback = None
    if mode == "full":
        ranked = names
    else:
        try:
            index.ensure_fts(conn, datasource_id)
            fts_hits = keyword.search_tables(conn, datasource_id, question, top=12)
            vec_hits = vector.search_tables(conn, datasource_id, question, top=4)
            boost_map = feedback.boosts(conn, datasource_id)
            ranked = [n for n in _rrf_fuse(fts_hits, vec_hits, boost_map) if n in names]
        except Exception as exc:
            logger.warning("Retrieval fallback: %s", type(exc).__name__)
            ranked = []
            fallback = "retrieval_error"
        if not ranked:
            ranked = names
            fallback = fallback or "no_hits"
    relations = conn.execute(
        "SELECT from_table,from_col,to_table,to_col FROM relation"
        " WHERE datasource_id=? AND status='confirmed' ORDER BY from_table,to_table,id",
        (datasource_id,),
    ).fetchall()
    seeds = list(dict.fromkeys([*required, *ranked[:3]]))
    graph: dict[str, set[str]] = {}
    for row in relations:
        graph.setdefault(row[0], set()).add(row[2])
        graph.setdefault(row[2], set()).add(row[0])
    bridges: list[str] = []
    if seeds and mode != "full":
        for target in seeds[1:3]:
            paths = [[seeds[0]]]
            visited = {seeds[0]}
            for path in paths:
                if path[-1] == target:
                    bridges.extend(n for n in path[1:-1] if n not in seeds and n not in bridges)
                    break
                if len(path) >= 4:
                    continue
                for neighbor in sorted(graph.get(path[-1], set()) - visited):
                    visited.add(neighbor)
                    paths.append([*path, neighbor])
        for seed in seeds:
            bridges.extend(n for n in sorted(graph.get(seed, set())) if n not in seeds and n not in bridges)
    ranked = list(dict.fromkeys([*required, *ranked[:2], *bridges[:4], *ranked]))
    parts: list[str] = []
    hit: list[str] = []
    omitted: list[str] = []
    budget = max(0, budget_tokens)

    def fits(text: str) -> bool:
        return app_tokens.estimate_tokens("\n\n".join([*parts, text])) <= budget

    pruned: dict[str, dict[str, int]] = {}
    for name in ranked:
        text = render_table_text(
            conn,
            datasource_id,
            name,
            enum_top_n=enum_n,
            partition_hint=hint,
            question=question,
            wide_threshold=wide_threshold,
            wide_keep=wide_keep,
            pruned=pruned,
        )
        if not text:
            continue
        if not fits(text):
            omitted.append(name)
            continue
        parts.append(text)
        hit.append(name)
    for row in relations:
        if row[0] in hit and row[2] in hit:
            text = (
                f"已确认关系: {sanitize_text(row[0])}.{sanitize_text(row[1])} = "
                f"{sanitize_text(row[2])}.{sanitize_text(row[3])}"
            )
            if fits(text):
                parts.append(text)
    text = "\n\n".join(parts)
    return SupplyResult(
        text,
        hit,
        mode,
        {
            "fts_tables": [n for n, _ in fts_hits],
            "vector_tables": [n for n, _ in vec_hits],
            "feedback_tables": [n for n in hit if boost_map.get(n)],
            "wide_pruned": pruned,
            "join_expansion": bridges[:4],
            "required_tables": required,
            "omitted_tables": omitted,
            "budget_tokens": budget,
            "used_tokens": app_tokens.estimate_tokens(text),
            "fallback": fallback,
            "revision": index.status(conn, datasource_id).get("revision"),
        },
    )
