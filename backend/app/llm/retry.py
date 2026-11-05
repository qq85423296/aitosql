"""模型调用重试执行器（借鉴 dsh-llm-retry，P3）。

语义对齐：
- 重试策略来自设置（相当于 DSH 的适配器路由配置）；
- 仅对可重试失败类重试：RATE_LIMIT / SERVER / TIMEOUT / TRANSPORT / EMPTY_RESPONSE；
- 退避：initial_delay_ms 起步指数退避至 max_delay_ms，带抖动；
- 提供方 Retry-After 在策略边界内时替换本地退避（上限 60s）；
- **先通知（落事件）后等待**——调用方传入 on_retry 协程负责持久化，崩溃不留隐形重试；
- 取消全程保持最终决定权（anyio.sleep 可取消，CancelledError 不被吞）。
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import anyio

from app.errors import E_MODEL_DOWN, BizError

logger = logging.getLogger("aitosql.llm.retry")

RETRYABLE_CLASSES = frozenset({"RATE_LIMIT", "SERVER", "TIMEOUT", "TRANSPORT", "EMPTY_RESPONSE"})
_RETRY_AFTER_CAP_MS = 60_000

# 重试事件回调：async (attempt, delay_ms, failure_class) -> None
RetryHook = Callable[[int, int, str], Awaitable[None]]


class RetryPolicy:
    """normal 有界重试策略（off 由调用方判断，不构造本对象）。"""

    def __init__(
        self,
        *,
        max_retries: int = 5,
        initial_delay_ms: int = 500,
        max_delay_ms: int = 10_000,
        jitter_pct: int = 10,
    ) -> None:
        self.max_retries = max(0, max_retries)
        self.initial_delay_ms = max(0, initial_delay_ms)
        self.max_delay_ms = max(self.initial_delay_ms, max_delay_ms)
        self.jitter_ratio = max(0, min(jitter_pct, 100)) / 100

    def delay_ms(self, attempt: int, retry_after_ms: int | None) -> int:
        """第 attempt 次重试（从 0 计）的等待时长：Retry-After 优先（限界内），否则指数退避+抖动。"""
        if retry_after_ms is not None and 0 <= retry_after_ms <= _RETRY_AFTER_CAP_MS:
            return retry_after_ms
        base = min(self.initial_delay_ms * (2**attempt), self.max_delay_ms)
        jitter = base * self.jitter_ratio * random.random()
        return int(base + jitter)


def policy_from_settings(settings: dict[str, Any]) -> RetryPolicy | None:
    """从设置快照读取策略；mode != normal 时返回 None（不重试）。"""
    if str(settings.get("llm.retry.mode") or "normal") != "normal":
        return None
    return RetryPolicy(
        max_retries=int(settings.get("llm.retry.max_retries") or 5),
        initial_delay_ms=int(settings.get("llm.retry.initial_delay_ms") or 500),
        max_delay_ms=int(settings.get("llm.retry.max_delay_ms") or 10_000),
        jitter_pct=int(settings.get("llm.retry.jitter_pct") or 10),
    )


async def chat_with_retry(
    once: Callable[[], Awaitable[Any]],
    *,
    policy: RetryPolicy | None,
    on_retry: RetryHook | None = None,
) -> Any:
    """执行一次模型调用，可重试失败按策略退避重跑。

    on_retry 在每次等待前调用（先持久化后等待语义）；无 on_retry 时仅日志记录。
    """
    if policy is None or policy.max_retries <= 0:
        return await once()
    attempt = 0
    while True:
        try:
            return await once()
        except BizError as exc:
            if exc.code != E_MODEL_DOWN:
                raise
            data = exc.data or {}
            failure_class = str(data.get("failure_class") or "UNKNOWN")
            if failure_class not in RETRYABLE_CLASSES or attempt >= policy.max_retries:
                raise
            delay = policy.delay_ms(attempt, data.get("retry_after_ms"))
            logger.info(
                "模型调用失败（%s），第 %d/%d 次重试，等待 %dms",
                failure_class,
                attempt + 1,
                policy.max_retries,
                delay,
            )
            if on_retry is not None:
                await on_retry(attempt + 1, delay, failure_class)
            await anyio.sleep(delay / 1000)
            attempt += 1
