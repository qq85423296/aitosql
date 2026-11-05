"""envelope 与鉴权用例（C0 / R-5 契约证据形状：{code, message, data}）。"""

from __future__ import annotations

import pytest

from app.api.deps import ok
from app.errors import E_NOT_FOUND, E_UNAUTHORIZED, BizError


def test_ok_envelope_shape() -> None:
    body = ok({"a": 1})
    assert body == {"code": 0, "message": "ok", "data": {"a": 1}}


def test_biz_error_carries_code_and_status() -> None:
    exc = BizError(E_NOT_FOUND, "资源不存在", 404)
    assert exc.code == "E4040" and exc.http_status == 404
    exc401 = BizError(E_UNAUTHORIZED, "缺少 Bearer token", 401)
    assert exc401.http_status == 401


async def test_bearer_rejects_missing() -> None:
    from app.api import deps

    with pytest.raises(BizError) as exc_info:
        await deps.require_token(None)
    assert exc_info.value.code == E_UNAUTHORIZED


def test_bearer_check_token(tmp_path, monkeypatch) -> None:
    """Bearer 校验：错误 token 拒绝 / 正确 token 放行（临时库隔离）。"""
    from app.api import deps
    from app.store import db as store_db
    from app.store import settings as store_settings

    monkeypatch.setenv("AITOSQL_DATA", str(tmp_path))
    store_db.run_migrations(tmp_path / "app.db")
    with store_db.get_conn() as conn:
        store_settings.ensure_defaults(conn)
        store_settings.save_setting(conn, "server.token", "good-token")
    with pytest.raises(BizError):
        deps._check_token("Bearer bad-token")
    deps._check_token("Bearer good-token")  # 不抛即通过
