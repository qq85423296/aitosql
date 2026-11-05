"""提供商注册表（design D-34）：CRUD 编排、模型目录拉取、测试连接、路由解析。

- 校验文案逐字契约：空模型 ID → 「模型 N: 模型 ID 不能为空」（FR-C1 / SSOT §13）；
- 测试连接：`200 {latency_ms, models}` / `4xx {code}`；失败响应附「密钥未外发」注记；
- 路由引用形态 `{provider_id}:{model}`（数字 id 引用，C7；P0-3 冻结）——非法形态
  抛 BizError(E4420)，不进 E2001 语义。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from app.errors import E_BAD_REQUEST, E_MODEL_DOWN, BizError
from app.llm import providers as llm_providers
from app.llm import secrets as llm_secrets
from app.store import providers as store_providers
from app.store.models import Provider


@dataclass
class ResolvedProfile:
    """路由解析产物（llm.providers.ProviderProfile 的实现）。"""

    id: int
    name: str
    protocol: str
    base_url: str | None
    api_key: str | None


def check_models(models: list[str]) -> None:
    """模型目录校验：空 ID 文案逐字一致（契约 FR-C1）。"""
    for i, m in enumerate(models, start=1):
        if not (m or "").strip():
            raise BizError(E_BAD_REQUEST, f"模型 {i}: 模型 ID 不能为空", 422)


def save_provider(
    conn: sqlite3.Connection,
    *,
    name: str,
    type_: str,
    protocol: str,
    base_url: str | None,
    api_key: str | None,
    models: list[str],
) -> Provider:
    """新建提供商：明文密钥在此加密入库（永不回显）。"""
    check_models(models)
    enc = llm_secrets.encrypt_text(api_key) if api_key else None
    pid = store_providers.save_provider(
        conn,
        name=name,
        type_=type_,
        protocol=protocol,
        base_url=base_url,
        api_key_enc=enc,
        models=models,
    )
    return store_providers.get_provider(conn, pid)


def update_provider(
    conn: sqlite3.Connection,
    provider_id: int,
    *,
    name: str | None = None,
    protocol: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    models: list[str] | None = None,
    enabled: int | None = None,
) -> Provider:
    if models is not None:
        check_models(models)
    enc = llm_secrets.encrypt_text(api_key) if api_key else ...
    store_providers.update_provider(
        conn,
        provider_id,
        name=name,
        protocol=protocol,
        base_url=base_url,
        api_key_enc=enc,
        models=models,
        enabled=enabled,
    )
    return store_providers.get_provider(conn, provider_id)


def resolve_route(conn: sqlite3.Connection, route_ref: str) -> tuple[ResolvedProfile, str]:
    """`{provider_id}:{model}` →（解密后的调用快照, 模型 ID）。

    值格式冻结（修复回路 P0-3，回写 contracts.md C7）：provider 段为**数字 id**
    （id 稳定不受重命名影响；前端 RouteSection 下拉 value 同格式）。
    非法形态抛 BizError(E4420)——配置类错误不得冒泡为裸 ValueError 被 runner
    误映射为 E2001「模型端点无响应」。
    密钥来源：provider.api_key_enc 解密 → `{PROVIDER}_API_KEY` 环境变量回退（§18.2）。
    """
    if not route_ref or ":" not in route_ref:
        raise BizError(
            E_BAD_REQUEST,
            f"路由未配置或形态非法: {route_ref!r}（期望 {{provider_id}}:{{model}}，数字 id 引用）",
            400,
        )
    pid_s, model = route_ref.split(":", 1)
    if not pid_s.isdigit() or not model.strip():
        raise BizError(
            E_BAD_REQUEST, f"路由引用形态非法: {route_ref!r}（provider 段须为数字 id，model 非空）", 400
        )
    row = store_providers.get_provider_row(conn, int(pid_s))
    if not row["enabled"]:
        raise BizError(E_MODEL_DOWN, f"提供商已停用: {row['name']}", 400)
    api_key = llm_secrets.decrypt_text(row["api_key_enc"]) if row["api_key_enc"] else None
    if not api_key:
        api_key = llm_providers.env_api_key_fallback(row["name"])
    profile = ResolvedProfile(
        id=row["id"],
        name=row["name"],
        protocol=row["protocol"],
        base_url=row["base_url"],
        api_key=api_key,
    )
    return profile, model


def load_profile(conn: sqlite3.Connection, provider_id: int) -> ResolvedProfile:
    """同步装配调用快照（含密钥解密 / env 回退）。

    F-3 修复：供 api 层经 `run()` 桥接调用——sqlite 连接在本函数返回后即释放，
    不得持有连接跨 `await` 网络调用（单 worker 下阻塞全部请求）。
    """
    row = store_providers.get_provider_row(conn, provider_id)
    api_key = llm_secrets.decrypt_text(row["api_key_enc"]) if row["api_key_enc"] else None
    if not api_key:
        api_key = llm_providers.env_api_key_fallback(row["name"])
    return ResolvedProfile(
        id=row["id"],
        name=row["name"],
        protocol=row["protocol"],
        base_url=row["base_url"],
        api_key=api_key,
    )


async def test_profile(profile: ResolvedProfile) -> dict:
    """测试连接（纯网络段）：成功 `{latency_ms, models}`；失败红卡注明「密钥未外发」。"""
    adapter = llm_providers.get_adapter(profile)
    started = time.monotonic()
    try:
        models = await adapter.list_models()
    except BizError:
        raise
    except Exception as exc:
        raise BizError(E_MODEL_DOWN, "模型端点无响应（密钥未外发）", 502) from exc
    return {"latency_ms": int((time.monotonic() - started) * 1000), "models": models}


async def list_profile_models(profile: ResolvedProfile) -> list[str]:
    """模型目录拉取（纯网络段，FR-C1 拉取按钮）。"""
    return await llm_providers.get_adapter(profile).list_models()


# 注：原 `chat()` 便捷封装已随 F-3 移除——调用方改为「resolve_route/load_profile
# （run() 桥接释放连接）→ get_adapter(profile).chat（裸 await）」两段式，
# 杜绝持有 sqlite 连接跨网络往返。图节点聊天入口 = graph/runner._make_chat。
