"""业务 ID 生成（contracts C0 / design D-22）。

`s_`/`q_` 前缀 + ULID（Crockford Base32，26 字符，毫秒前缀天然可排序）。
`client_request_id = thread_id = query_id` 三位一体；服务端不做二次生成（避免键分裂）。
"""

from __future__ import annotations

import re

from ulid import ULID

_ULID_RE = re.compile(r"^[sq]_[0-9A-HJKMNP-TV-Z]{26}$")


def new_id(prefix: str) -> str:
    """生成带前缀的 ULID。prefix 仅允许 's_' / 'q_'。"""
    if prefix not in ("s_", "q_"):
        raise ValueError(f"非法业务 ID 前缀: {prefix}")
    return f"{prefix}{ULID()!s}"


def new_session_id() -> str:
    return new_id("s_")


def new_query_id() -> str:
    return new_id("q_")


def is_valid_biz_id(value: str) -> bool:
    """校验 `s_`/`q_` + 26 位 Crockford Base32 形态。"""
    return bool(_ULID_RE.match(value or ""))
