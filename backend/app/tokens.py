"""token 估算（SSOT §8.3 / design D-09 / plan S11）。

按段分别取 max(len//1.7 中文, tiktoken cl100k 英文)，留 ±15-20% 余量。

放置说明（构建期定型，回写 design 段 5）：该估算同时被 llm/prompts.py（信封预算）
与 retrieval/supply.py（schema 预算装配）消费；两者同属 L3 且互禁导入（D1 补），
故估算器落在层外公共位 app/tokens.py（根级工具模块，不入七领域模块清单）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tiktoken import Encoding

logger = logging.getLogger("aitosql.tokens")

_encoder: Encoding | None = None
_encoder_failed = False


def _get_encoder() -> Encoding | None:
    """tiktoken cl100k 编码器（惰性加载；缺失时退化为 len//4 估算）。"""
    global _encoder, _encoder_failed
    if _encoder is not None or _encoder_failed:
        return _encoder
    try:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _encoder_failed = True
        logger.warning("tiktoken 不可用，英文通道退化为 len//4 估算")
    return _encoder


def estimate_tokens(text: str) -> int:
    """单段 token 估算：max(中文 len//1.7, 英文 cl100k)。"""
    if not text:
        return 0
    zh_est = int(len(text) / 1.7)
    enc = _get_encoder()
    en_est = len(enc.encode(text)) if enc is not None else len(text) // 4
    return max(zh_est, en_est)


def estimate_segments(segments: list[dict]) -> list[dict]:
    """为信封段列表补齐 tokens 字段（原地填充并返回）。"""
    for seg in segments:
        seg["tokens"] = estimate_tokens(seg.get("text") or "")
    return segments
