"""QueryState TypedDict（contracts C5 / SSOT §5.1 原文）。

尾部「内部协调键」为构建期补充（节点间判定标记，运行时写入、不进契约面，
定型后回写 contracts.md C5 附注）。
"""

from __future__ import annotations

from typing import TypedDict


class RiskItem(TypedDict):
    rule_id: str  # R01-R15
    level: str  # 'red' | 'yellow'
    message: str  # 用户可读（分方言文案）


class CorrectionRound(TypedDict):
    round: int
    sql: str
    error: str  # 已脱敏（§12.5）
    fingerprint: str  # sqlglot 归一化指纹


class QueryState(TypedDict, total=False):
    query_id: str
    thread_id: str  # = client_request_id（幂等键）
    session_id: str
    datasource_id: int
    dialect: str  # mysql|doris|clickhouse
    ds_mode: str  # online|offline
    question: str
    # M2 供给
    schema_mode: str  # full|retrieval（滞后带 55-65%）
    schema_text: str
    hit_tables: list[str]
    resupply_used: bool  # need_tables 二次供给仅 1 次
    # M3 生成
    contract: dict
    sql: str  # 与 confirm_gate 展示、execute 逐字同串（所验即所执）
    assumptions: list[dict]  # [{text, impact:'high'|'low', kind}]
    clarify_options: list[dict]
    clarify_choice: str
    # M5 校验
    risks: list[RiskItem]
    explain_rows: int
    # 修正回路
    round: int
    error_history: list[CorrectionRound]
    fingerprints: list[str]
    # 门状态
    write_confirmed: bool
    # M6 执行
    exec_meta: dict  # {rows, scan_rows, duration_ms, data_max_time, connection_id}
    result_preview_id: str
    # verify（回退全局共享 1 次，D09）
    verify_checks: list[dict]
    verify_passed: bool
    verify_fallback_used: bool  # 全局唯一回退额度凭证
    result_unverified: bool
    # 终态
    status: str  # running|waiting_clarify|waiting_confirm|done|failed|stopped
    tokens: int

    # ---- 内部协调键（构建期补充，不进契约面） ----
    hard_blocked: bool  # R15 命中（validate 置位，edges 据此 END）
    needs_correct: bool  # 可修正错误标记（validate/execute/verify 置位）
    stop_requested: bool  # stop 标记（runner CAS 置位）
    cache_hit: bool  # 样例缓存命中（D-15 三条件）
    degraded_contract: bool  # 契约降级标记（E2002「未结构化输出」）
    explain_skipped: bool  # E3001 干跑跳过（标注「未经验证」）
    gate_decision: str  # confirm_gate 裁决回传（approved/cancelled）
    meta_answer: bool  # 元数据问题直通（generate 置位，edges 据此 END 免 SQL）
