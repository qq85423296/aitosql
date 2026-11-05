"""Versioned local metadata indexes. No business rows or remote embeddings are used."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.retrieval import keyword, vector
from app.store import db, settings
from app.text import sanitize_text


def status(conn: sqlite3.Connection, ds_id: int) -> dict:
    row = conn.execute("SELECT * FROM schema_revision WHERE datasource_id=?", (ds_id,)).fetchone()
    return dict(row) if row else {}


def documents(conn: sqlite3.Connection, ds_id: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT t.name AS table_name, COALESCE(NULLIF(t.comment_custom,''),t.comment_db,'') AS tc,"
        " c.name AS col_name,c.type,COALESCE(NULLIF(c.comment_custom,''),c.comment_db,'') AS cc,"
        " COALESCE(f.name,'') AS standard,COALESCE(f.description,'') AS description"
        " FROM table_meta t LEFT JOIN column_meta c ON c.table_id=t.id"
        " LEFT JOIN field_standard f ON f.id=c.field_standard_id"
        " WHERE t.datasource_id=? ORDER BY t.name,c.id",
        (ds_id,),
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for r in rows:
        name = str(r["table_name"])
        if name not in grouped:
            grouped[name] = [sanitize_text(name), sanitize_text(r["tc"])]
        grouped[name].append(
            " ".join(sanitize_text(r[key]) for key in ("col_name", "type", "cc", "standard", "description"))
        )
    return [(name, "\n".join(parts)) for name, parts in grouped.items()]


def ensure_fts(conn: sqlite3.Connection, ds_id: int) -> None:
    revision = status(conn, ds_id)
    if not revision or revision["revision"] == revision["fts_revision"]:
        return
    docs = documents(conn, ds_id)
    keyword.rebuild_fts(conn, ds_id, [(str(ds_id), table, "", doc) for table, doc in docs])
    conn.execute("UPDATE schema_revision SET fts_revision=revision WHERE datasource_id=?", (ds_id,))


def rebuild(ds_id: int, *, include_vectors: bool = True, db_path: Path | None = None) -> dict:
    """Embed outside the write transaction, then install only if the revision still matches."""
    with db.get_conn(db_path) as conn:
        conn.execute("BEGIN")
        before = status(conn, ds_id)
        if not before:
            raise ValueError("Unknown datasource")
        docs = documents(conn, ds_id)
        model = str(settings.get_setting(conn, "schema.embed_model"))
        dim = int(settings.get_setting(conn, "schema.embed_dim"))
        enabled = str(settings.get_setting(conn, "schema.vector")) != "off"
    embeddings = vector.embed_texts([doc for _, doc in docs]) if include_vectors and enabled else None
    with db.get_conn(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if status(conn, ds_id)["revision"] != before["revision"]:
            return {"installed": False, "reason": "schema_changed_during_embedding"}
        vector_count = 0
        if embeddings is not None and vector.ensure_vec_table(conn, dim):
            vector_count = vector.replace_vectors(conn, ds_id, docs, embeddings, dim=dim)
            conn.execute(
                "UPDATE schema_revision SET vector_revision=revision,vector_model=?,vector_dim=?"
                " WHERE datasource_id=?",
                (model, dim, ds_id),
            )
        keyword.rebuild_fts(conn, ds_id, [(str(ds_id), table, "", doc) for table, doc in docs])
        conn.execute("UPDATE schema_revision SET fts_revision=revision WHERE datasource_id=?", (ds_id,))
        return {
            "installed": True,
            "tables": len(docs),
            "vectors": vector_count,
            "revision": before["revision"],
            "mode": "hybrid" if vector_count else "fts",
        }
