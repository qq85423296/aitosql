"""样例库用例（D-15 / §12.1）：归一化 / semantic_rev 三条件命中 / stale 治理。"""

from __future__ import annotations

import sqlite3

from app.store import datasources as store_datasources
from app.store import samples as store_samples


def _seed_ds(conn: sqlite3.Connection) -> int:
    return store_datasources.save_datasource(
        conn,
        name="t",
        dialect="doris",
        mode="online",
        host="127.0.0.1",
        port=9030,
        db_name="analytics",
        username="u",
        password_enc=None,
    )


def test_question_norm() -> None:
    assert store_samples.normalize_question("本月 收入是多少？") == "本月收入是多少"
    assert store_samples.normalize_question("  Hello World! ") == "helloworld"


def test_cache_three_conditions(tmp_db: sqlite3.Connection) -> None:
    ds_id = _seed_ds(tmp_db)
    s = store_samples.save_sample(tmp_db, datasource_id=ds_id, question="本月收入是多少？", sql="SELECT 1")
    rev = store_samples.compute_semantic_rev(tmp_db, ds_id, "card_hash_a")
    # 未登记 semantic_rev → 不命中
    assert store_samples.cache_lookup(tmp_db, ds_id, "本月收入是多少？", rev) is None
    store_samples.refresh_semantic_rev(tmp_db, s.id, rev)
    hit = store_samples.cache_lookup(tmp_db, ds_id, "本月收入是多少？", rev)
    assert hit is not None and hit.sql == "SELECT 1"
    assert hit.hit_count == 1  # 命中累计
    # rev 变化（因子失效）→ 不命中（懒惰失效）
    assert store_samples.cache_lookup(tmp_db, ds_id, "本月收入是多少？", "other") is None
    # status != on → 不命中
    store_samples.update_sample(tmp_db, s.id, status="off")
    assert store_samples.cache_lookup(tmp_db, ds_id, "本月收入是多少？", rev) is None


def test_stale_governance_marks_field(tmp_db: sqlite3.Connection) -> None:
    ds_id = _seed_ds(tmp_db)
    store_samples.save_sample(
        tmp_db, datasource_id=ds_id, question="退款总额", sql="SELECT SUM(amount) FROM refunds"
    )
    marked = store_samples.mark_stale_by_vanished(tmp_db, ds_id, ["amount"])
    assert marked == 1
    items = store_samples.query_samples(tmp_db, ds_id, status="stale")
    assert len(items) == 1 and items[0].stale_reason == "amount"  # stale_reason 记字段名


def test_revalidate_back_to_on(tmp_db: sqlite3.Connection) -> None:
    ds_id = _seed_ds(tmp_db)
    s = store_samples.save_sample(tmp_db, datasource_id=ds_id, question="q", sql="SELECT 1")
    store_samples.update_sample(tmp_db, s.id, status="stale")
    refreshed = store_samples.mark_revalidated(tmp_db, s.id)
    assert refreshed.status == "on" and refreshed.validated_at is not None
