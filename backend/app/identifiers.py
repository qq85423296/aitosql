"""Checked integer identifiers at SQLite/model boundaries."""

from __future__ import annotations

from app.errors import E_NOT_FOUND, BizError


def require_id(value: int | None) -> int:
    if value is None:
        raise BizError(E_NOT_FOUND, "关联标识缺失，请检查资源是否仍存在", 404)
    return value
