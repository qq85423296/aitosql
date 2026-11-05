"""单实例运行契约（阶段 D）：数据目录 `.aitosql.lock` OS 级非阻塞互斥。

两条持锁路径共用同一文件锁，互为互斥：
1. 标准启动 `scripts/dev.py`：父进程 `acquire_lock()` 持锁，并通过进程内
   `register_launcher_lock()` 告知同进程 uvicorn 应用「锁已在手」；
2. 直接 uvicorn/ASGI 托管：lifespan 调 `try_hold_for_lifespan()` 自行持锁——
   多 worker 部署时仅首个 worker 得锁，其余启动即明确失败（拒绝静默多实例：
   双实例会双份 D03 续跑、跨进程 stop→KILL 寻址失败、双份 backup_loop）。

锁随进程退出由 OS 释放；应用自持锁在 lifespan 收尾显式关闭。
限制说明：无法可靠探测所有 ASGI 部署方式（如外部进程管理器自行 fork），
强制运行时检查以外的部署约束以 README 文档为准。
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import IO

from app import config

LOCK_FILE = ".aitosql.lock"

# PID 定长 20 字节左对齐空格填充，原地覆写不截断（Windows 同进程冲突路径上
# truncate 会先毁持锁者记录再报错——覆写语义下首句柄记录全程完好）。
_PID_FIELD = 20
_launcher_lock: IO[bytes] | None = None


class InstanceLockError(RuntimeError):
    """数据目录已被另一 AItosql 实例持有（单实例运行契约拒绝启动）。"""


def _read_holder(lock_path: Path) -> str | None:
    """尽力读取持锁者 PID（诊断用）。Windows 持锁期间锁定区对其他句柄
    读写全拒（含本读取）→ 返回 None；POSIX flock 不拦读 → 可读出。"""
    try:
        with open(lock_path, "rb") as fh:
            return fh.read(_PID_FIELD).decode("ascii").strip() or None
    except OSError:
        return None


def acquire_lock() -> IO[bytes]:
    """非阻塞抢锁；冲突抛 InstanceLockError（含数据目录与持锁 PID 上下文）。

    返回文件句柄——进程存续期间保持打开即持锁，退出由 OS 自动释放。
    冲突检测双通道（Windows 字节范围锁为强制锁且按进程互斥）：
    - 跨进程：msvcrt.locking / flock 直接报错；
    - 同进程第二句柄：locking 放行（进程级语义），但写 PID 区与首句柄
      锁定区重叠 → PermissionError——PID 字段兼任冲突金丝雀。
    整个「锁 + 写入」序列收口 OSError → InstanceLockError，两平台语义一致。
    """
    lock_path = config.data_dir() / LOCK_FILE
    # direct ASGI startup reaches the lifespan lock before _startup_sync creates
    # the data directory; create only the parent here so first boot can acquire
    # the mutex without running migrations or other startup side effects.
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # r+b/w+b（非追加模式）：Windows 追加模式写操作强制落 EOF，金丝雀写会
    # 错过锁定字节导致同进程冲突漏检；读写模式 seek 后写位置确定。
    try:
        fh = open(lock_path, "r+b")  # noqa: SIM115 —— 句柄须贯穿进程生命周期
    except FileNotFoundError:
        fh = open(lock_path, "w+b")  # noqa: SIM115 —— 同上
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            # fcntl 仅 POSIX 可用：importlib 动态加载（Windows 上 mypy 无法解析静态 import）
            import importlib

            fcntl = importlib.import_module("fcntl")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.seek(0)
        fh.write(f"{os.getpid():<{_PID_FIELD}}".encode("ascii"))
        fh.flush()
    except OSError as exc:
        # close 可能因重放未决写（Windows 锁定区域写拒绝）二次报错，须吞掉保诊断链
        with contextlib.suppress(OSError):
            fh.close()
        holder = _read_holder(lock_path)
        where = f"数据目录 {config.data_dir()}"
        if holder:
            where += f"，持锁 PID {holder}"
        raise InstanceLockError(f"已有 AItosql 实例在运行（{where}），拒绝重复启动") from exc
    return fh


def register_launcher_lock(lock: IO[bytes]) -> None:
    """登记由当前进程启动脚本持有的锁，供同进程 lifespan 安全直通。

    不使用环境变量传递此状态：环境可被调用者任意设置，也会被子进程继承，
    从而绕过 lifespan 的单实例互斥。
    """
    global _launcher_lock
    if lock.closed:
        raise ValueError("不能登记已关闭的 AItosql 实例锁")
    if _launcher_lock is not None and not _launcher_lock.closed and _launcher_lock is not lock:
        raise InstanceLockError("当前进程已登记另一把 AItosql 启动锁")
    _launcher_lock = lock


def release_launcher_lock(lock: IO[bytes]) -> None:
    """撤销启动锁登记并关闭句柄；允许 finally 重复调用。"""
    global _launcher_lock
    if _launcher_lock is lock:
        _launcher_lock = None
    if not lock.closed:
        lock.close()


def try_hold_for_lifespan() -> IO[bytes] | None:
    """lifespan 自持锁入口：返回 None 表示锁已由启动脚本在本进程登记持有。

    直接 ASGI 托管（无登记锁）→ 尝试自持锁；冲突抛 InstanceLockError，
    使 uvicorn 启动明确失败（多 worker 仅首个得锁，其余崩溃退出）。
    """
    if _launcher_lock is not None and not _launcher_lock.closed:
        return None
    return acquire_lock()
