"""提供商域请求模型（C1.6）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: str = Field(default="custom", max_length=50)
    protocol: str = Field(default="openai_chat", pattern="^(openai_chat|anthropic|ollama)$")
    base_url: str | None = None
    api_key: str | None = None  # 仅入站；落库 Fernet 密文，响应永不回显
    models: list[str] = Field(default_factory=list)


class ProviderPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    protocol: str | None = Field(default=None, pattern="^(openai_chat|anthropic|ollama)$")
    base_url: str | None = None
    api_key: str | None = None  # 显式传入才更新密文列
    models: list[str] | None = None
    enabled: bool | None = None


class ModelsProbeIn(BaseModel):
    """免保存拉模型目录（抽屉草稿态直连）：密钥仅本次内存使用，不落库。"""

    protocol: str = Field(default="openai_chat", pattern="^(openai_chat|anthropic|ollama)$")
    base_url: str = Field(min_length=1)
    api_key: str | None = None
