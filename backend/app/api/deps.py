"""API 公共依赖：Bearer 校验、envelope 助手、同步 DAO 执行桥（D-02）。

- Bearer token = setting `server.token`（首启生成，仅 CLI 打印一次）；
- 同步 store 调用统一经 `run()`（anyio.to_thread）隔离出事件循环；
- 路由层不 try-catch：业务异常抛 BizError，由全局处理器收口（技能规范正反例）。
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any, TypeVar

import anyio
from fastapi import Depends, Header

from app.errors import E_UNAUTHORIZED, BizError
from app.store import db
from app.store import settings as store_settings

T = TypeVar("T")


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功 envelope（C0：code=0 / message=ok / data）。"""
    return {"code": 0, "message": message, "data": data}


async def run[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """同步 IO 隔离桥（D-02）：store/引擎等同步调用一律经此入线程池。"""
    return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))


def _check_token(authorization: str | None) -> None:
    with db.get_conn() as conn:
        if not store_settings.get_setting(conn, "server.auth"):
            return  # server.auth=off：本地单用户免 token（设置页可关）
        expected = store_settings.get_setting(conn, "server.token")
    if not authorization or not authorization.startswith("Bearer "):
        raise BizError(E_UNAUTHORIZED, "缺少 Bearer token", 401)
    token = authorization.removeprefix("Bearer ").strip()
    # S-6：定常时比较（防时序侧信道）
    if not expected or not hmac.compare_digest(token, str(expected)):
        raise BizError(E_UNAUTHORIZED, "Bearer token 校验失败", 401)


async def require_token(authorization: str | None = Header(default=None)) -> None:
    """Bearer 校验依赖（全部 /api 端点挂载，C0）。"""
    await run(_check_token, authorization)


# 供 router 统一挂载：dependencies=[Depends(require_token)] 由 api/__init__ 聚合层注入
TokenDep = Depends(require_token)
