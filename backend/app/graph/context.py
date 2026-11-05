"""NodeContext：跨模块依赖注入载体（design 段 5 / D-2，形状编码期定型 R-6）。

三件套：事件发射器（先落库再推送，D-06）、路由对话（route.generate/fix/ai 统一入口）、
状态持久化回调（app.db 单事务先提交，D-01）。引擎/检索等能力经节点内 to_thread
调 L3 模块；store 连接经 db_path 工厂随用随关（同步 IO 隔离 D-02）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NodeContext:
    query_id: str
    # 事件发射：async (type, payload, persist=True) -> seq；result 批量行 persist=False（D-06）
    emit: Callable[[str, dict[str, Any], bool], Awaitable[int]]
    # LLM 路由对话：async (route_ref, messages, max_tokens) -> ChatOutcome
    chat_fn: Callable[..., Awaitable[Any]]
    # 终态/进度持久化：async (**fields) -> None（写 app.db query 行，单事务）
    persist_fn: Callable[..., Awaitable[None]]
    # 设置快照（本次查询起跑时读取，运行期不漂移）
    settings: dict[str, Any] = field(default_factory=dict)
    # 数据源快照（不含明文密码）与解密密码（仅内存，严禁落库/出境）
    datasource: dict[str, Any] = field(default_factory=dict)
    password: str | None = None
    # store 连接工厂参数（测试指向临时库）
    db_path: Path | None = None
