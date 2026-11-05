"""阶段 D 单实例运行契约：锁冲突拒绝 / 启动器锁登记 / 释放再获 / dev.py 友好退出。

隔离说明：全程 AITOSQL_DATA 指向 tmp_path，绝不持有真实用户数据目录锁；
不 fork 真实子进程（同进程二次抢锁即可复现跨进程冲突语义：
Windows msvcrt 与 POSIX flock 均按打开句柄互斥）。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app import config, instance_lock


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(config.ENV_DATA, str(tmp_path))
    return tmp_path


def _load_dev_script() -> ModuleType:
    """按文件路径加载 scripts/dev.py（scripts 非包，不经 import 系统）。"""
    spec = importlib.util.spec_from_file_location(
        "aitosql_dev_script", Path(__file__).resolve().parents[2] / "scripts" / "dev.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lock_conflict_refused_with_context(tmp_data: Path) -> None:
    first = instance_lock.acquire_lock()
    try:
        with pytest.raises(instance_lock.InstanceLockError) as exc_info:
            instance_lock.acquire_lock()  # 第二实例（同/跨进程同语义）被拒
        msg = str(exc_info.value)
        assert str(tmp_data.resolve()) in msg  # 异常带数据目录上下文
        if os.name != "nt":
            # Windows 持锁期间锁定区对其他句柄读写全拒，PID 仅在释放后可读
            assert str(os.getpid()) in msg  # 持锁者 PID 可溯源
    finally:
        first.close()


def test_lock_released_then_reacquirable(tmp_data: Path) -> None:
    first = instance_lock.acquire_lock()
    first.close()  # 句柄关闭即释放（模拟进程退出）
    second = instance_lock.acquire_lock()
    second.close()


def test_lock_creates_missing_data_directory(
    tmp_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直接 ASGI 首启时数据目录尚不存在，锁逻辑应先创建父目录。"""
    data_dir = tmp_data / "not-created" / "data"
    monkeypatch.setenv(config.ENV_DATA, str(data_dir))
    assert not data_dir.exists()
    held = instance_lock.acquire_lock()
    try:
        assert (data_dir / instance_lock.LOCK_FILE).is_file()
    finally:
        held.close()


def test_lock_file_records_holder_pid(tmp_data: Path) -> None:
    fh = instance_lock.acquire_lock()
    fh.close()
    assert (tmp_data / instance_lock.LOCK_FILE).read_text(encoding="ascii").strip() == str(os.getpid())


def test_launcher_lock_registration_allows_lifespan_passthrough(tmp_data: Path) -> None:
    """标准启动登记进程内锁后，lifespan 直通但不二次抢锁。"""
    held = instance_lock.acquire_lock()
    instance_lock.register_launcher_lock(held)
    try:
        assert instance_lock.try_hold_for_lifespan() is None
    finally:
        instance_lock.release_launcher_lock(held)


def test_forged_environment_cannot_bypass_lifespan_lock(
    tmp_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧环境变量即使被伪造，也不能让 lifespan 跳过文件锁。"""
    monkeypatch.setenv("AITOSQL_LOCK_HELD", "self")
    held = instance_lock.try_hold_for_lifespan()
    assert held is not None
    try:
        with pytest.raises(instance_lock.InstanceLockError):
            instance_lock.try_hold_for_lifespan()
    finally:
        held.close()


def test_lifespan_self_hold_blocks_second_worker(tmp_data: Path) -> None:
    """直接 ASGI 托管：首个 worker 自持锁；第二 worker 启动即明确失败。"""
    held = instance_lock.try_hold_for_lifespan()
    assert held is not None
    try:
        with pytest.raises(instance_lock.InstanceLockError):
            instance_lock.try_hold_for_lifespan()
    finally:
        held.close()


def test_dev_script_conflict_prints_and_exits_2(tmp_data: Path, capsys: pytest.CaptureFixture) -> None:
    held = instance_lock.acquire_lock()
    try:
        dev = _load_dev_script()
        with pytest.raises(SystemExit) as exc_info:
            dev._acquire_single_instance_lock()
        assert exc_info.value.code == 2
        assert "拒绝重复启动" in capsys.readouterr().err
    finally:
        held.close()


def test_dev_script_registers_lock_held(tmp_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """dev.py 使用同一把进程内登记锁，lifespan 不再自我冲突。"""
    monkeypatch.setattr(sys, "argv", ["dev.py"])
    dev = _load_dev_script()
    fh = dev._acquire_single_instance_lock()
    instance_lock.register_launcher_lock(fh)
    try:
        assert instance_lock.try_hold_for_lifespan() is None
    finally:
        instance_lock.release_launcher_lock(fh)


@pytest.mark.asyncio
async def test_lifespan_releases_lock_when_startup_fails(
    tmp_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动异常发生在 yield 前也必须释放锁，避免残留句柄阻塞重启。"""
    from app import main
    from app.graph import runner

    async def no_op_shutdown() -> None:
        return None

    def fail_startup() -> None:
        raise RuntimeError("injected startup failure")

    monkeypatch.setattr(main, "_startup_sync", fail_startup)
    monkeypatch.setattr(runner, "shutdown_runner", no_op_shutdown)
    app = main.create_app()

    with pytest.raises(RuntimeError, match="injected startup failure"):
        async with main.lifespan(app):
            pytest.fail("启动失败不应进入 lifespan 主体")

    reacquired = instance_lock.acquire_lock()
    reacquired.close()
