"""语义层请求模型（C1.3）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class StandardIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    data_type: str | None = None
    unit: str | None = None
    value_desc: str | None = None
    dimension_standard_id: int | None = None
    description: str | None = None


class DimensionIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class DimensionValueIn(BaseModel):
    value: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    sort: int = 0


class MetricIn(BaseModel):
    """指标保存（保存时 sqlglot 校验，未知标识符黄警不阻断——warnings 随响应返回）。"""

    name: str = Field(min_length=1, max_length=100)
    expr: str = Field(min_length=1)
    filter: str | None = None  # D-19：保留列名，全链路引号/参数化
    base_tables: str | None = None
    description: str | None = None
