"""通用契约件（C0 envelope 形状 = 原型 data/data.json 顶层，置信度项 R-5）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Envelope(BaseModel):
    """响应 envelope：`{code, message, data}`；成功 code=0/message=ok，错误 code 取 C8。"""

    code: int | str = 0
    message: str = "ok"
    data: Any = None
