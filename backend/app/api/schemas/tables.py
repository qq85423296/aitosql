"""表/字段域请求模型（C1.2；在线库字段名/类型只读——模型层不放行 name/type）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TablePatchIn(BaseModel):
    comment_custom: str | None = Field(default=None, max_length=500)
    partition_type: str | None = Field(default=None, pattern="^(month|day)$")
    row_estimate: int | None = Field(default=None, ge=0)


class ColumnPatchIn(BaseModel):
    comment_custom: str | None = Field(default=None, max_length=500)
    is_pk: bool | None = None
    is_partition: bool | None = None
    is_bucket: bool | None = None
    field_standard_id: int | None = None


class RelationCreateIn(BaseModel):
    datasource_id: int
    from_table: str = Field(min_length=1)
    from_col: str = Field(min_length=1)
    to_table: str = Field(min_length=1)
    to_col: str = Field(min_length=1)
    source: str = Field(default="manual", pattern="^(fk|inferred|manual)$")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
