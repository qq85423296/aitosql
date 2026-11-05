"""单命令启动（design D-42 / T0.5、T4.4 后端半）。

    uv run python scripts/dev.py

首启动作：
    1. 数据目录初始化（POSIX 0600/0700）；
    2. migrations 幂等执行（失败拒启报迁移号）；
    3. server.token 首启生成并 **仅 CLI 打印一次**（此后只回显是否已生成）；
    4. bge 权重存在性检测（缺失 → 提示下载 + schema.vector=off 降级纯 FTS）；
    5. uvicorn 起 127.0.0.1:8321（AITOSQL_PORT 可覆盖）。
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

# 允许从 05-backend/ 目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, instance_lock
from app.store import db
from app.store import settings as store_settings


def _acquire_single_instance_lock() -> object:
    """单实例锁（F11 根治；实现收口 app.instance_lock，与应用 lifespan 同文件互斥）。

    双实例同数据目录的实测后果：D03 启动清扫双份续跑（同一 running 查询被
    两个进程各跑一遍、事件/状态互踩）、跨进程 stop→KILL 寻址失败（CAS 已置
    stopped 但引擎查询继续跑）、backup_loop 双份、连接池/摄取断点各执一份。
    返回文件句柄——进程存续期间保持打开即持锁，退出由 OS 自动释放。
    """
    try:
        return instance_lock.acquire_lock()
    except instance_lock.InstanceLockError:
        print(f"已有 AItosql 实例在运行（数据目录 {config.data_dir()}），"
              "拒绝重复启动；若为残留进程请先终止。", file=sys.stderr)
        sys.exit(2)


def _check_port_free(port: int) -> None:
    """端口占用 → 友好报错退出（F11③；不再让 uvicorn 打栈）。"""
    import socket

    with socket.socket() as s:
        if s.connect_ex((config.HOST, port)) == 0:
            print(f"端口 {port} 已被占用（可能有残留实例或其他服务）；"
                  "确认进程归属后终止再启动，或用 AITOSQL_PORT 换端口。",
                  file=sys.stderr)
            sys.exit(2)


def first_boot() -> str | None:
    """迁移 + 默认值 + token 首启生成；返回新生成的 token（已存在则 None）。"""
    config.ensure_data_dirs()
    db.run_migrations()
    with db.get_conn() as conn:
        store_settings.ensure_defaults(conn)
        if store_settings.get_setting(conn, "server.token"):
            return None
        token = secrets.token_urlsafe(24)
        store_settings.save_setting(conn, "server.token", token)
        return token


def main() -> None:
    import uvicorn

    config.ensure_data_dirs()
    # 进程存续期间持有锁（局部变量随 main 帧存活，uvicorn.run 返回后才释放）；
    # 通过进程内登记告知 lifespan「锁已在手」，不使用可伪造/可继承的环境变量。
    _lock = _acquire_single_instance_lock()
    instance_lock.register_launcher_lock(_lock)
    try:
        port = config.listen_port()
        _check_port_free(port)
        token = first_boot()
        if token:
            print("=" * 60)
            print("AItosql 首启完成，本机 Bearer token（仅此一次打印，请妥善保存）：")
            print(f"  {token}")
            print("=" * 60)
        print(f"AItosql 监听 http://{config.HOST}:{port} （CORS/Origin 限定本机）")
        uvicorn.run("app.main:app", host=config.HOST, port=port, reload=False)
    finally:
        instance_lock.release_launcher_lock(_lock)


if __name__ == "__main__":
    main()
