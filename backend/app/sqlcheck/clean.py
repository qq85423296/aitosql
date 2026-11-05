"""Unicode 清洗 + 脱敏规则表（D-11 / SSOT §12.5，三出口统一）。

三出口：回喂 LLM 的错误 / 前端回显 / query_event 落库——全部经本模块，单点实现防漂移。
规则：连接串/主机/端口 → [HOST]/[PORT]；账号/密码 → [USER]/[CREDENTIAL]；
IP 白名单仅放行 127.0.0.1/localhost 字面量；SQL 字面值 → 占位符 `?`。
"""

from __future__ import annotations

import re

# 零宽/bidi（与 ingest/sanitize.py 同源口径；本模块面向 SQL 文本清洗）
_CLEAN_CHARS = {
    "​",
    "‌",
    "‍",
    "⁠",
    "﻿",
    "᠎",
    "­",
    "‪",
    "‫",
    "‬",
    "‭",
    "‮",
    "⁦",
    "⁧",
    "⁨",
    "⁩",
    "‎",
    "‏",
    "⁪",
}

# F2：TLD 白名单扩列（org/dev/ai/cloud/top/xyz/info/me/cc/lan/corp 等常见内网
# 与公网后缀）+ 驱动连接消息结构化提取（_DRIVER_HOST_RE，任意后缀/任意主机形态
# 均脱敏）——仅白名单会让 .org/.dev 等主机名原样泄漏；白名单本身保留是为了
# 不误伤 SQL 错误里的 db.table 限定名（修正回路需要表名回喂 LLM）。
_HOST_PORT_RE = re.compile(
    r"\b[\w.-]+\.(?:com|cn|net|io|local|internal|org|dev|ai|cloud|top|xyz|info|me|cc|lan|corp)(:\d+)?\b"
)
# pymysql 连接错误消息中的主机提取：2003/2005/2013/2055 族
#   "Can't connect to MySQL server on 'host' (…)"
#   "Unknown MySQL server host 'host' (…)"
#   "Lost connection to MySQL server at 'host:port'" / "… at 'reading …'"
_DRIVER_HOST_RE = re.compile(
    r"(?i)((?:can't connect to[\w ]*?server on|unknown[\w ]*?server host|"
    r"lost connection to[\w ]*?server at)\s*')([^']+)(')"
)
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b")
_USER_RE = re.compile(r"(?i)\b(user(?:name)?|uid)\s*[=:'\"]\s*([\w.-]+)")
_PWD_RE = re.compile(r"(?i)\b(pass(?:word|wd)?|pwd|secret|token|api[_-]?key)\s*[=:'\"]\s*([^\s'\"]+)")
_URL_CRED_RE = re.compile(r"://([^:/@\s]+):([^@/\s]+)@")

_IP_ALLOWLIST = {"127.0.0.1", "localhost"}


def unicode_clean(sql: str) -> str:
    """Unicode 清洗：剔除零宽字符与 bidi 控制符（validate 执行序第 1 步）。"""
    return "".join(ch for ch in sql if ch not in _CLEAN_CHARS)


def mask_error(message: str) -> str:
    """错误脱敏（引擎报错回喂/落库/回显统一入口）。"""
    if not message:
        return ""
    text = str(message)
    text = _URL_CRED_RE.sub(r"://[USER]:[CREDENTIAL]@", text)
    text = _PWD_RE.sub(lambda m: f"{m.group(1)}=[CREDENTIAL]", text)
    text = _USER_RE.sub(lambda m: f"{m.group(1)}=[USER]", text)

    def _mask_driver_host(m: re.Match) -> str:
        host = m.group(2)
        head = host.split(":", 1)[0]
        if head in _IP_ALLOWLIST:
            return m.group(0)  # 白名单字面量放行
        return f"{m.group(1)}[HOST]{m.group(3)}"

    text = _DRIVER_HOST_RE.sub(_mask_driver_host, text)

    def _mask_ip(m: re.Match) -> str:
        token = m.group(0)
        host = token.split(":", 1)[0]
        if host in _IP_ALLOWLIST:
            return token  # 白名单字面量放行
        return "[HOST]:[PORT]" if ":" in token else "[HOST]"

    text = _IP_RE.sub(_mask_ip, text)
    text = _HOST_PORT_RE.sub(lambda m: "[HOST]:[PORT]" if m.group(1) else "[HOST]", text)
    return text


def mask_sql_literals(sql: str, dialect: str | None = None) -> str:
    """SQL 字面值 → 占位符 `?`（AST 变换；解析失败原样返回，由 R15 B2 另行把关）。"""
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read=_dialect_arg(dialect))
    except Exception:
        return sql

    def _replace(node: exp.Expr) -> exp.Expr:
        if isinstance(node, exp.Literal):
            return exp.Placeholder()
        return node

    return tree.transform(_replace).sql(dialect=_dialect_arg(dialect) or "")


def _dialect_arg(dialect: str | None) -> str | None:
    """sqlglot 方言名映射（doris 走 mysql 词法族）。"""
    return {"mysql": "mysql", "doris": "mysql", "clickhouse": "clickhouse"}.get(dialect or "", dialect)


def classify_unexpected(exc: Exception) -> str:
    """runner 泛化兜底异常 → 错误码映射（修复回路 Q-3）。

    sqlglot ParseError → E3002（校验类）；超时族（内置/httpx Timeout*）→ E2001
    （端点无响应，retryable）；其余 → E5000 服务内部错误。BizError 透传原码由
    调用方先行分支处理，本函数只承接非业务异常。
    sqlglot import 收敛本目录（D3）；httpx 不引依赖——按类名判定超时族。
    """
    from sqlglot.errors import ParseError

    from app.errors import E_INTERNAL, E_MODEL_DOWN, E_SQL_PARSE

    if isinstance(exc, ParseError):
        return E_SQL_PARSE
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return E_MODEL_DOWN
    return E_INTERNAL
