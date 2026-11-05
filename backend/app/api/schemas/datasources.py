"""数据源域请求模型（C1.2）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DatasourceCreateIn(BaseModel):
    """POST `{name, dialect, mode, host?, port?, db_name?, username?, password?}`；
    响应永不回显 password（store 层模型 has_password 替代）。"""

    name: str = Field(min_length=1, max_length=100)
    dialect: str = Field(pattern="^(mysql|doris|clickhouse)$")
    mode: str = Field(pattern="^(online|offline)$")
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    db_name: str | None = None
    username: str | None = None
    password: str | None = None  # 仅入站；落库前 Fernet 加密，永不回显


class DatasourcePatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    db_name: str | None = None
    username: str | None = None
    password: str | None = None  # 显式传入才更新密文列


class DdlPreviewIn(BaseModel):
    ddl: str = Field(min_length=1)


class GraphLayoutIn(BaseModel):
    """摆位保存（D-18）：{table_name: {x, y}}。"""

    node_positions: dict[str, dict[str, float]] = Field(default_factory=dict)


class InstructionIn(BaseModel):
    """每数据源口径说明（P5）：整文覆盖保存，最大 64KB。"""

    content: str = Field(default="", max_length=65536)


class CsvTemplateIn(BaseModel):
    """元数据字典模板通道：11 列中文表头（必填 8 列），行 = 某表一个字段。"""

    csv_text: str = Field(min_length=1, max_length=1024 * 1024)


class AiDraftIn(BaseModel):
    """AI 草稿通道（FR-D13/R-1）：自然语言描述 → route.ai 生成 DDL 入库。"""

    hint: str = Field(min_length=1, max_length=4096)
