"""版本探测 → 能力卡（design D-32/D-41）。

注册/摄取时探测引擎版本覆盖能力卡 detected_version；MySQL <8 标注能力降级（SSOT §17-1）。
能力卡文件 = configs/dialects/{dialect}.yaml（默认 doris.yaml 为 SSOT §8.2 原文）。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import yaml

from app import config

logger = logging.getLogger("aitosql.ingest.capability")


@lru_cache(maxsize=8)
def load_card(dialect: str) -> dict[str, Any]:
    """加载方言能力卡（YAML）；缺失文件返回最小兜底卡。"""
    path = config.dialects_config_dir() / f"{dialect}.yaml"
    if not path.is_file():
        logger.warning("能力卡缺失，使用兜底: %s", path)
        return {"engine": dialect, "detected_version": "", "supports": [], "notes": [], "forbidden": []}
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def card_hash(dialect: str) -> str:
    """能力卡内容指纹（semantic_rev 五因子之一 dialect_card_hash）。"""
    path = config.dialects_config_dir() / f"{dialect}.yaml"
    data = path.read_bytes() if path.is_file() else b""
    return hashlib.sha256(data).hexdigest()


def probe_version(dialect: str, query_fn: Callable[[str], list[tuple]]) -> str | None:
    """版本探测：query_fn 为引擎查询回调（`SELECT VERSION()` / CH 同形态）。"""
    try:
        rows = query_fn("SELECT VERSION()")
        if rows and rows[0]:
            return str(rows[0][0])
    except Exception as exc:
        logger.warning("版本探测失败: %s", type(exc).__name__)
    return None


def build_capabilities(dialect: str, version: str | None) -> dict[str, Any]:
    """能力卡装配：版本覆盖 + 降级标注 + 干跑/KILL 能力位。"""
    card = load_card(dialect)
    caps: dict[str, Any] = {
        "engine": card.get("engine", dialect),
        "detected_version": version or card.get("detected_version", ""),
        "supports": card.get("supports", []),
        "forbidden": card.get("forbidden", []),
        "explain": {"mysql": "json", "doris": "text", "clickhouse": "estimate"}.get(dialect, "none"),
        "kill_channel": dialect in ("mysql", "doris"),  # CH readonly=1 无 KILL 通道（D-36）
        "degraded": False,
    }
    if dialect == "mysql" and version:
        major = version.split(".", 1)[0]
        if major.isdigit() and int(major) < 8:
            caps["degraded"] = True
            caps.setdefault("notes", []).append("MySQL <8.0：能力降级标注（SSOT §17-1）")
    return caps
