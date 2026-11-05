"""设置域请求模型（C1.6 / C7 全键校验由 store/settings.py 承载）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SettingsPutIn(BaseModel):
    """PUT /settings：{key: value} 批量写入；未知键/类型不符 → 422（store 校验）。"""

    values: dict[str, Any] = Field(default_factory=dict)
