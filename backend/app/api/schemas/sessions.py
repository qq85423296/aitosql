"""会话域请求模型（C1.1）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionCreateIn(BaseModel):
    datasource_id: int | None = None
    title: str | None = Field(default=None, max_length=200)


class SessionPatchIn(BaseModel):
    """PATCH `{title?, pinned?, archived?}`；重命名后首问不再自动改题（store 层口径）。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None
