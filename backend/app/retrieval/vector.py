"""向量路（design D-08）：fastembed + BAAI/bge-small-zh-v1.5（512 维）+ sqlite-vec。

- fastembed 唯一 import 点（D3）；
- schema_vec 运行期按 `schema.embed_dim` 建表，不写死 migration；
- 换模型/权重缺失：schema.vector 临时 off，降级纯 FTS，完成自动恢复；
- 召回 top-4，distance ≤ 0.35 过滤；FTS 命中 ≥6 表时不触发（supply 侧判断）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import struct
import threading
import time
from typing import Any

from app.store import settings as store_settings

logger = logging.getLogger("aitosql.retrieval.vector")

DISTANCE_THRESHOLD = 0.35
VEC_TABLE = "schema_vec_v2"
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MODEL_DIM = 512

_embedder: Any = None
_embedder_failed = False
_retry_at = 0.0
_embedder_lock = threading.Lock()


def _get_embedder() -> Any:
    """Read local cached weights only; explicit provisioning is a separate operation."""
    global _embedder, _embedder_failed, _retry_at
    with _embedder_lock:
        if _embedder is not None or time.monotonic() < _retry_at:
            return _embedder
        try:
            from fastembed import TextEmbedding

            _embedder = TextEmbedding(
                model_name=MODEL_NAME, cache_dir=os.environ.get("AITOSQL_MODEL_CACHE"), local_files_only=True
            )
            _embedder_failed = False
        except Exception as exc:
            _embedder_failed = True
            _retry_at = time.monotonic() + 60
            logger.warning("Local embedding unavailable; using FTS: %s", type(exc).__name__)
        return _embedder


def vector_enabled(conn: sqlite3.Connection) -> bool:
    """开关判定：schema.vector != off 且 embedder 可用。"""
    return str(store_settings.get_setting(conn, "schema.vector")) != "off" and (_get_embedder() is not None)


async def check_weights_or_degrade() -> None:
    import anyio

    if await anyio.to_thread.run_sync(_get_embedder) is None:
        logger.warning("Local weights absent: runtime FTS fallback; vector setting unchanged")


def ensure_vec_table(conn: sqlite3.Connection, dim: int) -> bool:
    """运行期建表（维度随 embed_dim）；sqlite-vec 缺失返回 False（降级纯 FTS）。"""
    try:
        import sqlite_vec

        if dim != MODEL_DIM:
            return False
        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_TABLE} USING vec0("
            f"datasource_id TEXT, table_name TEXT, doc TEXT, embedding float[{dim}] distance_metric=cosine)"
        )
        return True
    except Exception as exc:
        logger.warning("sqlite-vec 不可用: %s", type(exc).__name__)
        return False


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    embedder = _get_embedder()
    if embedder is None:
        return None
    return [list(map(float, v)) for v in embedder.embed(texts)]


def rebuild_vec(conn: sqlite3.Connection, datasource_id: int, docs: list[tuple[str, str]]) -> int:
    """按数据源重建向量表（docs = (table_name, 消毒后文本)；消毒先于 embedding，§8.4）。"""
    dim = int(store_settings.get_setting(conn, "schema.embed_dim"))
    if not ensure_vec_table(conn, dim):
        return 0
    vecs = embed_texts([d for _t, d in docs])
    if vecs is None:
        return 0
    return replace_vectors(conn, datasource_id, docs, vecs, dim=dim)


def replace_vectors(
    conn: sqlite3.Connection,
    datasource_id: int,
    docs: list[tuple[str, str]],
    vecs: list[list[float]],
    *,
    dim: int,
) -> int:
    if len(docs) != len(vecs) or any(len(v) != dim for v in vecs):
        raise ValueError("Embedding shape does not match the configured model")
    conn.execute(f"DELETE FROM {VEC_TABLE} WHERE datasource_id = ?", (str(datasource_id),))
    for (table, doc), vec in zip(docs, vecs, strict=True):
        conn.execute(
            f"INSERT INTO {VEC_TABLE}(datasource_id, table_name, doc, embedding) VALUES (?, ?, ?, ?)",
            (str(datasource_id), table, doc, _pack(vec)),
        )
    return len(docs)


def search_tables(
    conn: sqlite3.Connection, datasource_id: int, question: str, top: int = 4
) -> list[tuple[str, float]]:
    """向量召回 top-4，distance ≤ 0.35；任一环节不可用 → 空召回（supply 降级）。"""
    configured_model = str(store_settings.get_setting(conn, "schema.embed_model"))
    dim = int(store_settings.get_setting(conn, "schema.embed_dim"))
    revision = conn.execute(
        "SELECT revision,vector_revision,vector_model,vector_dim FROM schema_revision WHERE datasource_id=?",
        (datasource_id,),
    ).fetchone()
    if (
        not revision
        or revision[0] != revision[1]
        or revision[2] != configured_model
        or revision[3] != dim
        or configured_model != MODEL_NAME
    ):
        return []
    if not vector_enabled(conn):
        return []
    if not ensure_vec_table(conn, dim):
        return []
    vecs = embed_texts([question])
    if not vecs:
        return []
    try:
        rows = conn.execute(
            f"SELECT table_name, distance FROM {VEC_TABLE}"
            " WHERE embedding MATCH ? AND k = ? AND datasource_id = ?",
            (_pack(vecs[0]), top, str(datasource_id)),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(str(r[0]), float(r[1])) for r in rows if float(r[1]) <= DISTANCE_THRESHOLD]
