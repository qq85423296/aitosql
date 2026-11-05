"""Pydantic v2 实体（design D-38 / plan S5：不引入 SQLAlchemy ORM）。

原生 sqlite3.Row → 模型显式构造（from_row 类方法）；字段与 migrations DDL 逐表对齐。
密级标注（C6.15）：password_enc/api_key_enc 为 Fernet 密文（备份写 NULL，永不回显）；
result_preview 为真实数据行；query_event 为 SQL 字面量明文（错误类落库前脱敏）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel, Field


def _j(text: str | None, default: Any) -> Any:
    """JSON 文本列解析（空值回退默认）。"""
    if not text:
        return default
    return json.loads(text)


# ---------- 连接与元数据 ----------


class Datasource(BaseModel):
    id: int
    name: str
    dialect: str  # mysql|doris|clickhouse
    mode: str  # online|offline
    host: str | None = None
    port: int | None = None
    db_name: str | None = None
    username: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    version: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    last_ingest_at: str | None = None
    created_at: str | None = None
    has_password: bool = False  # 密文永不回显，仅回显「是否已设置」

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Datasource:
        return cls(
            id=r["id"],
            name=r["name"],
            dialect=r["dialect"],
            mode=r["mode"],
            host=r["host"],
            port=r["port"],
            db_name=r["db_name"],
            username=r["username"],
            params=_j(r["params_json"], {}),
            version=r["version"],
            capabilities=_j(r["capabilities_json"], {}),
            last_ingest_at=r["last_ingest_at"],
            created_at=r["created_at"],
            has_password=r["password_enc"] is not None,
        )


class TableMeta(BaseModel):
    id: int
    datasource_id: int
    name: str
    schema_name: str | None = None  # 0011：模式名（问数 SQL 限定前缀）
    comment_db: str | None = None
    comment_custom: str | None = None  # 人工优先，重摄取不覆盖
    row_estimate: int | None = None
    field_count: int | None = None
    partition_type: str | None = None  # month|day|NULL
    updated_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> TableMeta:
        return cls(**{k: r[k] for k in cls.model_fields})


class ColumnMeta(BaseModel):
    id: int
    table_id: int
    name: str
    type: str
    comment_db: str | None = None
    comment_custom: str | None = None
    is_pk: int = 0
    is_partition: int = 0
    is_bucket: int = 0
    field_standard_id: int | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> ColumnMeta:
        return cls(**{k: r[k] for k in cls.model_fields})


class Relation(BaseModel):
    id: int
    datasource_id: int
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    source: str  # fk|inferred|manual
    confidence: float = 1.0
    status: str = "confirmed"  # confirmed|pending（inferred 默认 pending）
    created_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Relation:
        return cls(**{k: r[k] for k in cls.model_fields})


class GraphLayout(BaseModel):
    datasource_id: int
    node_positions: dict[str, dict[str, float]] = Field(default_factory=dict)
    updated_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> GraphLayout:
        return cls(
            datasource_id=r["datasource_id"],
            node_positions=_j(r["node_positions_json"], {}),
            updated_at=r["updated_at"],
        )


# ---------- 语义层（全局一份跨数据源） ----------


class FieldStandard(BaseModel):
    id: int
    name: str
    data_type: str | None = None
    unit: str | None = None
    value_desc: str | None = None
    dimension_standard_id: int | None = None
    description: str | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> FieldStandard:
        return cls(**{k: r[k] for k in cls.model_fields})


class DimensionStandard(BaseModel):
    id: int
    name: str
    description: str | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> DimensionStandard:
        return cls(**{k: r[k] for k in cls.model_fields})


class DimensionValue(BaseModel):
    id: int
    dimension_id: int
    value: str
    label: str
    sort: int = 0

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> DimensionValue:
        return cls(**{k: r[k] for k in cls.model_fields})


class MetricStandard(BaseModel):
    id: int
    name: str
    expr: str
    filter: str | None = None  # D-19：保留列名，全链路引号/参数化
    base_tables: str | None = None
    description: str | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> MetricStandard:
        return cls(**{k: r[k] for k in cls.model_fields})


# ---------- 建议 / 会话 / 查询 ----------


class AiSuggestion(BaseModel):
    id: int
    datasource_id: int
    kind: str  # comment|std_bind|relation|metric
    payload: dict[str, Any] = Field(default_factory=dict)  # 结构 C6.8
    confidence: float | None = None
    status: str = "pending"  # pending|accepted|ignored（无 target 列、无 rejected 态）
    run_id: int | None = None  # 产出批次（0007 增量列；0007 前的历史数据为 NULL）
    created_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> AiSuggestion:
        keys = set(r.keys())
        return cls(
            id=r["id"],
            datasource_id=r["datasource_id"],
            kind=r["kind"],
            payload=_j(r["payload_json"], {}),
            confidence=r["confidence"],
            status=r["status"],
            created_at=r["created_at"],
            run_id=r["run_id"] if "run_id" in keys else None,
        )


class AiRun(BaseModel):
    """AI 补全任务（0007；方案 v1.1 §3）。"""

    id: int
    datasource_id: int
    scope_type: str  # datasource|table|relations
    scope_table_id: int | None = None
    kinds: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    status: str = "queued"  # queued|running|done|failed|cancelled
    progress_done: int = 0
    progress_total: int = 0
    made: dict[str, int] = Field(default_factory=dict)
    failed: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    created_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> AiRun:
        return cls(
            id=r["id"],
            datasource_id=r["datasource_id"],
            scope_type=r["scope_type"],
            scope_table_id=r["scope_table_id"],
            kinds=_j(r["kinds_json"], []),
            options=_j(r["options_json"], {}),
            status=r["status"],
            progress_done=r["progress_done"],
            progress_total=r["progress_total"],
            made=_j(r["made_json"], {}),
            failed=_j(r["failed_json"], []),
            error=r["error"],
            created_at=r["created_at"],
            finished_at=r["finished_at"],
        )


class Session(BaseModel):
    id: str  # s_ + ULID
    title: str
    datasource_id: int | None = None
    pinned: int = 0
    archived: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    summary: str | None = None  # 滚动摘要（有损链，0004）
    summary_upto: str | None = None  # 摘要覆盖到的最后 query_id

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Session:
        return cls(**{k: r[k] for k in cls.model_fields})


class ClarifyPref(BaseModel):
    id: int
    datasource_id: int
    term: str
    chosen_rule: str
    created_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> ClarifyPref:
        return cls(**{k: r[k] for k in cls.model_fields})


class Query(BaseModel):
    id: str  # q_ + ULID = client_request_id = thread_id
    session_id: str | None = None
    datasource_id: int | None = None
    dialect: str | None = None
    question: str
    contract: dict[str, Any] | None = None
    sql_final: str | None = None
    status: str = "running"  # running|waiting_clarify|waiting_confirm|done|failed|stopped
    rounds: int = 0
    tokens: int = 0
    scan_rows: int | None = None
    data_max_time: str | None = None
    duration_ms: int | None = None
    result_unverified: int = 0
    clarify_choice: str | None = None
    recovered: int = 0  # D03 自动续跑标记（0006；E5 透出驱动恢复横幅）
    created_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Query:
        keys = set(r.keys())
        return cls(
            id=r["id"],
            session_id=r["session_id"],
            datasource_id=r["datasource_id"],
            dialect=r["dialect"],
            question=r["question"],
            contract=_j(r["contract_json"], None),
            sql_final=r["sql_final"],
            status=r["status"],
            rounds=r["rounds"],
            tokens=r["tokens"],
            scan_rows=r["scan_rows"],
            data_max_time=r["data_max_time"],
            duration_ms=r["duration_ms"],
            result_unverified=r["result_unverified"],
            clarify_choice=r["clarify_choice"],
            recovered=r["recovered"] if "recovered" in keys else 0,
            created_at=r["created_at"],
        )


class ResultPreview(BaseModel):
    query_id: str
    columns: list[dict[str, str]] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    truncated: int = 0  # rows_json 截断 50KB 标记

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> ResultPreview:
        return cls(
            query_id=r["query_id"],
            columns=_j(r["columns_json"], []),
            rows=_j(r["rows_json"], []),
            truncated=r["truncated"],
        )


class QueryEvent(BaseModel):
    query_id: str
    seq: int  # 单调递增，续播接缝
    type: str  # C3.2 十六事件（stream_resumed 不入表）
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> QueryEvent:
        return cls(
            query_id=r["query_id"],
            seq=r["seq"],
            type=r["type"],
            payload=_j(r["payload"], {}),
        )


class Sample(BaseModel):
    id: int
    datasource_id: int
    question: str
    question_norm: str  # 小写/去空白/去标点
    sql: str
    status: str = "on"  # on|stale|off
    source: str = "manual"
    semantic_rev: str | None = None  # 五因子 sha256（D-15）
    validated_at: str | None = None
    stale_reason: str | None = None  # 记字段名
    hit_count: int = 0
    created_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Sample:
        return cls(**{k: r[k] for k in cls.model_fields})


# ---------- 提供商 / 记忆 ----------


class Provider(BaseModel):
    id: int
    name: str
    type: str
    protocol: str = "openai_chat"  # openai_chat|anthropic|ollama
    base_url: str | None = None
    models: list[str] = Field(default_factory=list)
    enabled: int = 1
    created_at: str | None = None
    has_api_key: bool = False  # 密文永不回显

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Provider:
        return cls(
            id=r["id"],
            name=r["name"],
            type=r["type"],
            protocol=r["protocol"],
            base_url=r["base_url"],
            models=_j(r["models_json"], []),
            enabled=r["enabled"],
            created_at=r["created_at"],
            has_api_key=r["api_key_enc"] is not None,
        )


class MemoryLog(BaseModel):
    id: int
    datasource_id: int | None = None  # NULL=全局
    kind: str  # caliber|correction|quirk|preference
    fact: str
    source_query_id: str | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> MemoryLog:
        return cls(**{k: r[k] for k in cls.model_fields})


class Memory(BaseModel):
    id: int
    scope: str = "global"  # global|datasource
    datasource_id: int | None = None
    topic: str
    content: str
    enabled: int = 1  # 停用即不注入
    token_est: int = 0
    updated_at: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Memory:
        return cls(**{k: r[k] for k in cls.model_fields})
