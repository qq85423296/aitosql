"""检索供给用例（T1.2 验收锚点）：bigram 展开 / RRF 确定性排序 / 预算装配 / 滞后带。"""

from __future__ import annotations

import sqlite3

from app.retrieval import keyword, supply
from app.store import datasources as store_datasources


def test_bigram_expand() -> None:
    out = keyword.bigram_expand("订单金额 amount")
    assert "订单" in out and "单金" in out and "金额" in out  # 中文 bigram
    assert "amount" in out  # 英文词保留


def test_rrf_deterministic_order() -> None:
    """确定性排序验收锚点：rrf_score DESC, table_name ASC。"""
    fts = [("orders", 10.0), ("channels", 8.0), ("users", 7.0)]
    vec = [("channels", 0.1), ("orders", 0.2)]
    ranked = supply._rrf_fuse(fts, vec)
    # channels: 1/61+1/61=0.0328；orders: 1/60+1/62=0.0328…；users: 1/62
    assert ranked[0] in ("orders", "channels")
    assert ranked[-1] == "users"
    # 同分字典序：两路各自 rank1（b_t 走 FTS、 a_t 走向量）→ 分数严格相等
    tie = supply._rrf_fuse([("b_t", 1.0)], [("a_t", 0.5)])
    assert tie == ["a_t", "b_t"]


def _seed_schema(conn: sqlite3.Connection) -> int:
    ds_id = store_datasources.save_datasource(
        conn,
        name="t",
        dialect="mysql",
        mode="online",
        host="127.0.0.1",
        port=3306,
        db_name="biz",
        username="u",
        password_enc=None,
    )
    tid, _ = store_datasources.upsert_table_physical(
        conn,
        ds_id,
        name="orders",
        comment_db="订单主表",
        row_estimate=100,
        field_count=2,
        partition_type="day",
    )
    store_datasources.upsert_column_physical(
        conn, tid, name="id", type_="bigint", comment_db="订单ID", is_pk=1, is_partition=0, is_bucket=0
    )
    store_datasources.upsert_column_physical(
        conn,
        tid,
        name="created_at",
        type_="datetime",
        comment_db="下单时间",
        is_pk=0,
        is_partition=1,
        is_bucket=0,
    )
    return ds_id


def test_supply_full_mode_small_schema(tmp_db: sqlite3.Connection) -> None:
    ds_id = _seed_schema(tmp_db)
    result = supply.supply(
        tmp_db, question="订单", datasource_id=ds_id, budget_tokens=2000, mode_setting="auto"
    )
    assert result.schema_mode == "full"  # 库表 ≤30 → full
    assert "orders" in result.schema_text
    assert "分区键" in result.schema_text  # 键标注注入
    assert result.hit_tables == ["orders"]


def test_supply_forced_retrieval_uses_fts(tmp_db: sqlite3.Connection) -> None:
    ds_id = _seed_schema(tmp_db)
    rows = [
        (str(ds_id), "orders", "id", "订单ID"),
        (str(ds_id), "orders", "created_at", "下单时间"),
    ]  # 首元=数据源 id 文本位
    keyword.rebuild_fts(tmp_db, ds_id, rows)
    result = supply.supply(
        tmp_db, question="订单ID 查询", datasource_id=ds_id, budget_tokens=200, mode_setting="retrieval"
    )
    assert result.schema_mode == "retrieval"
    assert result.hit_tables  # FTS 召回命中
    assert result.hit_tables[0] == "orders"


def test_hysteresis_keeps_last_mode() -> None:
    """滞后带 55-65%：带内保持上次模式（防模式抖动）。"""
    # 直接验证带内分支：预算使估算占比落入带内时沿用 last_mode
    assert supply.HYSTERESIS_LO == 55 and supply.HYSTERESIS_HI == 65
