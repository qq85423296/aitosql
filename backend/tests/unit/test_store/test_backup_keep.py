"""阶段 E：backup.keep 边界值——写入侧 422 + 删除点钳制，不致误删全部备份。

覆盖：
- save_setting 最小值校验（keep<1 → 422）；
- 库内脏值（绕过写入校验直接 SQL 篡改）下 _rotate 钳制 keep≥1，
  最新备份必存活（未钳制时 keep=-1 切片 files[1:] 反向只留最旧）；
- 非数值脏值显式失败且零删除（不得静默滚动）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.errors import E_BAD_REQUEST, BizError
from app.store import backup as store_backup
from app.store import db as store_db
from app.store import settings as store_settings


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    # 数据目录命名 data（镜像生产布局）：backup.dir 相对路径锚定 data_dir().parent，
    # 若直指 tmp_path 根，各用例的备份目录会汇聚到共享父目录互相污染
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AITOSQL_DATA", str(data))
    store_db.run_migrations(data / "app.db")
    conn = store_db.connect(data / "app.db")
    store_settings.ensure_defaults(conn)
    conn.commit()
    yield conn
    conn.close()


def _tamper_keep(conn: sqlite3.Connection, raw_json: str) -> None:
    """绕过写入校验直接改库（模拟历史库/手工 SQL 落脏值）。"""
    conn.execute("UPDATE setting SET value_json = ? WHERE key = 'backup.keep'", (raw_json,))
    conn.commit()


def _fake_old_backups(conn: sqlite3.Connection) -> Path:
    backup_dir = store_backup._resolve_backup_dir(conn)
    backup_dir.mkdir(parents=True, exist_ok=True)
    for day in ("01", "02", "03"):
        (backup_dir / f"app-202601{day}-000000.db").write_bytes(b"fake")
    return backup_dir


def test_save_setting_keep_minimum(env: sqlite3.Connection) -> None:
    with pytest.raises(BizError) as e0:
        store_settings.save_setting(env, "backup.keep", 0)
    with pytest.raises(BizError) as e1:
        store_settings.save_setting(env, "backup.keep", -1)
    for exc in (e0.value, e1.value):
        assert exc.code == E_BAD_REQUEST and exc.http_status == 422
        assert "最小值" in exc.message
    store_settings.save_setting(env, "backup.keep", 1)  # 边界值放行


def test_rotate_clamps_tampered_zero_keeps_newest(env: sqlite3.Connection) -> None:
    backup_dir = _fake_old_backups(env)
    _tamper_keep(env, "0")
    created = store_backup.create_backup()
    left = sorted(backup_dir.glob("app-*.db*"))
    assert left == [created]  # 钳制 keep=1：最新存活、最旧滚动删除（非全删/非反向）


def test_rotate_clamps_tampered_negative_keeps_newest(env: sqlite3.Connection) -> None:
    backup_dir = _fake_old_backups(env)
    _tamper_keep(env, "-1")
    created = store_backup.create_backup()
    left = sorted(backup_dir.glob("app-*.db*"))
    # 未钳制时 keep=-1 → files[1:] 仅留最旧 fake 且删除最新真实备份
    assert left == [created]


def test_rotate_tampered_non_numeric_fails_loudly(env: sqlite3.Connection) -> None:
    backup_dir = _fake_old_backups(env)
    _tamper_keep(env, '"abc"')
    with pytest.raises((ValueError, TypeError)):
        store_backup.create_backup()  # 显式失败，不做任何滚动
    assert len(list(backup_dir.glob("app-*.db*"))) == 3  # 三份假备份零删除


def test_rotate_normal_keep(env: sqlite3.Connection) -> None:
    backup_dir = _fake_old_backups(env)
    store_settings.save_setting(env, "backup.keep", 3)
    env.commit()
    created = store_backup.create_backup()
    left = sorted(p.name for p in backup_dir.glob("app-*.db*"))
    assert left == ["app-20260102-000000.db", "app-20260103-000000.db", created.name]
