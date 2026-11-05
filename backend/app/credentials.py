"""密钥链（design D-10 / SSOT §12.4，评审项 37）。

获取顺序：keyring → `AITOSQL_FERNET_KEY` 环境变量（仅内存驻留）→ 首启生成。
keyring 不可用且环境变量缺失 → **fail-closed 拒启**。
密文只进 `provider.api_key_enc` / `datasource.password_enc` BLOB；
轮换走 E4001 重新录入（不迁移）；备份把两处密文写 NULL（双封）。
"""

from __future__ import annotations

import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from app import config
from app.errors import E_DECRYPT, BizError

logger = logging.getLogger("aitosql.llm.secrets")

SERVICE_NAME = "aitosql"
KEY_NAME = "fernet_key"

_fernet: Fernet | None = None
_keyring_unavailable = False


def _keyring_get() -> str | None:
    """keyring 读取；后端不可用记一次性降级标记（五款后端由 V-4 实测校准）。"""
    global _keyring_unavailable
    try:
        import keyring

        return keyring.get_password(SERVICE_NAME, KEY_NAME)
    except Exception as exc:
        _keyring_unavailable = True
        logger.warning("keyring 不可用: %s", type(exc).__name__)
        return None


def _keyring_set(value: str) -> bool:
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, KEY_NAME, value)
        return True
    except Exception:
        return False


def ensure_key_ready() -> str:
    """启动自检：返回 Fernet 密钥串；三序皆不可得 → fail-closed 拒启。"""
    key = _keyring_get()
    if key:
        return key
    env_key = os.environ.get(config.ENV_FERNET_KEY, "").strip()
    if env_key:
        # 环境变量仅内存驻留，不落任何文件（§12.4-3）
        return env_key
    if _keyring_unavailable:
        raise BizError(
            E_DECRYPT,
            "keyring 不可用且未提供 AITOSQL_FERNET_KEY，fail-closed 拒启",
            500,
        )
    generated = Fernet.generate_key().decode()
    if not _keyring_set(generated):
        raise BizError(
            E_DECRYPT,
            "keyring 写入失败且未提供 AITOSQL_FERNET_KEY，fail-closed 拒启",
            500,
        )
    logger.info("首启生成 Fernet 密钥并写入 keyring")
    return generated


def get_fernet() -> Fernet:
    """进程内缓存的 Fernet 实例。"""
    global _fernet
    if _fernet is None:
        key = ensure_key_ready()
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_text(plain: str | None) -> bytes | None:
    """加密为 BLOB；None 直通（离线库 password 为 NULL）。"""
    if plain is None:
        return None
    return get_fernet().encrypt(plain.encode("utf-8"))


def decrypt_text(blob: bytes | memoryview | None) -> str | None:
    """解密；失败抛 E4001（提示重新录入，不做密文迁移）。"""
    if blob is None:
        return None
    try:
        return get_fernet().decrypt(bytes(blob)).decode("utf-8")
    except InvalidToken as exc:
        raise BizError(E_DECRYPT, "密钥解密失败，请至设置页重新录入（不迁移）", 400) from exc
    except Exception as exc:
        raise BizError(E_DECRYPT, "密钥不可用，请检查 keyring / AITOSQL_FERNET_KEY", 500) from exc


def new_key_b64() -> str:
    """生成新密钥串（测试与首启兜底用）。"""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()
