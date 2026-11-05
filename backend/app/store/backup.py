"""备份（D-17 / SSOT §12.3，BR-15）。

- 每 4h `VACUUM INTO '{backup.dir}/app-{yyyyMMdd-HHmm}.db'`，滚动 keep=10；
- 入备份仅 app.db（含 query_event，密级「SQL 字面量与事件溯源明文」）；
  **checkpoints.db / exports/ 不入备份**；
- 密钥剥离：备份副本内 provider.api_key_enc / datasource.password_enc 写 NULL；
- `backup.encrypt=true` 时备份文件 Fernet 加密（后缀 .enc）；
- 恢复语义 ≠ 重启：恢复后 running 一律 failed+标注，不适用 D03 自动续跑
  （该提示由 api 层随 GET /backup/list 返回文案）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from app import config
from app.errors import E_BAD_REQUEST, BizError
from app.store import db
from app.store import settings as store_settings

logger = logging.getLogger("aitosql.store.backup")

BACKUP_INTERVAL_S = 4 * 3600  # 每 4h 滚动（SSOT §12.3）
RESTORE_HINT = "恢复后进行中的查询一律标记失败，不适用自动续跑"  # FR-C8 恢复提示原文


def _strip_secrets(backup_path: Path) -> None:
    """备份副本密文剥离（写 NULL，双封之一）。"""
    conn = sqlite3.connect(str(backup_path))
    try:
        conn.execute("UPDATE provider SET api_key_enc = NULL")
        conn.execute("UPDATE datasource SET password_enc = NULL")
        conn.commit()
    finally:
        conn.close()


def _encrypt_file(path: Path) -> Path:
    """可选加密（D08；不覆盖 exports/）。"""
    from app import credentials as llm_secrets

    enc = llm_secrets.get_fernet().encrypt(path.read_bytes())
    enc_path = path.with_suffix(path.suffix + ".enc")
    enc_path.write_bytes(enc)
    path.unlink()
    return enc_path


def _sql_quote(path: Path) -> str:
    """SQL 字符串字面量安全转义（S-5：单引号双写，防路径注入拼接）。"""
    return str(path).replace("'", "''")


def _resolve_backup_dir(conn: sqlite3.Connection) -> Path:
    """backup.dir 解析 + 白名单收敛（S-5-b：用户设置项属半可信输入）。

    相对路径按既有语义锚定 `data_dir().parent`（部署根：默认 `./data/backups/` 与
    自定义 AITOSQL_DATA 下均成立）；解析结果必须落在部署根树内，越界
    （`../..` 逃逸、绝对路径外溢至 /etc、系统目录等）一律 422 拒绝——
    VACUUM INTO 目标路径由此唯一产出，杜绝借设置项写任意路径。
    """
    root = config.data_dir().parent.resolve()  # 部署根（数据目录的父目录）
    backup_dir = Path(str(store_settings.get_setting(conn, "backup.dir") or ""))
    if not backup_dir.is_absolute():
        backup_dir = root / backup_dir
    resolved = backup_dir.resolve()
    if not resolved.is_relative_to(root):
        raise BizError(E_BAD_REQUEST, f"backup.dir 越界：{resolved}（仅允许部署根 {root} 内路径）", 422)
    return resolved


def _chmod_private(path: Path) -> None:
    """S-4：备份文件 0600（含密级数据副本；POSIX 生效，Windows 跳过）。"""
    if os.name == "posix":
        os.chmod(path, 0o600)


def create_backup() -> Path:
    """立即备份：VACUUM INTO → 密文剥离 → 可选加密 → 滚动清理。返回最终路径。

    S-5：文件名精确到秒（同一分钟连点两次备份不再同名覆盖）；
    目标目录经 _resolve_backup_dir 白名单收敛（越界 422）+ 路径引号双写转义。
    """
    with db.get_conn() as conn:
        backup_dir = _resolve_backup_dir(conn)  # S-5-b：白名单收敛（越界 422）
        keep = int(store_settings.get_setting(conn, "backup.keep"))
        encrypt = bool(store_settings.get_setting(conn, "backup.encrypt"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"app-{datetime.now():%Y%m%d-%H%M%S}.db"
        conn.execute(f"VACUUM INTO '{_sql_quote(target)}'")
    _chmod_private(target)
    _strip_secrets(target)
    if encrypt:
        target = _encrypt_file(target)
        _chmod_private(target)
    _rotate(backup_dir, keep)
    logger.info("备份完成: %s", target)
    return target


def _rotate(backup_dir: Path, keep: int) -> None:
    """滚动保留 keep 份（按文件名时间序，超出删最旧）。

    keep 下限钳制 1（删除点兜底）：keep<=0 语义在切片下会反向删除
    最新备份（keep=-1 → 仅留最旧），此处保证任何设置值都不致误删最新。
    写入侧另有 save_setting 最小值 422 校验（双保险）。
    """
    keep = max(1, keep)
    files = sorted(backup_dir.glob("app-*.db*"))
    for old in files[:-keep] if len(files) > keep else []:
        old.unlink(missing_ok=True)
        logger.info("滚动清理旧备份: %s", old.name)


def list_backups() -> list[dict]:
    """GET /backup/list：文件名/大小/时间 + 恢复语义提示（FR-C8）。"""
    with db.get_conn() as conn:
        backup_dir = _resolve_backup_dir(conn)  # S-5-b：同一收敛入口
    items = []
    if backup_dir.is_dir():
        for f in sorted(backup_dir.glob("app-*.db*"), reverse=True):
            items.append(
                {
                    "name": f.name,
                    "size": f.stat().st_size,
                    "encrypted": f.suffix == ".enc",
                    "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                }
            )
    return items


async def backup_loop() -> None:
    """定时备份任务（main.py lifespan 挂载；失败仅记日志，E12 隔离）。"""
    while True:
        await asyncio.sleep(BACKUP_INTERVAL_S)
        try:
            await asyncio.to_thread(create_backup)
        except Exception:
            logger.exception("定时备份失败（下轮重试）")
