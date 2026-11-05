"""setting 表 DAO + C7 全键默认值登记表（contracts C7，SSOT §4，40+ 键）。

- 首启 ensure_defaults 写入默认值；`server.token` 由 dev.py/main.py 首启生成后写入；
- 读写带类型校验（按默认值类型推断）；未知键拒绝写入（防止契约外漂移）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app import config
from app.errors import E_BAD_REQUEST, BizError

# key -> (默认值, 说明)；默认值 None 表示「首启生成/无出厂值」
SETTING_DEFAULTS: dict[str, tuple[Any, str]] = {
    # 三路路由（provider_id:model 引用；全本地 = local_only）
    "route.generate": ("", "生成路由 provider:model"),
    "route.fix": ("", "修正路由 provider:model"),
    "route.ai": ("", "AI 补全路由 provider:model"),
    # 查询与执行
    "query.auto_limit": (1000, "纯 SELECT 无 LIMIT 追加（AST 注入，validate 内）"),
    "query.timeout_s": (30, "语句超时"),
    "query.write_confirm": (True, "写操作确认门"),
    "query.ch_readonly": (True, "CH 连接 readonly=1（强制；MySQL 只读为建议级 D05）"),
    "query.max_fix_rounds": (3, "修正轮数硬上限"),
    "query.pool_size": (2, "工作连接数/数据源；管理连接独立随用随建随关"),
    "query.max_result_rows": (10000, "结果行数硬上限；超限显式失败，不静默截断"),
    "query.max_result_mb": (16, "结果序列化字节上限 MiB；导出同受此预算约束"),
    "query.explain_timeout_s": (5, "EXPLAIN 干跑超时"),
    "query.explain_scan_warn_rows": (5000000, "成本黄警阈值"),
    "query.ch_client_timeout_s": (35, "CH 客户端超时（超时矩阵）"),
    # schema 供给
    "schema.mode": ("auto", "auto/full/retrieval（滞后带 55-65%）"),
    "schema.budget_pct": (60, "schema 注入占上下文比例"),
    "schema.vector": ("auto", "向量检索开关（vec 重建期临时 off）"),
    "schema.enum_top_n": (8, "维度枚举注入上限"),
    "schema.wide_table_columns": (200, "宽表阈值：字段数超过则按问题相关性裁剪注入（0=关闭）"),
    "schema.wide_table_keep": (60, "宽表注入字段上限（必保留主键/分区键/关联键/标准绑定列）"),
    "schema.embed_model": ("BAAI/bge-small-zh-v1.5", "D01 定版"),
    "schema.embed_dim": (512, "与 embed_model 联动登记"),
    # 提示词
    "prompt.system": (config.DEFAULT_SYSTEM_PROMPT, "系统提示词（可覆盖，SSOT §8.1 原文）"),
    "prompt.segments": ({}, "信封段开关（段名→true/false，缺省全开；未知段名/关闭主干段拒绝）"),
    # 近期轮次（DSH deriveMessages 语义：最近 K 轮逐字注入信封）
    "history.rounds": (6, "信封「近期轮次」段注入的最近轮数（0=关闭该段）"),
    "history.max_question_chars": (200, "近期轮次每轮问题截断字符数"),
    "history.max_sql_chars": (1200, "近期轮次每轮 SQL 截断字符数"),
    "datasource.instruction_max_chars": (4000, "每数据源口径说明注入字符预算（P5，超限截断）"),
    # 行为开关（砍项顺序：先 verify_llm 后 followups）
    "feature.clarify_gate": (True, "高风险歧义澄清门"),
    "feature.verify_rules": (True, "结果校验规则层"),
    "feature.verify_llm": (False, "LLM 层；local_only 置灰禁用（D07）"),
    "feature.followups": (True, "追问建议"),
    "feature.partition_hint": (True, "分区提示注入"),
    "feature.meta_intent": (True, "库概况问题 AI 意图直通（免 SQL，AI 识别+整理）"),
    "feature.sql_comments": (True, "SQL 详细备注（表/字段中文名注释块）"),
    # AI 补全（方案 v1.1 §3.1/§6）
    "ai.completion_workers": (3, "AI 补全并发泳道数（1–6）"),
    "ai.completion_max_tokens": (
        8192,
        "AI 补全单次调用 max_tokens（思考模型需含推理预算；finish=length 自动翻倍重试）",
    ),
    "ai.sample_values_allowed": (False, "允许 AI 补全使用采样值（出境风险门控）"),
    # 样例
    "sample.fewshot_n": (3, "few-shot 条数"),
    "sample.cache": (True, "重复问题缓存（semantic_rev 判等）"),
    "sample.auto_stale": (True, "schema 变更自动置过期"),
    # 记忆
    "memory.enabled": (True, "记忆模块总开关"),
    "memory.max_tokens": (4000, "总账容量上限"),
    "memory.consolidate_min_gap_min": (30, "水位线合并最小间隔（分钟）"),
    "memory.inject": (True, "总账注入信封第 3 段"),
    # 模型调用重试（借鉴 dsh-llm-retry：可重试失败类退避重跑，先落事件后等待）
    "llm.retry.mode": ("normal", "模型调用重试模式：normal=有界重试/off=关闭"),
    "llm.retry.max_retries": (5, "可重试失败（限流/5xx/超时/传输/空响应）最大重试次数"),
    "llm.retry.initial_delay_ms": (500, "重试退避起始毫秒（指数翻倍至上限）"),
    "llm.retry.max_delay_ms": (10000, "重试退避上限毫秒"),
    "llm.retry.jitter_pct": (10, "退避抖动比例（%）"),
    # 生成输出预算与超时（GLM-5.3 级思考模型实测：复杂 SQL 问题推理 7-12 万字符，
    # 16384 必被思考吃满致正文为空；32768 起步 + EMPTY_RESPONSE 自动翻倍至 65536 兜底）
    "llm.generate_max_tokens": (
        32768,
        "生成/修正单次调用 max_tokens（思考模型需含推理预算；EMPTY_RESPONSE 自动翻倍至 131072 封顶）",
    ),
    "llm.timeout_s": (900, "模型调用整体超时秒（65536 预算的思考生成实测可达 400s+）"),
    # 会话压缩
    "session.compact_msgs": (40, "会话消息数超此值触发压缩"),
    "session.keep_msgs": (6, "压缩保留最近 N 轮完整"),
    "session.compact_pressure_ratio": (80, "token 压力阈值（占模型上下文 %，与条数阈值任一触达即压缩）"),
    # 备份
    "backup.dir": ("data/backups/", "滚动备份目录"),
    "backup.keep": (10, "滚动保留份数"),
    "backup.encrypt": (False, "备份可选加密（D08；不覆盖 exports/）"),
    # 导出（瞬态目录 D14）
    "export.dir": ("data/exports/", "CSV 瞬态目录"),
    "export.ttl_h": (24, "导出 TTL，定时清理"),
    "export.max_total_mb": (2048, "超量 LRU"),
    # 服务
    "server.token": (None, "本机 Bearer token（首启生成，仅 CLI 打印一次）"),
    "server.auth": (True, "API Bearer 认证总开关；关闭后本机免 token（仅建议单机自用）"),
    "server.port": (8321, "监听 127.0.0.1（CORS/Origin 同限定）"),
    # SSE
    "sse.heartbeat_s": (15, "心跳注释帧间隔"),
    "sse.event_payload_kb": (16, "单事件 payload 上限，超出截断标注"),
}

# 数值型设置的最小值（写入侧 422；backup.keep<1 的滚动语义会反向删除最新备份）
_SETTING_MIN: dict[str, int] = {
    "backup.keep": 1,
}


def ensure_defaults(conn: sqlite3.Connection) -> None:
    """写入缺失的默认键（不覆盖已有值）。"""
    for key, (default, _desc) in SETTING_DEFAULTS.items():
        if default is None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO setting(key, value_json) VALUES (?, ?)",
            (key, json.dumps(default, ensure_ascii=False)),
        )


def check_key(key: str) -> None:
    if key not in SETTING_DEFAULTS:
        raise BizError(E_BAD_REQUEST, f"未知配置键: {key}", 422)


def get_setting(conn: sqlite3.Connection, key: str) -> Any:
    """读取配置（未落库时回退默认值）。"""
    check_key(key)
    row = conn.execute("SELECT value_json FROM setting WHERE key = ?", (key,)).fetchone()
    if row is None:
        return SETTING_DEFAULTS[key][0]
    return json.loads(row[0])


def get_settings_map(conn: sqlite3.Connection, keys: list[str] | None = None) -> dict[str, Any]:
    """批量读取（防 N+1）：keys 缺省为全表。"""
    wanted = keys or list(SETTING_DEFAULTS)
    placeholders = ",".join("?" * len(wanted))
    rows = conn.execute(
        f"SELECT key, value_json FROM setting WHERE key IN ({placeholders})",
        wanted,
    ).fetchall()
    found = {r[0]: json.loads(r[1]) for r in rows}
    return {k: found.get(k, SETTING_DEFAULTS[k][0]) for k in wanted}


def save_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """写入配置，按默认值类型校验 + 数值最小值校验。"""
    check_key(key)
    default = SETTING_DEFAULTS[key][0]
    if default is not None and not isinstance(value, type(default)):
        raise BizError(
            E_BAD_REQUEST,
            f"配置 {key} 类型应为 {type(default).__name__}",
            422,
        )
    min_v = _SETTING_MIN.get(key)
    if min_v is not None and isinstance(value, int) and value < min_v:
        raise BizError(E_BAD_REQUEST, f"配置 {key} 最小值为 {min_v}", 422)
    conn.execute(
        "INSERT INTO setting(key, value_json) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def query_all_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """设置页 GET：全键值 + 说明（server.token 不回显明文，仅回显是否已生成）。"""
    data = get_settings_map(conn)
    data["server.token"] = bool(data.get("server.token"))
    return {k: {"value": v, "desc": SETTING_DEFAULTS[k][1]} for k, v in data.items()}


def is_local_only(routes: dict[str, str], local_provider_ids: set[int]) -> bool:
    """三路全本地 = local_only（零出境，C11）。routes 形如 {'route.generate': '1:qwen'}。"""
    for key in ("route.generate", "route.fix", "route.ai"):
        ref = routes.get(key) or ""
        if not ref:
            continue  # 未配置视为本地占位
        try:
            pid = int(ref.split(":", 1)[0])
        except ValueError:
            return False
        if pid not in local_provider_ids:
            return False
    return True
