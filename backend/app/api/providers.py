"""提供商路由（C1.6 / FR-C1/C2）：CRUD + 测试连接 + 模型目录拉取。

密钥纪律：响应永不回显 api_key（store 模型 has_api_key 替代）；
空模型 ID 校验文案逐字：「模型 N: 模型 ID 不能为空」（FR-C1）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.providers import ModelsProbeIn, ProviderCreateIn, ProviderPatchIn
from app.llm import registry as llm_registry
from app.store import db
from app.store import providers as store_providers

router = APIRouter(tags=["providers"], dependencies=[TokenDep])


@router.get("/providers")
async def list_providers() -> dict:
    def _q() -> list[dict]:
        with db.get_conn() as conn:
            return [p.model_dump() for p in store_providers.query_providers(conn)]

    return ok(await run(_q))


@router.post("/providers", status_code=201)
async def create_provider(payload: ProviderCreateIn) -> dict:
    def _c() -> dict:
        with db.get_conn() as conn:
            return llm_registry.save_provider(
                conn,
                name=payload.name,
                type_=payload.type,
                protocol=payload.protocol,
                base_url=payload.base_url,
                api_key=payload.api_key,
                models=payload.models,
            ).model_dump()

    return ok(await run(_c), "已创建")


@router.patch("/providers/{provider_id}")
async def patch_provider(provider_id: int, payload: ProviderPatchIn) -> dict:
    def _u() -> dict:
        with db.get_conn() as conn:
            return llm_registry.update_provider(
                conn,
                provider_id,
                name=payload.name,
                protocol=payload.protocol,
                base_url=payload.base_url,
                api_key=payload.api_key,
                models=payload.models,
                enabled=None if payload.enabled is None else int(payload.enabled),
            ).model_dump()

    return ok(await run(_u))


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: int) -> dict:
    def _d() -> None:
        with db.get_conn() as conn:
            store_providers.delete_provider(conn, provider_id)

    await run(_d)
    return ok(None, "已删除")


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: int) -> dict:
    """测试连接：`{latency_ms, models}`；失败红卡注明「密钥未外发」。

    F-3：profile 装配（同步 sqlite）经 run() 桥接后即释放连接；网络段裸 await，
    不持有连接跨 LLM 往返（单 worker 防阻塞）。
    """

    def _load() -> llm_registry.ResolvedProfile:
        with db.get_conn() as conn:
            return llm_registry.load_profile(conn, provider_id)

    profile = await run(_load)
    return ok(await llm_registry.test_profile(profile))


@router.get("/providers/{provider_id}/models")
async def list_provider_models(provider_id: int) -> dict:
    def _load() -> llm_registry.ResolvedProfile:
        with db.get_conn() as conn:
            return llm_registry.load_profile(conn, provider_id)

    profile = await run(_load)
    return ok({"models": await llm_registry.list_profile_models(profile)})


@router.post("/providers/models-probe")
async def probe_provider_models(payload: ModelsProbeIn) -> dict:
    """免保存拉模型目录：草稿态 base_url/api_key 直连，密钥不落库（仅本次内存）。"""
    profile = llm_registry.ResolvedProfile(
        id=0,
        name="models-probe",
        protocol=payload.protocol,
        base_url=payload.base_url,
        api_key=payload.api_key,
    )
    return ok({"models": await llm_registry.list_profile_models(profile)})
