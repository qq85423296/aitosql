"""样例库 DAO + stale 治理 + semantic_rev 缓存命中（D-15 / SSOT §12.1，BR-13）。

- question_norm 归一化：小写 / 去空白 / 去标点；
- 缓存命中三条件：question_norm 精确 ∧ status=on ∧ semantic_rev 相等；
- semantic_rev = sha256(schema_rev, clarify_pref_rev, dialect_card_hash, sem_bind_rev,
  prompt_sys_hash)，各 rev 懒惰失效（变更才重算——本实现按读取时重算，等效惰性求值）；
- schema 变更（重摄取 diff）→ 引用消失字段的样例置 stale（stale_reason 记字段名）；
- 复验走 EXPLAIN 干跑（由 api 层编排 exec 连接，本模块只负责状态迁移）。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import string
import unicodedata

from app.errors import E_NOT_FOUND, BizError
from app.identifiers import require_id
from app.store import sessions as store_sessions
from app.store import settings as store_settings
from app.store.models import Sample

_PUNCT_TABLE = str.maketrans("", "", string.punctuation + "，。！？、；：（）【】《》「」“”‘’")


def normalize_question(question: str) -> str:
    """归一化：NFKC → 小写 → 去标点 → 去一切空白。"""
    text = unicodedata.normalize("NFKC", question).lower()
    text = text.translate(_PUNCT_TABLE)
    return re.sub(r"\s+", "", text)


# ---------- semantic_rev 五因子（D-15） ----------


def _schema_rev(conn: sqlite3.Connection, ds_id: int) -> str:
    row = conn.execute("SELECT revision FROM schema_revision WHERE datasource_id=?", (ds_id,)).fetchone()
    return str(row[0]) if row else "missing"


def _sem_bind_rev(conn: sqlite3.Connection, ds_id: int) -> str:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(c.updated_at), '') FROM column_meta c"
        " JOIN table_meta t ON t.id = c.table_id"
        " WHERE t.datasource_id = ? AND c.field_standard_id IS NOT NULL",
        (ds_id,),
    ).fetchone()
    return f"{row[0]}:{row[1]}"


def compute_semantic_rev(conn: sqlite3.Connection, ds_id: int, dialect_card_hash: str) -> str:
    """五因子 sha256（dialect_card_hash 由调用方按能力卡文件内容供给）。"""
    parts = [
        _schema_rev(conn, ds_id),
        store_sessions.clarify_pref_rev(conn, ds_id),
        dialect_card_hash,
        _sem_bind_rev(conn, ds_id),
        hashlib.sha256(str(store_settings.get_setting(conn, "prompt.system")).encode("utf-8")).hexdigest(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# ---------- CRUD ----------


def query_samples(
    conn: sqlite3.Connection, datasource_id: int | None = None, status: str | None = None
) -> list[Sample]:
    sql = "SELECT * FROM sample"
    conds: list[str] = []
    params: list[object] = []
    if datasource_id is not None:
        conds.append("datasource_id = ?")
        params.append(datasource_id)
    if status:
        conds.append("status = ?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    rows = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
    return [Sample.from_row(r) for r in rows]


def get_sample(conn: sqlite3.Connection, sample_id: int) -> Sample:
    row = conn.execute("SELECT * FROM sample WHERE id = ?", (sample_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"样例不存在: {sample_id}", 404)
    return Sample.from_row(row)


def save_sample(
    conn: sqlite3.Connection, *, datasource_id: int, question: str, sql: str, source: str = "manual"
) -> Sample:
    cur = conn.execute(
        "INSERT INTO sample(datasource_id, question, question_norm, sql, source) VALUES (?, ?, ?, ?, ?)",
        (datasource_id, question, normalize_question(question), sql, source),
    )
    return get_sample(conn, require_id(cur.lastrowid))


def update_sample(
    conn: sqlite3.Connection,
    sample_id: int,
    *,
    question: str | None = None,
    sql: str | None = None,
    status: str | None = None,
) -> Sample:
    get_sample(conn, sample_id)
    if question is not None:
        conn.execute(
            "UPDATE sample SET question = ?, question_norm = ? WHERE id = ?",
            (question, normalize_question(question), sample_id),
        )
    if sql is not None:
        conn.execute("UPDATE sample SET sql = ? WHERE id = ?", (sql, sample_id))
    if status is not None:
        if status not in ("on", "stale", "off"):
            raise BizError("E4420", f"非法样例状态: {status}", 422)
        conn.execute("UPDATE sample SET status = ? WHERE id = ?", (status, sample_id))
    return get_sample(conn, sample_id)


def delete_sample(conn: sqlite3.Connection, sample_id: int) -> None:
    cur = conn.execute("DELETE FROM sample WHERE id = ?", (sample_id,))
    if cur.rowcount == 0:
        raise BizError(E_NOT_FOUND, f"样例不存在: {sample_id}", 404)


# ---------- 缓存命中与 stale 治理 ----------


def cache_lookup(conn: sqlite3.Connection, ds_id: int, question: str, semantic_rev: str) -> Sample | None:
    """三条件命中：question_norm 精确 ∧ status=on ∧ semantic_rev 相等。命中累计 hit_count。"""
    norm = normalize_question(question)
    row = conn.execute(
        "SELECT * FROM sample WHERE datasource_id = ? AND question_norm = ?"
        " AND status = 'on' AND semantic_rev = ?",
        (ds_id, norm, semantic_rev),
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE sample SET hit_count = hit_count + 1 WHERE id = ?", (row["id"],))
    sample = Sample.from_row(row)
    return sample.model_copy(update={"hit_count": sample.hit_count + 1})  # 返回累计后值


def refresh_semantic_rev(conn: sqlite3.Connection, sample_id: int, semantic_rev: str) -> None:
    conn.execute("UPDATE sample SET semantic_rev = ? WHERE id = ?", (semantic_rev, sample_id))


def mark_stale_by_vanished(conn: sqlite3.Connection, ds_id: int, vanished_fields: list[str]) -> int:
    """schema 变更自动置过期（sample.auto_stale）：SQL 引用消失字段 → stale，记字段名。"""
    if not vanished_fields:
        return 0
    marked = 0
    rows = conn.execute(
        "SELECT id, sql FROM sample WHERE datasource_id = ? AND status = 'on'", (ds_id,)
    ).fetchall()
    for r in rows:
        for field in vanished_fields:
            if re.search(rf"\b{re.escape(field)}\b", r["sql"]):
                conn.execute(
                    "UPDATE sample SET status = 'stale', stale_reason = ? WHERE id = ?", (field, r["id"])
                )
                marked += 1
                break
    return marked


def mark_revalidated(conn: sqlite3.Connection, sample_id: int) -> Sample:
    """EXPLAIN 干跑复验通过 → 回 on 并刷新 validated_at。"""
    conn.execute(
        "UPDATE sample SET status = 'on', stale_reason = NULL, validated_at = datetime('now') WHERE id = ?",
        (sample_id,),
    )
    return get_sample(conn, sample_id)


def query_fewshot(conn: sqlite3.Connection, ds_id: int, limit: int, question: str = "") -> list[Sample]:
    """Bounded lexical reranking; exact cache reuse remains a separate validated path."""
    if limit <= 0:
        return []
    rows = conn.execute(
        "SELECT * FROM sample WHERE datasource_id = ? AND status = 'on'"
        " ORDER BY hit_count DESC, id DESC LIMIT 1000",
        (ds_id,),
    ).fetchall()
    if not question:
        return [Sample.from_row(r) for r in rows[:limit]]

    def terms(text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", text).lower()
        out = set(re.findall(r"[a-z0-9_]+", normalized))
        for run in re.findall(r"[\u4e00-\u9fff]+", normalized):
            out.update(run[i : i + 2] for i in range(max(1, len(run) - 1)))
        return out

    wanted = terms(question)
    scored = []
    for row in rows:
        candidate = terms(row["question"])
        overlap = wanted & candidate
        if overlap:
            score = len(overlap) / max(1, len(wanted | candidate))
            scored.append((score, int(row["hit_count"] or 0), int(row["id"]), row))
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2]))
    return [Sample.from_row(item[3]) for item in scored[:limit]]
