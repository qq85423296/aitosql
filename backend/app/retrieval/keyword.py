"""FTS5 关键词路（design D-33）：中文应用层 bigram 展开 + bm25 聚合到表级 top-12。

写入端唯一入口 = ingest/sanitize.py 消毒后的文本（§8.4）；本模块不再二次消毒断言，
但 rebuild_fts 只接受已消毒行（调用契约由 ingest/online、offline 保证）。
"""

from __future__ import annotations

import re
import sqlite3

_CJK_RE = re.compile(r"[一-鿿]")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def bigram_expand(text: str) -> str:
    """中文 bigram 展开 + 英文/数字整词保留（FTS unicode61 语料形态）。

    CJK 缓冲与英文词缓冲分别累计、以空格分隔输出——避免混排文本（如「订单ID」）
    在写入端合并成单一 token 导致 bigram 召回失效（探针实测修复）。
    """
    if not text:
        return ""
    tokens: list[str] = []
    cjk_buf: list[str] = []
    word_buf: list[str] = []

    def flush_cjk() -> None:
        if len(cjk_buf) == 1:
            tokens.append(cjk_buf[0])
        else:
            tokens.extend("".join(cjk_buf[i : i + 2]) for i in range(len(cjk_buf) - 1))
        cjk_buf.clear()

    def flush_word() -> None:
        if word_buf:
            tokens.append("".join(word_buf).lower())
            word_buf.clear()

    for ch in text:
        if _CJK_RE.match(ch):
            flush_word()
            cjk_buf.append(ch)
        elif ch.isalnum() or ch == "_":
            flush_cjk()
            word_buf.append(ch)
        else:
            flush_cjk()
            flush_word()
    flush_cjk()
    flush_word()
    return " ".join(tokens)


def rebuild_fts(conn: sqlite3.Connection, datasource_id: int, rows: list[tuple[str, str, str, str]]) -> int:
    """按数据源全量重建 FTS（rows = 消毒后 (datasource_id, table, col, comment)）。"""
    conn.execute("DELETE FROM schema_fts WHERE datasource_id = ?", (str(datasource_id),))
    for ds, table, col, comment in rows:
        doc = bigram_expand(f"{table} {col} {comment}")
        conn.execute(
            "INSERT INTO schema_fts(datasource_id, table_name, col_name, comment) VALUES (?, ?, ?, ?)",
            (ds, table, col, doc),
        )
    return len(rows)


def _to_query(question: str) -> str:
    """问题 → MATCH 表达式：bigram 展开后词间 OR（召回优先，排序交给 bm25/RRF）。"""
    terms = [t for t in bigram_expand(question).split() if t]
    terms += [w.lower() for w in _WORD_RE.findall(question)]
    uniq = list(dict.fromkeys(terms))
    return " OR ".join(uniq) if uniq else '""'


def search_tables(
    conn: sqlite3.Connection, datasource_id: int, question: str, top: int = 12
) -> list[tuple[str, float]]:
    """bm25 聚合到表级 top-12：返回 [(table_name, score)]，score 越大越相关。

    bm25() 不能进入聚合上下文（sqlite 3.53 实测报错），逐行取回后应用层聚合；
    排序确定性锚点：score DESC, table_name ASC。
    """
    match = _to_query(question)
    if match == '""':
        return []
    try:
        rows = conn.execute(
            "SELECT table_name, bm25(schema_fts) AS s FROM schema_fts"
            " WHERE schema_fts MATCH ? AND datasource_id = ?",
            (match, str(datasource_id)),
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # MATCH 表达式异常（如全标点问题）→ 空召回，由 supply 兜底
    score: dict[str, float] = {}
    for table_name, s in rows:
        score[str(table_name)] = score.get(str(table_name), 0.0) + (-float(s))
    return sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
