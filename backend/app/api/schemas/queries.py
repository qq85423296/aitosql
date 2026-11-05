"""查询主链路请求/事件模型（C2 / C3 / C4.1）。

C4.1 生成契约的权威模型在 llm/prompts.py（GenerationContract，L3 生成域持有；
本文件仅承载 HTTP 入站模型与 SSE 事件形状文档化模型，避免 L3 反向依赖 L1）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ids import is_valid_biz_id


class QueryCreateIn(BaseModel):
    """POST /queries（C2）：client_request_id = thread_id = query_id（三键合一 D-22）。

    缺省时由前端生成 `q_`+ULID；服务端不做二次生成（避免键分裂）——缺失直接 422。
    """

    session_id: str = Field(min_length=1)
    datasource_id: int
    question: str = Field(min_length=1, max_length=4000)
    client_request_id: str = Field(min_length=1)

    def check_ids(self) -> None:
        from app.errors import E_BAD_REQUEST, BizError

        if not is_valid_biz_id(self.client_request_id):
            raise BizError(E_BAD_REQUEST, "client_request_id 须为 q_+ULID（前端生成，服务端不二次生成）", 422)


class ResumeIn(BaseModel):
    """门裁决（CAS）：`{gate:'clarify'|'confirm', choice?, choices?, custom?, approved?}`。

    clarify 支持三态（P4，借鉴 dsh-user-questions）：
    - choice：单选（向后兼容）；
    - choices：多选标签列表；
    - custom：自定义文本（可与 choices 叠加，DSH 语义「custom 补充 selected」）。
    """

    gate: Literal["clarify", "confirm"]
    choice: str | None = None
    choices: list[str] | None = None
    custom: str | None = None
    approved: bool | None = None

    def effective_choice(self) -> str:
        """合成最终口径文本：多选「、」连接 + custom 以「；补充：」追加。"""
        parts: list[str] = []
        selected = [c.strip() for c in (self.choices or []) if c and c.strip()]
        if self.choice and self.choice.strip():
            selected.insert(0, self.choice.strip())
        if selected:
            parts.append("、".join(dict.fromkeys(selected)))
        if self.custom and self.custom.strip():
            parts.append(f"；补充：{self.custom.strip()}" if parts else self.custom.strip())
        return "".join(parts)


class SaveSampleIn(BaseModel):
    """存为样例（缺省取查询的问题与终态 SQL）。"""

    question: str | None = None
    sql: str | None = None


class SteerIn(BaseModel):
    """运行中输入（P6，DSH inbox 语义）：steer=下一边界并入当轮引导；inject=补充上下文。"""

    content: str = Field(min_length=1, max_length=4000)
    kind: Literal["steer", "inject"] = "steer"


class SseEvent(BaseModel):
    """SSE 事件形状（C3.2 十六事件文档化模型；运行期逐字 JSON 推送）。"""

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
