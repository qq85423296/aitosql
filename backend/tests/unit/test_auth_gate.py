"""Auth gate: server.auth 总开关 + Bearer 校验（含关闭路径回归）。"""

from __future__ import annotations

import pytest

from app.api import deps
from app.errors import E_UNAUTHORIZED, BizError
from app.store import db, settings


@pytest.fixture()
def auth_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AITOSQL_DATA", str(tmp_path))
    path = tmp_path / "app.db"
    db.run_migrations(path)
    with db.get_conn(path) as conn:
        settings.ensure_defaults(conn)
        settings.save_setting(conn, "server.token", "tok_test_secret")
    return path


def test_auth_on_requires_valid_bearer(auth_db):
    with pytest.raises(BizError) as missing:
        deps._check_token(None)
    assert missing.value.code == E_UNAUTHORIZED
    with pytest.raises(BizError):
        deps._check_token("Bearer wrong")
    deps._check_token("Bearer tok_test_secret")  # 正确 token 放行


def test_auth_off_allows_anonymous(auth_db):
    with db.get_conn(auth_db) as conn:
        settings.save_setting(conn, "server.auth", False)
    deps._check_token(None)  # 无头也放行
    deps._check_token("Bearer anything")
    with db.get_conn(auth_db) as conn:
        settings.save_setting(conn, "server.auth", True)
    with pytest.raises(BizError):
        deps._check_token(None)  # 重新开启立即恢复拦截
