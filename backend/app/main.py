"""FastAPI 装配（design D-30/D-42）。

启动钩子顺序（lifespan）：
    单实例锁（阶段 D：标准启动直通 / 直接托管自持，冲突拒启）
    → 数据目录初始化（0600）→ migrations 幂等执行（失败拒启报迁移号）→ setting 默认值
    → server.token 首启确保 → 密钥链自检（fail-closed，D-10）
    → bge 权重存在性检测（缺失则 schema.vector=off 降级纯 FTS，T0.5）
    → exports 孤儿清扫 + TTL/LRU（D-16）→ 备份定时任务（D-17）
    → D03 启动清扫（running 自动续跑 / waiting_* 保持门态 / checkpoint 缺失 failed+标注）
CORS/Origin 限定 127.0.0.1（C1）；静态 web/dist 存在则内嵌挂载（T4.4）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.types import Scope

from app import __version__, config, instance_lock
from app.api import api_router
from app.errors import register_exception_handlers
from app.store import db
from app.store import settings as store_settings

logger = logging.getLogger("aitosql.main")


def _startup_sync() -> None:
    """启动同步段：目录/迁移/默认值/token/密钥自检（同步 IO，由 lifespan 直接调用）。"""
    config.ensure_data_dirs()
    applied = db.run_migrations()  # 失败抛 MigrationError → 拒启（D-39）
    if applied:
        logger.info("本次应用迁移: %s", applied)
    with db.get_conn() as conn:
        store_settings.ensure_defaults(conn)
        if not store_settings.get_setting(conn, "server.token"):
            # dev.py 首启路径已生成则不会走到这；兜底生成（不打印，防日志泄漏）
            import secrets as _secrets

            store_settings.save_setting(conn, "server.token", _secrets.token_urlsafe(24))
            logger.warning("server.token 兜底生成（首启请优先走 scripts/dev.py 打印一次）")


async def _startup_async(app: FastAPI) -> None:
    """启动异步段：密钥自检 / 权重检测 / 清扫 / 定时任务 / D03。"""
    from app.llm import secrets as llm_secrets

    llm_secrets.ensure_key_ready()  # keyring 不可用且无环境变量 → fail-closed 拒启（D-10）

    from app.retrieval import vector

    await vector.check_weights_or_degrade()  # 权重缺失 → schema.vector=off + 提示（T0.5）

    from app.exec import meta as exec_meta
    from app.store import backup as store_backup

    await exec_meta.sweep_orphans_async()
    app.state.backup_task = asyncio.create_task(store_backup.backup_loop())

    from app.graph import runner

    await runner.init_runner()  # checkpointer（checkpoints.db，独立文件 D-01）
    await runner.startup_sweep()  # D03 自动续跑/门态保持/checkpoint 缺失降级


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # 单实例契约（阶段 D）：迁移/清扫等启动副作用之前先持锁——标准启动
    # （scripts/dev.py）已持锁直通；直接 ASGI 托管自行持锁，冲突即启动失败
    held = instance_lock.try_hold_for_lifespan()
    app.state.instance_lock = held
    try:
        _startup_sync()
        await _startup_async(app)
        yield
    finally:
        task = getattr(app.state, "backup_task", None)
        if task is not None:
            task.cancel()
        try:
            from app.graph import runner

            await runner.shutdown_runner()
        finally:
            if held is not None:
                held.close()  # 启动失败或正常关闭时均释放 OS 级锁


def create_app() -> FastAPI:
    app = FastAPI(title="AItosql", version=__version__, lifespan=lifespan)
    register_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{config.listen_port()}",
            f"http://localhost:{config.listen_port()}",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )
    app.include_router(api_router)
    dist = config.web_dist_dir()
    if dist.is_dir():
        app.mount("/", _SpaStaticFiles(directory=str(dist), html=True), name="web")
    return app


class _SpaStaticFiles(StaticFiles):
    """静态托管 + SPA 回退：无扩展名的 GET/HEAD 路径 404 时回 index.html。

    直达/刷新子路由不再返回裸 `{"detail":"Not Found"}`；带扩展名的资源
    （/assets/*.js 等）404 仍如实返回，避免错误 MIME 污染页面。
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            last = path.rsplit("/", 1)[-1]
            raw_path = str(scope.get("path", ""))
            is_api = raw_path == "/api" or raw_path.startswith("/api/")
            if (
                exc.status_code == 404
                and scope["method"] in ("GET", "HEAD")
                and "." not in last
                and not is_api
            ):
                return await super().get_response("index.html", scope)
            raise


app = create_app()
