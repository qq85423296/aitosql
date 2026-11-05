"""Shared text normalization for metadata ingestion and prompt retrieval."""

from __future__ import annotations

import re
import unicodedata

DEFAULT_MAX_LEN = 200


def sanitize_text(text: str | None, max_len: int = DEFAULT_MAX_LEN) -> str:
    if not text:
        return ""
    return "".join(c for c in str(text) if unicodedata.category(c) != "Cf")[:max_len]


def sanitize_row(
    table_name: str, col_name: str | None, comment: str | None, max_len: int = DEFAULT_MAX_LEN
) -> tuple[str, str, str]:
    return (
        sanitize_text(table_name, max_len),
        sanitize_text(col_name, max_len),
        sanitize_text(comment, max_len),
    )


def lexical_terms(text: str) -> set[str]:
    """词法词项集：NFKC 小写后取字母数字词 + CJK bigram（检索相关性评分共用）。"""
    normalized = unicodedata.normalize("NFKC", text).lower()
    out = set(re.findall(r"[a-z0-9_]+", normalized))
    for run in re.findall(r"[一-鿿]+", normalized):
        out.update(run[i : i + 2] for i in range(max(1, len(run) - 1)))
    return out
