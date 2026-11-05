"""pytest 公共夹具：临时 app.db（迁移执行器直建），不依赖真实引擎/模型端点（R-10）。

golden 中标注 need_engine 的用例单独分组（CI 提供三方言容器后启用）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.store import db as store_db
from app.store import settings as store_settings


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """临时 app.db：迁移幂等执行 + 默认值落库。"""
    db_path = tmp_path / "app.db"
    store_db.run_migrations(db_path)
    conn = store_db.connect(db_path)
    store_settings.ensure_defaults(conn)
    conn.commit()
    yield conn
    conn.close()
