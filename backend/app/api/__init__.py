"""L1 路由层（design D-30）。

边界：不含业务规则；查询类写操作只调 graph/runner；纯 CRUD/只读可直达 store（D1 补丁）。
全部端点 `/api` 前缀 + Bearer 校验；响应 envelope `{code, message, data}`（C0）。
"""

from fastapi import APIRouter

from app.api import (
    ai_runs,
    backup,
    datasources,
    dimensions,
    health,
    memory,
    metrics,
    providers,
    queries,
    relations,
    samples,
    sessions,
    settings,
    sse,
    standards,
    suggestions,
    tables,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(sessions.router)
api_router.include_router(datasources.router)
api_router.include_router(tables.router)
api_router.include_router(relations.router)
api_router.include_router(standards.router)
api_router.include_router(dimensions.router)
api_router.include_router(metrics.router)
api_router.include_router(suggestions.router)
api_router.include_router(ai_runs.router)
api_router.include_router(samples.router)
api_router.include_router(queries.router)
api_router.include_router(sse.router)
api_router.include_router(providers.router)
api_router.include_router(settings.router)
api_router.include_router(memory.router)
api_router.include_router(backup.router)
