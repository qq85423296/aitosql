"""元数据字典模板（11 列）解析与入库回归。"""

from __future__ import annotations

import pytest

from app.errors import BizError
from app.ingest import offline
from app.store import datasources

HEADER = (
    "表模式名,表英文名,表中文名,表业务描述,字段序号,字段英文名,"
    "字段中文名,字段类型,字段业务描述,是否主键,是否分区"
)
ROW1 = (
    "dataos_v5_dev,dataps_logic_model_field,逻辑模型字段信息,主要存储逻辑模型元数据信息,"
    "1,id,字段编码,varchar(32),系统内定义的逻辑模型字段唯一标识,1,"
)


def test_parse_sample_row_and_comment_join() -> None:
    tables, errors = offline.csv_dict_template_to_tables(HEADER + "\n" + ROW1 + "\n")
    assert errors == []
    assert len(tables) == 1
    t = tables[0]
    assert t["schema"] == "dataos_v5_dev"
    assert t["name"] == "dataps_logic_model_field"
    assert t["comment"] == "逻辑模型字段信息｜主要存储逻辑模型元数据信息"
    assert t["columns"] == [
        {
            "name": "id",
            "type": "varchar(32)",
            "comment": "字段编码｜系统内定义的逻辑模型字段唯一标识",
            "is_pk": 1,
            "is_partition": 0,
        }
    ]


def test_grouping_seq_order_and_carry_forward() -> None:
    rows = [
        HEADER,
        "s1,orders,订单主表,,2,created_at,下单时间,datetime,,0,1",
        ",,,,1,id,订单ID,bigint,,1,0",  # 合并单元格导出：表级四列留空沿用上一行
        "s1,customers,客户主档,,1,id,客户ID,bigint,,1,0",
        "s1,customers,客户主档,,1,id,客户ID,bigint,,1,0".replace("s1", "", 1).replace(
            ",,,,1", ",,,,2"
        ).replace("id,客户ID", "name,客户姓名").replace("bigint", "varchar(64)"),
    ]
    tables, errors = offline.csv_dict_template_to_tables("\n".join(rows) + "\n")
    assert errors == []
    assert [t["name"] for t in tables] == ["orders", "customers"]
    assert [c["name"] for c in tables[0]["columns"]] == ["id", "created_at"]  # 序号升序
    assert tables[0]["columns"][1]["is_partition"] == 1
    assert tables[0]["comment"] == "订单主表"  # 无业务描述时不拼接
    assert [c["name"] for c in tables[1]["columns"]] == ["id", "name"]


def test_row_level_errors_do_not_block_valid_rows() -> None:
    rows = [
        HEADER,
        ROW1,
        "s1,bad1,坏表,,x,no_seq,字段,varchar(8),,1,0",  # 序号非整数
        "s1,bad2,坏表,,2,,缺英文名,varchar(8),,1,0",  # 缺字段英文名
        "s1,bad3,坏表,,3,no_pk,字段,varchar(8),,,0",  # 是否主键留空（必填）
        "s1,bad4,坏表,,4,bad_bool,字段,varchar(8),,maybe,0",  # 是否主键非法值
        "s1,good,好表,,1,id,主键,bigint,,是,否",  # 是/否 同义词
    ]
    tables, errors = offline.csv_dict_template_to_tables("\n".join(rows) + "\n")
    assert [t["name"] for t in tables] == ["dataps_logic_model_field", "good"]
    assert tables[1]["columns"][0]["is_pk"] == 1 and tables[1]["columns"][0]["is_partition"] == 0
    assert len(errors) == 4
    assert all(e["row"] in (3, 4, 5, 6) for e in errors)
    assert any("字段序号须为整数" in e["message"] for e in errors)
    assert any("缺少必填项 字段英文名" in e["message"] for e in errors)
    assert any("是否主键须填" in e["message"] for e in errors)


def test_zero_padded_seq_and_identifier_guard() -> None:
    rows = [
        HEADER,
        "doc_config,t_log,校验结果日志,,002,logic_job_code,逻辑任务编码,varchar(64),,0,0",
        ",,,,000,自增生成,记录行号,varchar(32),,1,0",  # 中文占位名 → 行级错误
        ",,,,001,id,运行实例ID,varchar(32),,1,0",
        ",,,,010,file_name,文件名称,varchar(64),,0,0",
    ]
    tables, errors = offline.csv_dict_template_to_tables("\n".join(rows) + "\n")
    assert len(errors) == 1
    assert "字段英文名须为英文标识符" in errors[0]["message"] and errors[0]["row"] == 3
    # 零填充序号按数值排序：001(id) → 002(logic_job_code) → 010(file_name)
    assert [c["name"] for c in tables[0]["columns"]] == ["id", "logic_job_code", "file_name"]


