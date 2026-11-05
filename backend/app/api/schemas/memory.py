"""记忆域请求模型（C1.6：总账管理 / 流水账只读 / 手动合并）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryCreateIn(BaseModel):
    scope: str = Field(default="global", pattern="^(global|datasource)$")
    datasource_id: int | None = None
    topic: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)


class MemoryPatchIn(BaseModel):
    topic: str | None = Field(default=None, min_length=1, max_length=100)
    content: str | None = Field(default=None, min_length=1)


class MemoryToggleIn(BaseModel):
    """启停开关（P1-8）：`enabled` 缺省时服务端读当前态取反（前端 toggle(id) 无 body
    调用与显式 `{enabled: bool}` 调用均可）。整个请求体亦可省略。"""

    enabled: bool | None = None
