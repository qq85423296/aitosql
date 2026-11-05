"""设置路由（C1.6 / FR-C4）：全键读写 + 组装预览（C4.2 预览同源唯一生成器）。

读写包络冻结（修复回路 P1-3，回写 contracts.md C1.6）：
- GET /settings 响应 `{settings: {key: {value, desc}}, local_only, verify_llm_greyed}`；
- PUT /settings 请求 `{values: {key: value}}`（未知键/类型不符 422）；
- 前端经 api/settings.ts 唯一适配点解包/打包，组件不感知本包络。

- PUT 按默认值类型校验（未知键 422）；verify_llm 在 local_only 下置灰拒绝开启（D07）；
- route.* 值格式 `{provider_id}:{model}`（数字 id 引用），写入期 fail-fast 422（P0-3）；
- prompt-preview：与真实注入同走 llm/prompts.assemble_envelope（D-09）；
  `datasource_id` 为必填 query 参数（C4.2 冻结：调用方取当前/第一可用数据源，P1-7）；
- GET /settings 回显 server.token 仅布尔（已生成与否），明文永不回显。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import TokenDep, ok, run
from app.api.schemas.settings import SettingsPutIn
from app.errors import E_BAD_REQUEST, BizError
from app.store import db
from app.store import providers as store_providers
from app.store import settings as store_settings

router = APIRouter(tags=["settings"], dependencies=[TokenDep])


@router.get("/settings")
async def get_settings() -> dict:
    return ok(await run(_read_all))


def _read_all() -> dict:
    with db.get_conn() as conn:
        data = store_settings.query_all_settings(conn)
        routes = {
            k: str(store_settings.get_setting(conn, k) or "")
            for k in ("route.generate", "route.fix", "route.ai")
        }
        local_ids = {p.id for p in store_providers.query_providers(conn) if p.protocol == "ollama"}
        local_only = store_settings.is_local_only(routes, local_ids)
        return {
            "settings": data,
            "local_only": local_only,
            "verify_llm_greyed": local_only,
        }  # D07：local_only 下 LLM 校验层置灰


_ROUTE_KEYS = ("route.generate", "route.fix", "route.ai")

# 系统内部只读键：GET 回显为布尔/占位，前端 PUT 会原样回带，落库即破坏鉴权
# （实测事故：server.token 被布尔值覆盖 → 全站 401）。写入一律忽略。
_READONLY_KEYS = {"server.token"}


def _check_route_ref(key: str, value: object) -> None:
    """route.* 值格式冻结（P0-3）：`{provider_id}:{model}`，数字 id 引用。

    写入期 422 拦截（fail-fast）：非法引用若落库，查询主链路每次必失败且
    错误语义漂移（历史上曾误报 E2001「模型端点无响应」）。
    """
    text = str(value or "")
    if not text:
        return  # 空串 = 未配置（local_only 占位），放行
    pid_s, sep, model = text.partition(":")
    if not sep or not pid_s.isdigit() or not model.strip():
        raise BizError(
            E_BAD_REQUEST,
            f"{key} 值形态非法: {text!r}（期望 {{provider_id}}:{{model}}，"
            "provider 段为数字 id，如 '1:qwen3-32b'）",
            422,
        )


@router.put("/settings")
async def put_settings(payload: SettingsPutIn) -> dict:
    def _w() -> None:
        with db.get_conn() as conn:
            for key in _ROUTE_KEYS:
                if key in payload.values:
                    _check_route_ref(key, payload.values[key])
            if payload.values.get("feature.verify_llm"):
                routes = {k: str(store_settings.get_setting(conn, k) or "") for k in _ROUTE_KEYS}
                local_ids = {p.id for p in store_providers.query_providers(conn) if p.protocol == "ollama"}
                if store_settings.is_local_only(routes, local_ids):
                    raise BizError(
                        E_BAD_REQUEST,
                        "verify_llm 在 local_only（三路全本地零出境）下置灰禁用；"
                        "开启需配置云路由并明示出境清单（E6 红标）",
                        422,
                    )
            for key, value in payload.values.items():
                if key in _READONLY_KEYS:
                    continue
                store_settings.save_setting(conn, key, value)

    await run(_w)
    return ok(None, "已保存")


@router.get("/settings/prompt-preview")
async def prompt_preview(datasource_id: int, question: str = "示例问题") -> dict:
    """组装预览（C4.2）：`{segments: [{name, text, tokens}...], total_tokens, budget}`。"""

    def _p() -> dict:
        from app.llm import prompts as llm_prompts
        from app.retrieval import supply as retrieval_supply
        from app.sqlcheck import dialects as sql_dialects
        from app.store import datasources as store_datasources

        with db.get_conn() as conn:
            ds = store_datasources.get_datasource(conn, datasource_id)
            budget = int(
                llm_prompts.DEFAULT_CTX * int(store_settings.get_setting(conn, "schema.budget_pct")) / 100
            )
            supply_result = retrieval_supply.supply(
                conn,
                question=question,
                datasource_id=datasource_id,
                budget_tokens=budget,
                mode_setting=str(store_settings.get_setting(conn, "schema.mode")),
            )
            return llm_prompts.assemble_envelope(
                conn,
                dialect=ds.dialect,
                datasource_id=datasource_id,
                question=question,
                schema_text=supply_result.schema_text,
                dialect_card=sql_dialects.load_card(ds.dialect),
            )

    return ok(await run(_p))
