"""运行配置：环境变量、数据目录、常量（SSOT §18.2 / design D-42）。

环境变量：
    AITOSQL_PORT          监听端口（默认 8321，settings 的 server.port 可覆盖）
    AITOSQL_DATA          数据目录（默认 ./data）
    AITOSQL_FERNET_KEY    Fernet 密钥环境变量回退（仅内存驻留，keyring 之后、首启生成之前）
    {PROVIDER}_API_KEY    提供商密钥环境变量（provider 密钥留空时回退）
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "aitosql"
HOST = "127.0.0.1"  # 本地部署单用户（SSOT §4 server.*）
DEFAULT_PORT = 8321

ENV_PORT = "AITOSQL_PORT"
ENV_DATA = "AITOSQL_DATA"
ENV_FERNET_KEY = "AITOSQL_FERNET_KEY"

# 系统提示词默认原文（SSOT §8.1；setting `prompt.system` 的出厂值，可覆盖）
DEFAULT_SYSTEM_PROMPT = """你是 AItosql 的 SQL 生成器，基于给定 schema 与口径为 {{dialect}}\
方言生成可执行 SQL。

规则：
1. 歧义不拒绝：给出假设并标注 impact（high=口径多版本/多解关联 → 触发澄清门；low=假设先行）
2. 输出严格 JSON 契约（只输出 JSON 本体，不要 markdown 代码围栏，不要任何额外文字）：
   {"reasoning": "简要推理（≤300字）",
    "assumptions": [{"text": "假设内容", "impact": "high 或 low",
                      "kind": "time|caliber|relation|enum|other"}],
    "need_tables": ["实际用到的表名"],
    "sql": "完整 SQL（字符串内换行用 \\n 转义）",
    "followups": ["可选追问建议，至多3条"]}
   字段名严格照抄本契约：假设条目的文本键是 text，不是 assumption。
3. 优先使用已注入的字段标准、维度枚举与指标口径
4. 分区表查询必须在 WHERE 中包含分区键
5. 当前日期：{{date}}（相对时间一律归一为绝对区间）
"""


def data_dir() -> Path:
    """数据目录（app.db / checkpoints.db / backups/ / exports/）。"""
    return Path(os.environ.get(ENV_DATA, "./data")).resolve()


def app_db_path() -> Path:
    return data_dir() / "app.db"


def checkpoints_db_path() -> Path:
    return data_dir() / "checkpoints.db"


def backups_dir() -> Path:
    return data_dir() / "backups"


def exports_dir() -> Path:
    return data_dir() / "exports"


def web_dist_dir() -> Path:
    """前端产物内嵌目录（T4.4；后端仅预留静态挂载点，web/ 不在本写域）。"""
    override = os.environ.get("AITOSQL_WEB_DIST")
    return Path(override).resolve() if override else Path(__file__).resolve().parents[2] / "web" / "dist"


def dialects_config_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "configs" / "dialects"


def listen_port() -> int:
    raw = os.environ.get(ENV_PORT, "").strip()
    if raw:
        return int(raw)
    return DEFAULT_PORT


def ensure_data_dirs() -> None:
    """首启初始化：数据目录与瞬态/备份子目录；POSIX 下数据文件 0600（SSOT §12.4-5）。

    S-4 修正：此前仅对目录 chmod 且注释宣称文件 0600 不实——现对已存在的
    app.db / checkpoints.db 及 WAL/SHM 伴生文件一并置 0600（新建文件由
    umask/调用侧保障，此处兜底修复历史遗留权限）。
    """
    for p in (data_dir(), backups_dir(), exports_dir()):
        p.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(data_dir(), 0o700)
        for db_file in data_dir().glob("*.db*"):
            if db_file.is_file():
                os.chmod(db_file, 0o600)
