"""重试执行器用例（P3，对标 dsh-llm-retry 语义）。

退避与事件顺序用假睡眠驱动：monkeypatch anyio.sleep，断言「先 on_retry 后等待」。
"""

from __future__ import annotations

import pytest

from app.errors import E_MODEL_DOWN, BizError
from app.llm import retry as llm_retry


def _err(failure_class: str, retry_after_ms: int | None = None) -> BizError:
    return BizError(
        E_MODEL_DOWN,
        "模型端点无响应",
        502,
        data={"failure_class": failure_class, "retry_after_ms": retry_after_ms},
    )


def test_policy_delay_backoff_and_retry_after() -> None:
    p = llm_retry.RetryPolicy(max_retries=5, initial_delay_ms=500, max_delay_ms=10_000, jitter_pct=0)
    assert p.delay_ms(0, None) == 500
    assert p.delay_ms(1, None) == 1000
    assert p.delay_ms(2, None) == 2000
    assert p.delay_ms(20, None) == 10_000  # 上限
    # Retry-After 限界内替换本地退避；超限回落本地
    assert p.delay_ms(0, 3000) == 3000
    assert p.delay_ms(0, 120_000) == 500


def test_policy_from_settings() -> None:
    assert llm_retry.policy_from_settings({"llm.retry.mode": "off"}) is None
    p = llm_retry.policy_from_settings({"llm.retry.mode": "normal", "llm.retry.max_retries": 2})
    assert p is not None and p.max_retries == 2
    assert llm_retry.policy_from_settings({}) is not None  # 缺省 normal


@pytest.mark.asyncio
async def test_retry_success_after_transient(monkeypatch) -> None:
    sleeps: list[float] = []
    events: list[tuple[int, int, str]] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(llm_retry.anyio, "sleep", fake_sleep)

    calls = {"n": 0}

    async def once() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _err("SERVER")
        return "ok"

    async def on_retry(attempt: int, delay_ms: int, failure_class: str) -> None:
        events.append((attempt, delay_ms, failure_class))
        # 先落事件后等待：第 attempt 次 on_retry 发生时，最多只睡过 attempt-1 次
        assert len(sleeps) == attempt - 1

    policy = llm_retry.RetryPolicy(max_retries=5, initial_delay_ms=500, jitter_pct=0)
    out = await llm_retry.chat_with_retry(once, policy=policy, on_retry=on_retry)
    assert out == "ok" and calls["n"] == 3
    assert [e[0] for e in events] == [1, 2]
    assert [e[2] for e in events] == ["SERVER", "SERVER"]
    assert sleeps == [0.5, 1.0]


@pytest.mark.asyncio
async def test_retry_exhaustion_raises(monkeypatch) -> None:
    async def fake_sleep(s: float) -> None:
        return None

    monkeypatch.setattr(llm_retry.anyio, "sleep", fake_sleep)
    calls = {"n": 0}

    async def once() -> str:
        calls["n"] += 1
        raise _err("TIMEOUT")

    policy = llm_retry.RetryPolicy(max_retries=2, initial_delay_ms=100, jitter_pct=0)
    with pytest.raises(BizError):
        await llm_retry.chat_with_retry(once, policy=policy)
    assert calls["n"] == 3  # 首次 + 2 次重试


@pytest.mark.asyncio
async def test_non_retryable_raises_immediately(monkeypatch) -> None:
    async def fake_sleep(s: float) -> None:
        raise AssertionError("不可重试错误不得等待")

    monkeypatch.setattr(llm_retry.anyio, "sleep", fake_sleep)
    calls = {"n": 0}

    async def once() -> str:
        calls["n"] += 1
        raise _err("CLIENT")

    with pytest.raises(BizError):
        await llm_retry.chat_with_retry(once, policy=llm_retry.RetryPolicy())
    assert calls["n"] == 1
    # 非 E2001 不透传重试
    async def once2() -> str:
        raise BizError("E4420", "参数错误", 422)

    with pytest.raises(BizError):
        await llm_retry.chat_with_retry(once2, policy=llm_retry.RetryPolicy())


@pytest.mark.asyncio
async def test_retry_off_single_attempt() -> None:
    calls = {"n": 0}

    async def once() -> str:
        calls["n"] += 1
        raise _err("RATE_LIMIT", retry_after_ms=1000)

    with pytest.raises(BizError):
        await llm_retry.chat_with_retry(once, policy=None)
    assert calls["n"] == 1
