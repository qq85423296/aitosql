"""provider 表 DAO（D4：SQL 拼装收口 store 层；构建期补充文件，回写 plan §5.3）。

密文纪律：api_key_enc 只写不读——列表/详情返回模型永不携带密文（C1.6「密钥未外发」）；
解密仅发生在 llm/providers.py 发起调用前（经 store 取密文行后交 llm/secrets）。
"""

from __future__ import annotations

import sqlite3

from app.errors import E_NOT_FOUND, BizError
from app.identifiers import require_id
from app.store.models import Provider


def query_providers(conn: sqlite3.Connection) -> list[Provider]:
    rows = conn.execute("SELECT * FROM provider ORDER BY id").fetchall()
    return [Provider.from_row(r) for r in rows]


def get_provider(conn: sqlite3.Connection, provider_id: int) -> Provider:
    row = conn.execute("SELECT * FROM provider WHERE id = ?", (provider_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"提供商不存在: {provider_id}", 404)
    return Provider.from_row(row)


def get_provider_row(conn: sqlite3.Connection, provider_id: int) -> sqlite3.Row:
    """取原始行（含 api_key_enc BLOB）——仅供 llm 调用链解密使用，禁止直接出网。"""
    row = conn.execute("SELECT * FROM provider WHERE id = ?", (provider_id,)).fetchone()
    if row is None:
        raise BizError(E_NOT_FOUND, f"提供商不存在: {provider_id}", 404)
    return row


def save_provider(
    conn: sqlite3.Connection,
    *,
    name: str,
    type_: str,
    protocol: str,
    base_url: str | None,
    api_key_enc: bytes | None,
    models: list[str],
    enabled: int = 1,
) -> int:
    import json

    cur = conn.execute(
        "INSERT INTO provider(name, type, protocol, base_url, api_key_enc, models_json, enabled)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, type_, protocol, base_url, api_key_enc, json.dumps(models, ensure_ascii=False), enabled),
    )
    return require_id(cur.lastrowid)


def update_provider(
    conn: sqlite3.Connection,
    provider_id: int,
    *,
    name: str | None = None,
    protocol: str | None = None,
    base_url: str | None = None,
    api_key_enc: bytes | object | None = ...,  # ... = 不触碰密文列
    models: list[str] | None = None,
    enabled: int | None = None,
) -> None:
    import json

    get_provider(conn, provider_id)  # 404 闸门
    sets: list[str] = []
    params: list[object] = []
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if protocol is not None:
        sets.append("protocol = ?")
        params.append(protocol)
    if base_url is not None:
        sets.append("base_url = ?")
        params.append(base_url)
    if api_key_enc is not ...:
        sets.append("api_key_enc = ?")
        params.append(api_key_enc)
    if models is not None:
        sets.append("models_json = ?")
        params.append(json.dumps(models, ensure_ascii=False))
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(enabled)
    if not sets:
        return
    params.append(provider_id)
    conn.execute(f"UPDATE provider SET {', '.join(sets)} WHERE id = ?", params)


def delete_provider(conn: sqlite3.Connection, provider_id: int) -> None:
    get_provider(conn, provider_id)
    conn.execute("DELETE FROM provider WHERE id = ?", (provider_id,))
