"""样例域请求模型（C1.4）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SampleCreateIn(BaseModel):
    datasource_id: int
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    source: str = Field(default="manual", max_length=50)


class SamplePatchIn(BaseModel):
    question: str | None = Field(default=None, min_length=1)
    sql: str | None = Field(default=None, min_length=1)
    status: str | None = Field(default=None, pattern="^(on|stale|off)$")
