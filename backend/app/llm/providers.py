"""LLM 三适配器（design D-34 / plan S7）：OpenAI 兼容 / Anthropic Messages / Ollama 原生。

- httpx（async）自封装，不引第三方 SDK；超时/连接错误统一转 BizError(E2001)；
- 出境唯一发生地：离开本机的文本只经本模块发出；密钥经 secrets.decrypt_text 内存解密，
  支持 `{PROVIDER}_API_KEY` 环境变量回退（SSOT §18.2，密钥留空时）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from app.errors import E_MODEL_DOWN, BizError

logger = logging.getLogger("aitosql.llm.providers")

# 流式增量回调：kind = reasoning|content，第二参为该 kind 的累计全文（替换式渲染）
DeltaCallback = Callable[[str, str], Awaitable[None]]


class ChatOutcome:
    """一次调用的统一结果：文本 + token 用量 + 时延。"""

    def __init__(self, text: str, tokens: int, latency_ms: int) -> None:
        self.text = text
        self.tokens = tokens
        self.latency_ms = latency_ms


class ProviderProfile(Protocol):
    """发起调用所需的提供商快照（由 registry 从 store 装配，显式参数 D2）。"""

    id: int
    name: str
    protocol: str
    base_url: str | None
    api_key: str | None  # 已解密，仅内存


DEFAULT_BASE_URLS = {
    "openai_chat": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://127.0.0.1:11434",
}


class BaseAdapter:
    """适配器基类：chat / list_models / ping。"""

    protocol = "base"

    def __init__(self, profile: ProviderProfile) -> None:
        self.p = profile
        self.base_url = (profile.base_url or DEFAULT_BASE_URLS.get(self.protocol, "")).rstrip("/")

    def _client(self, timeout_s: float = 60.0) -> httpx.AsyncClient:
        # trust_env=False：出境唯一发生地收口，不经系统代理；
        # 且本机 NO_PROXY 含 IPv6 括号形态（[::1]）会触发 httpx InvalidURL（实测复现）
        return httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0), trust_env=False)

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4096,
        on_delta: DeltaCallback | None = None,
        timeout_s: float = 300.0,
    ) -> ChatOutcome:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        raise NotImplementedError

    def _wrap_error(self, exc: Exception) -> BizError:
        """统一转 E2001（契约文案不变），并附失败分类供重试执行器判定（借鉴 dsh-llm-retry：
        RATE_LIMIT/SERVER/TIMEOUT/TRANSPORT 为可重试类；其余 CLIENT/UNKNOWN 不重试）。"""
        logger.warning("模型端点调用失败: %s %s", self.p.name, type(exc).__name__)
        return BizError(
            E_MODEL_DOWN,
            "模型端点无响应",
            502,
            data={"provider": self.p.name, **classify_failure(exc)},
        )


def classify_failure(exc: Exception) -> dict[str, Any]:
    """异常 → 稳定失败分类（对标 DSH 稳定 code 词汇）。

    顺序敏感：httpx.TimeoutException 是 TransportError 子类，须先判超时。
    """
    import httpx as _httpx

    if isinstance(exc, BizError):
        return {"failure_class": "CLIENT"}
    if isinstance(exc, _httpx.TimeoutException):
        return {"failure_class": "TIMEOUT"}
    if isinstance(exc, _httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            retry_after_ms: int | None = None
            raw = exc.response.headers.get("retry-after")
            if raw:
                try:
                    retry_after_ms = int(float(raw) * 1000)
                except ValueError:
                    retry_after_ms = None
            return {"failure_class": "RATE_LIMIT", "retry_after_ms": retry_after_ms}
        if 500 <= status < 600:
            return {"failure_class": "SERVER"}
        return {"failure_class": "CLIENT"}
    if isinstance(exc, _httpx.TransportError):
        return {"failure_class": "TRANSPORT"}
    return {"failure_class": "UNKNOWN"}


class OpenAIChatAdapter(BaseAdapter):
    protocol = "openai_chat"

    def _headers(self) -> dict[str, str]:
        # 密钥为空时不下发 Authorization（'Bearer ' 尾空格是非法头值，httpx LocalProtocolError）
        return {"Authorization": f"Bearer {self.p.api_key}"} if self.p.api_key else {}

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 16384,
        on_delta: DeltaCallback | None = None,
        timeout_s: float = 300.0,
    ) -> ChatOutcome:
        # max_tokens 默认 16384：思考型模型（glm-5.3 等）reasoning 与正文共享输出预算，
        # 4096 会被思考吃满导致正文为空（实测复现）。
        # timeout_s：65536 预算的思考生成实测可达 400s+，runner 按 llm.timeout_s 设置传入 900。
        # on_delta(kind, accumulated)：流式回调，kind = reasoning|content，
        # 每 ~0.3s 上报一次累计全文（前端替换式渲染），结束时各类别再终刷一次。
        started = time.monotonic()
        parts: list[str] = []
        reasoning_parts: list[str] = []
        tokens = 0
        finish_reason: str | None = None
        last_flush = {"reasoning": 0.0, "content": 0.0}

        async def _flush(kind: str, force: bool = False) -> None:
            if on_delta is None:
                return
            now = time.monotonic()
            if not force and now - last_flush[kind] < 0.3:
                return
            last_flush[kind] = now
            text = "".join(reasoning_parts if kind == "reasoning" else parts)
            await on_delta(kind, text)

        try:
            # 统一 stream=true 自行拼合 SSE chunk（实测有网关只支持流式，
            # stream:false 直接报错；标准 OpenAI 兼容端点流式形态一致）。
            # 注意：思考型模型会先发 delta.reasoning_content，只拼接 delta.content。
            async with (
                self._client(timeout_s=timeout_s) as client,
                client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0,
                        "stream": True,
                    },
                ) as resp,
            ):
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for ch in chunk.get("choices") or []:
                        delta = ch.get("delta") or {}
                        rc = delta.get("reasoning_content")
                        if rc:
                            reasoning_parts.append(rc)
                            await _flush("reasoning")
                        content = delta.get("content")
                        if content:
                            parts.append(content)
                            await _flush("content")
                        if ch.get("finish_reason"):
                            finish_reason = ch["finish_reason"]
                    usage = chunk.get("usage") or {}
                    if usage.get("total_tokens"):
                        tokens = int(usage["total_tokens"])
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        await _flush("reasoning", force=True)
        await _flush("content", force=True)
        if not parts:
            # 空产出直接报真因，不走契约解析兜底（否则误导为 E2002）
            if finish_reason == "length":
                raise BizError(
                    E_MODEL_DOWN,
                    "模型未产出正文：输出预算被思考内容占满（finish=length）；"
                    "已自动上调 max_tokens 重试，若仍出现请换非思考模型",
                    502,
                    # 分类必须带上：chat_with_retry 只对 RETRYABLE_CLASSES 重试，
                    # 缺 data 会被当 UNKNOWN 直接失败（且 runner 依此做预算翻倍恢复）
                    data={"failure_class": "EMPTY_RESPONSE", "finish_reason": "length"},
                )
            raise BizError(
                E_MODEL_DOWN,
                "模型未产出正文内容（finish=" + str(finish_reason) + "）",
                502,
                data={"failure_class": "EMPTY_RESPONSE"},
            )
        return ChatOutcome("".join(parts), tokens, int((time.monotonic() - started) * 1000))

    async def list_models(self) -> list[str]:
        try:
            async with self._client() as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        return sorted(str(m.get("id")) for m in data.get("data", []) if m.get("id"))


class AnthropicAdapter(BaseAdapter):
    protocol = "anthropic"

    def _headers(self) -> dict[str, str]:
        headers = {"anthropic-version": "2023-06-01"}
        if self.p.api_key:  # 空密钥不下发头（非法头值）
            headers["x-api-key"] = self.p.api_key
        return headers

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4096,
        on_delta: DeltaCallback | None = None,
        timeout_s: float = 300.0,
    ) -> ChatOutcome:
        started = time.monotonic()
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] != "system"]
        try:
            async with self._client(timeout_s=timeout_s) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers=self._headers(),
                    json={"model": model, "max_tokens": max_tokens, "system": system, "messages": turns},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage") or {}
        tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
        return ChatOutcome(text, tokens, int((time.monotonic() - started) * 1000))

    async def list_models(self) -> list[str]:
        try:
            async with self._client() as client:
                resp = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        return sorted(str(m.get("id")) for m in data.get("data", []) if m.get("id"))


class OllamaAdapter(BaseAdapter):
    """Ollama 原生（本地路由主力；local_only 零出境的承载者）。"""

    protocol = "ollama"

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4096,
        on_delta: DeltaCallback | None = None,
        timeout_s: float = 300.0,
    ) -> ChatOutcome:
        started = time.monotonic()
        try:
            async with self._client(timeout_s=timeout_s) as client:
                resp = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "options": {"num_predict": max_tokens, "temperature": 0},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        text = (data.get("message") or {}).get("content", "")
        tokens = int(data.get("prompt_eval_count", 0)) + int(data.get("eval_count", 0))
        return ChatOutcome(text, tokens, int((time.monotonic() - started) * 1000))

    async def list_models(self) -> list[str]:
        try:
            async with self._client() as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise self._wrap_error(exc) from exc
        return sorted(str(m.get("name")) for m in data.get("models", []) if m.get("name"))


_ADAPTERS: dict[str, type[BaseAdapter]] = {
    "openai_chat": OpenAIChatAdapter,
    "anthropic": AnthropicAdapter,
    "ollama": OllamaAdapter,
}


def get_adapter(profile: ProviderProfile) -> BaseAdapter:
    cls = _ADAPTERS.get(profile.protocol)
    if cls is None:
        raise BizError(E_MODEL_DOWN, f"未知协议: {profile.protocol}", 400)
    return cls(profile)


def env_api_key_fallback(provider_name: str) -> str | None:
    """`{PROVIDER}_API_KEY` 环境变量回退（SSOT §18.2；名称大写非字母数字转下划线）。"""
    import re

    env_name = re.sub(r"[^A-Za-z0-9]", "_", provider_name).upper() + "_API_KEY"
    return os.environ.get(env_name) or None