def test_missing_required_header_is_fatal() -> None:
    with pytest.raises(BizError) as exc:
        offline.csv_dict_template_to_tables("表英文名,表中文名\norders,订单\n")
    assert "缺少必填列" in str(exc.value)
    with pytest.raises(BizError):
        offline.csv_dict_template_to_tables("")


def test_commit_roundtrip_into_metadata(tmp_db) -> None:
    ds = datasources.save_datasource(
        conn=tmp_db,
        name="dict",
        dialect="mysql",
        mode="offline",
        host=None,
        port=None,
        db_name="s1",
        username=None,
        password_enc=None,
    )
    csv_text = HEADER + "\n" + ROW1 + "\n" + "s1,orders,订单主表,,1,id,订单ID,bigint,,1,0\n"
    tables, errors = offline.csv_dict_template_to_tables(csv_text)
    assert errors == []
    result = offline.commit_parsed_tables(tmp_db, ds, tables)
    assert result == {"tables": 2, "columns": 2}
    table = datasources.get_table_by_name(tmp_db, ds, "dataps_logic_model_field")
    assert table is not None and "逻辑模型字段信息" in (table.comment_db or "")
    # 0011：表模式名落库（问数 SQL 限定前缀）
    assert table.schema_name == "dataos_v5_dev"
    cols = datasources.query_columns(tmp_db, table.id)
    assert cols[0].is_pk == 1
    orders = datasources.get_table_by_name(tmp_db, ds, "orders")
    assert orders is not None and orders.schema_name == "s1"


def test_schema_name_coalesce_on_reimport(tmp_db) -> None:
    """0011：无模式信息的通道（DDL 粘贴）重摄取不抹掉已有 schema_name（COALESCE）。"""
    ds = datasources.save_datasource(
        conn=tmp_db,
        name="dict2",
        dialect="mysql",
        mode="offline",
        host=None,
        port=None,
        db_name=None,
        username=None,
        password_enc=None,
    )
    csv_text = HEADER + "\n" + "s1,orders,订单主表,,1,id,订单ID,bigint,,1,0\n"
    tables, _ = offline.csv_dict_template_to_tables(csv_text)
    offline.commit_parsed_tables(tmp_db, ds, tables)
    assert datasources.get_table_by_name(tmp_db, ds, "orders").schema_name == "s1"
    # DDL 通道（无 schema 字段）重摄取：保留 s1
    offline.commit_parsed_tables(
        tmp_db, ds, [{"name": "orders", "comment": "订单主表", "columns": [{"name": "id", "type": "bigint"}]}]
    )
    assert datasources.get_table_by_name(tmp_db, ds, "orders").schema_name == "s1"


def test_render_table_text_qualified_with_schema(tmp_db) -> None:
    """0011：问数提示词表标题带模式名前缀（schema.table），模型据此生成限定名。"""
    from app.retrieval import supply

    ds = datasources.save_datasource(
        conn=tmp_db,
        name="q",
        dialect="mysql",
        mode="offline",
        host=None,
        port=None,
        db_name=None,
        username=None,
        password_enc=None,
    )
    tid, _ = datasources.upsert_table_physical(
        tmp_db, ds, name="orders", comment_db=None, row_estimate=None,
        field_count=1, partition_type=None, schema_name="mart",
    )
    datasources.upsert_column_physical(
        tmp_db, tid, name="id", type_="bigint", comment_db=None, is_pk=1, is_partition=0, is_bucket=0
    )
    text = supply.render_table_text(tmp_db, ds, "orders", enum_top_n=0, partition_hint=False)
    assert text.startswith("表 mart.orders")
    # 无模式名：不强行加前缀
    tid2, _ = datasources.upsert_table_physical(
        tmp_db, ds, name="users", comment_db=None, row_estimate=None,
        field_count=1, partition_type=None,
    )
    datasources.upsert_column_physical(
        tmp_db, tid2, name="id", type_="bigint", comment_db=None, is_pk=1, is_partition=0, is_bucket=0
    )
    text2 = supply.render_table_text(tmp_db, ds, "users", enum_top_n=0, partition_hint=False)
    assert text2.startswith("表 users")
