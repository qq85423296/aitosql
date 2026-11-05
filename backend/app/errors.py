"""业务异常与错误码（contracts.md C8 + 全局异常处理器）。

envelope 统一形状：``{"code": ..., "message": ..., "data": ...}``（C0，证据 = 原型 data/data.json）。
成功：code=0 / message="ok"；失败：code 取 C8 错误码。

编码期补充码（contracts C8 未覆盖的通用 HTTP 语义，待回写 C8）：
    E4010 未授权（Bearer 缺失/不符）、E4040 资源不存在、
    E4090 状态冲突（CAS 拒绝，如非 waiting_* 态 resume）、E4420 参数校验失败。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("aitosql.errors")

# --- C8 错误码（SSOT §18.1 原文） ---
E_DS_CONNECT = "E1001"  # 数据源连接失败
E_INGEST_INTERRUPT = "E1002"  # 摄取中断（可续摄）
E_MODEL_DOWN = "E2001"  # 模型端点无响应
E_CONTRACT_PARSE = "E2002"  # 契约解析失败（重试后）
E_EXPLAIN = "E3001"  # EXPLAIN 不支持/失败
E_HARD_BLOCK = "E3010"  # R15 硬阻断
E_DECRYPT = "E4001"  # 密钥解密失败

# --- 编码期补充码（回写清单 §8 R-5 关联项；修复回路增补 E2004/E3002/E5000） ---
E_UNAUTHORIZED = "E4010"
E_NOT_FOUND = "E4040"
E_CONFLICT = "E4090"
E_BAD_REQUEST = "E4420"
E_RECOVERY = "E5001"  # 崩溃恢复失败标注（checkpoint 缺失/损坏，D-01/D-17 语义）
E_FIX_EXHAUSTED = "E2004"  # 修正轮数耗尽（P1-14：不得以 done 静默交付）
E_SQL_PARSE = "E3002"  # SQL 解析失败（校验类；runner 异常映射用，Q-3）
E_INTERNAL = "E5000"  # 服务内部错误（未映射异常的兜底码）
E_AI_RUN_CONFLICT = "E3101"  # AI 补全任务冲突（同数据源已有进行中任务，方案 v1.1 §8）


class BizError(Exception):
    """业务异常：路由/服务层抛出，由全局处理器统一转 envelope。严禁吞异常。"""

    def __init__(self, code: str, message: str, http_status: int = 400, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data


def error_payload(code: str, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data}


def register_exception_handlers(app: FastAPI) -> None:
    """全局异常处理器：envelope 收口（项目侧注册一次）。"""

    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=error_payload(exc.code, exc.message, exc.data),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        msg = f"参数校验失败: {loc} {first.get('msg', '')}".strip()
        return JSONResponse(status_code=422, content=error_payload(E_BAD_REQUEST, msg))

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # 堆栈只进服务端日志，严禁返回给调用方
        logger.exception("unhandled error: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_payload("E5000", "服务内部错误"),
        )
