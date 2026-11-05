"""8 节点（SSOT §5.2 逐行实现，design D-31）。

节点统一形态：`async def node(state: QueryState, *, ctx: NodeContext) -> dict`（段 5 签名）；
ctx 经 runner 以 functools.partial 绑定（langgraph 版本演进隔离，R-1 spike 回写点）。
interrupt 副作用后置铁律（§5.4-5）：interrupt 前不得产生任何引擎/写库副作用——
门节点先 emit 事件（query_event 落库）再 interrupt。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

import anyio
from langgraph.types import interrupt
from pydantic import ValidationError

from app.errors import E_FIX_EXHAUSTED, E_HARD_BLOCK
from app.graph.context import NodeContext
from app.graph.state import CorrectionRound, QueryState
from app.llm import intent as llm_intent
from app.llm import prompts as llm_prompts
from app.retrieval import metaqa
from app.retrieval import supply as retrieval_supply
from app.sqlcheck import annotate as sql_annotate
from app.sqlcheck import clean as sql_clean
from app.sqlcheck import dialects as sql_dialects
from app.sqlcheck import explain as sql_explain
from app.sqlcheck import rules as sql_rules
from app.store import datasources as store_datasources
from app.store import db as store_db
from app.store import inbox as store_inbox
from app.store import queries as store_queries
from app.store import samples as store_samples
from app.store import sessions as store_sessions
from app.verify import llm as verify_llm
from app.verify import rules as verify_rules

logger = logging.getLogger("aitosql.graph.nodes")


# ---------- 公共小件 ----------


async def _stage(ctx: NodeContext, stage: str, round_: int, status: str, elapsed_ms: int = 0) -> None:
    await ctx.emit(
        "stage",
        {"type": "stage", "stage": stage, "round": round_, "status": status, "elapsed_ms": elapsed_ms},
        True,
    )


def _s(ctx: NodeContext, key: str) -> Any:
    return ctx.settings.get(key)


def _budget_tokens(ctx: NodeContext) -> int:
    ctx_total = llm_prompts.DEFAULT_CTX
    pct = int(_s(ctx, "schema.budget_pct") or 60)
    return int(ctx_total * pct / 100)


def _retrieval_question(conn: Any, state: QueryState, ctx: NodeContext) -> str:
    """检索用问题：当前句 + 上一轮问题拼接（治修复轮检索焦点漂移，零额外模型调用）。

    信封注入仍用 state["question"] 原文——拼接只影响检索召回，不污染用户问题段。
    """
    question = str(state["question"])
    session_id = str(state.get("session_id") or "")
    if not session_id:
        return question
    prev = store_queries.recent_rounds(conn, session_id, exclude_query_id=ctx.query_id, limit=1)
    if not prev:
        return question
    prev_q = str(prev[-1].get("question") or "").strip()
    if not prev_q or prev_q == question:
        return question
    return f"{prev_q}\n{question}"


async def _drain_inbox(ctx: NodeContext) -> str:
    """领取运行中输入（P6，DSH inbox 语义）：steer 引导 + inject 补充上下文。

    在 generate 节点边界领取（FIFO），领取即持久迁移 claimed；返回并入工作问题的
    追加文本（steer=「用户引导」、inject=「补充上下文」标签区分），无待处理返回空串。
    """

    def _drain() -> list[dict[str, Any]]:
        with store_db.get_conn(ctx.db_path) as conn:
            rows = store_inbox.pending_inbox(conn, ctx.query_id, kinds=("steer", "inject"))
            store_inbox.claim_inbox(conn, [int(r["id"]) for r in rows])
            return rows

    rows = await anyio.to_thread.run_sync(_drain)
    if not rows:
        return ""
    await ctx.emit(
        "inbox_claimed",
        {"type": "inbox_claimed", "items": [{"kind": r["kind"], "content": r["content"]} for r in rows]},
        True,
    )
    parts = []
    for r in rows:
        label = "用户引导" if r["kind"] == "steer" else "补充上下文"
        parts.append(f"{label}：{r['content']}")
    return "\n" + "\n".join(parts)


# ---------- M2 supply_schema ----------


async def supply_schema(state: QueryState, *, ctx: NodeContext) -> dict:
    """双路融合 RRF + 预算装配 + 滞后带 + need_tables 二次供给（仅 1 次）。"""
    started = time.monotonic()
    round_ = int(state.get("round", 0))
    await _stage(ctx, "supply", round_, "start")
    need_tables = None
    resupply_used = bool(state.get("resupply_used"))
    contract = state.get("contract") or {}
    if contract.get("need_tables") and not resupply_used and state.get("ds_mode") == "online":
        need_tables = list(contract["need_tables"])
        resupply_used = True

    def _supply() -> retrieval_supply.SupplyResult:
        with store_db.get_conn(ctx.db_path) as conn:
            return retrieval_supply.supply(
                conn,
                question=_retrieval_question(conn, state, ctx),
                datasource_id=int(state["datasource_id"]),
                budget_tokens=_budget_tokens(ctx),
                mode_setting=str(_s(ctx, "schema.mode") or "auto"),
                last_mode=state.get("schema_mode"),
                need_tables=need_tables,
            )

    result = await anyio.to_thread.run_sync(_supply)
    elapsed = int((time.monotonic() - started) * 1000)
    await _stage(ctx, "supply", round_, "done", elapsed)
    # meta post_supply（C3.2：两次全字段之第一次）
    await ctx.emit(
        "meta",
        {
            "type": "meta",
            "phase": "post_supply",
            "hit_tables": result.hit_tables,
            "route": {
                "generate": str(_s(ctx, "route.generate") or ""),
                "fix": str(_s(ctx, "route.fix") or ""),
            },
            "tokens": int(state.get("tokens", 0)),
            "retrieval": {k: (v[:32] if isinstance(v, list) else v) for k, v in result.trace.items()},
        },
        True,
    )
    return {
        "schema_text": result.schema_text,
        "hit_tables": result.hit_tables,
        "schema_mode": result.schema_mode,
        "resupply_used": resupply_used,
    }


# ---------- M3 generate ----------


async def generate(state: QueryState, *, ctx: NodeContext) -> dict:
    """8 段信封组装 → 生成路由 → 契约校验（失败重试 1 次 → 降级提取）。

    前置：样例缓存三条件命中（D-15）→ 直返 SQL 并标注「复用已验证结果」。
    失败：模型离线 → BizError(E2001)（runner 捕获落 failed；D03 续跑场景见 §13）。
    """
    started = time.monotonic()
    round_ = int(state.get("round", 0))
    await _stage(ctx, "generate", round_, "start")
    ds_id = int(state["datasource_id"])
    dialect = str(state["dialect"])

    _reasoning_buf: list[str] = []  # 推理全文缓冲（DSH 语义：思考全程持久可回放）

    async def _on_delta(kind: str, text: str) -> None:
        # 生成过程实时流（不落库、无 seq，前端替换式渲染）
        # 注意：provider 的 reasoning delta 是「累计快照」（每帧全量，前端 genPreview 同语义），
        # 缓冲只保留最新一帧（替换）——append 会把内容重复拼接膨胀百倍（实测 10KB→322K 字符）
        if kind == "reasoning":
            _reasoning_buf.clear()
            _reasoning_buf.append(text)
        await ctx.emit(
            "gen_delta",
            {"type": "gen_delta", "stage": "generate", "round": round_, "kind": kind, "text": text},
            False,
        )

    async def _persist_reasoning() -> None:
        """推理全文持久化（reasoning_full 事件）——历史轮次回放时可见完整思考，而非仅存摘要。"""
        text = _reasoning_buf[-1].strip() if _reasoning_buf else ""
        if text:
            await ctx.emit(
                "reasoning_full",
                {"type": "reasoning_full", "round": round_, "text": text},
                True,
            )

    # ---- 库概况直通：AI 意图识别 + AI 整理（启发式仅作 AI 不可用时兜底） ----
    if (
        round_ == 0
        and not state.get("clarify_choice")
        and _s(ctx, "feature.meta_intent")
        and not metaqa.has_data_intent(state["question"])
    ):
        route_ai = str(_s(ctx, "route.ai") or "")
        intent: str | None = None
        if route_ai:

            async def _on_delta_classify(kind: str, text: str) -> None:
                # 分类调用的 content 是内部协议 JSON（{"intent":...}），不进预览；
                # 仅思考流外显（用户可见 AI 正在判断）
                if kind == "reasoning":
                    await _on_delta(kind, text)

            intent = await llm_intent.classify(
                ctx.chat_fn, state["question"], route_ref=route_ai, on_delta=_on_delta_classify
            )
            # 不发空帧重置：下一段流式的首帧会整体替换文本，清空反而造成
            # 「页面突然啥也没了」的空白窗（实测反馈 2026-10-17）
        if intent is None:  # AI 不可用/未配置/解析失败 → 启发式兜底
            intent = "meta" if metaqa.is_meta_question(state["question"]) else "data"
        if intent == "meta":

            def _payload() -> dict[str, Any] | None:
                with store_db.get_conn(ctx.db_path) as conn:
                    return metaqa.build_metadata(conn, ds_id)

            payload = await anyio.to_thread.run_sync(_payload)
            if payload is not None:
                answer: str | None = None
                if route_ai:
                    answer = await llm_intent.compose_answer(
                        ctx.chat_fn, state["question"], payload, route_ref=route_ai, on_delta=_on_delta
                    )
                if answer is None:  # AI 整理失败 → 确定性渲染兜底

                    def _render() -> str | None:
                        with store_db.get_conn(ctx.db_path) as conn:
                            return metaqa.render_meta_answer(conn, ds_id)

                    answer = await anyio.to_thread.run_sync(_render)
                if answer is not None:
                    meta_contract = {
                        "kind": "meta",
                        "reasoning": answer,
                        "assumptions": [],
                        "need_tables": [],
                        "sql": "",
                        "followups": [],
                    }
                    elapsed = int((time.monotonic() - started) * 1000)
                    await _persist_reasoning()  # 思考全文落库（意图分类 + 整理两段的合并流）
                    await _stage(ctx, "generate", round_, "done", elapsed)
                    await ctx.emit("contract", {"type": "contract", "contract": meta_contract}, True)
                    return {"contract": meta_contract, "sql": "", "assumptions": [], "meta_answer": True}

    # ---- P6：领取运行中输入（DSH inbox 语义，在缓存短路与信封组装之前生效） ----
    inbox_note = await _drain_inbox(ctx)
    if inbox_note:
        state = {**state, "question": str(state["question"]) + inbox_note}

    # ---- 样例缓存命中（question_norm 精确 ∧ on ∧ semantic_rev 相等） ----
    if _s(ctx, "sample.cache") and not state.get("clarify_choice") and round_ == 0:
        hit = await anyio.to_thread.run_sync(_cache_lookup, ctx, ds_id, state["question"], dialect)
        if hit is not None:
            hit_sql = await _annotate(ctx, ds_id, hit.sql)
            await ctx.emit("sql", {"type": "sql", "sql": hit_sql, "round": round_}, True)
            await ctx.emit(
                "meta",
                {
                    "type": "meta",
                    "phase": "post_supply",
                    "hit_tables": [],
                    "route": {},
                    "tokens": int(state.get("tokens", 0)),
                },
                True,
            )
            await _stage(ctx, "generate", round_, "done", int((time.monotonic() - started) * 1000))
            return {
                "sql": hit_sql,
                "cache_hit": True,
                "contract": {
                    "reasoning": "复用已验证结果（样例缓存命中）",
                    "assumptions": [],
                    "sql": hit_sql,
                },
                "assumptions": [],
            }

    # ---- 信封组装（预览同源唯一生成器） ----
    def _assemble() -> dict[str, Any]:
        with store_db.get_conn(ctx.db_path) as conn:
            session_summary = None
            rounds: list[dict[str, Any]] = []
            if state.get("session_id"):
                session_summary = store_sessions.get_session(conn, str(state["session_id"])).summary
                history_k = int(_s(ctx, "history.rounds") or 0)
                if history_k > 0:
                    rounds = store_queries.recent_rounds(
                        conn,
                        str(state["session_id"]),
                        exclude_query_id=ctx.query_id,
                        limit=history_k,
                    )
            return llm_prompts.assemble_envelope(
                conn,
                dialect=dialect,
                datasource_id=ds_id,
                question=state["question"],
                schema_text=state.get("schema_text", ""),
                dialect_card=sql_dialects.load_card(dialect),
                session_summary=session_summary,
                recent_rounds=rounds,
            )

    envelope = await anyio.to_thread.run_sync(_assemble)
    messages = llm_prompts.envelope_to_messages(envelope)

    route_ref = str(_s(ctx, "route.generate") or "")
    # 思考模型含推理预算（实测 GLM-5.3 复杂问题推理 7-12 万字符）
    gen_budget = int(_s(ctx, "llm.generate_max_tokens") or 32768)
    outcome = await ctx.chat_fn(route_ref, messages, gen_budget, on_delta=_on_delta)  # E2001 由适配器抛出
    tokens = int(state.get("tokens", 0)) + int(getattr(outcome, "tokens", 0))

    contract: llm_prompts.GenerationContract
    contract, degraded = await _parse_with_retry(ctx, route_ref, messages, outcome.text)
    await _persist_reasoning()  # 生成思考全文落库（reasoning_full 事件，回放可见完整推理）
    assumptions = [a.model_dump() for a in contract.assumptions]
    clarify_options = [
        {"label": a["text"], "expr": a["text"]} for a in assumptions if a.get("impact") == "high"
    ]
    elapsed = int((time.monotonic() - started) * 1000)
    await _stage(ctx, "generate", round_, "done", elapsed)
    final_sql = await _annotate(ctx, ds_id, contract.sql)
    contract_dump = contract.model_dump()
    contract_dump["sql"] = final_sql
    await ctx.emit("contract", {"type": "contract", "contract": contract_dump}, True)
    await ctx.emit("sql", {"type": "sql", "sql": final_sql, "round": round_}, True)
    return {
        "contract": contract_dump,
        "sql": final_sql,
        "assumptions": assumptions,
        "clarify_options": clarify_options,
        "degraded_contract": degraded,
        "tokens": tokens,
    }


def _strict_parse(raw: str) -> llm_prompts.GenerationContract:
    text = raw.strip()
    start = text.index("{")
    end = text.rindex("}") + 1
    return llm_prompts.GenerationContract.model_validate(json.loads(text[start:end]))


async def _parse_with_retry(
    ctx: NodeContext,
    route_ref: str,
    messages: list[dict[str, str]],
    raw: str,
) -> tuple[llm_prompts.GenerationContract, bool]:
    """契约校验失败 → 原样回喂重试 1 次 → 正则提取 ```sql 降级（E2002 标注）。"""
    try:
        return _strict_parse(raw), False
    except (ValueError, ValidationError) as exc:
        logger.info("契约首解析失败，原样回喂重试 1 次")
        logger.warning("契约首解析失败 detail=%s raw[:800]=%r", exc, raw[:800])  # 调试日志（硬化期收敛）
    retry_messages = [
        *messages,
        {"role": "assistant", "content": raw},
        {"role": "user", "content": "上次输出未通过 JSON 契约校验，请严格按契约重新输出"},
    ]
    outcome2 = await ctx.chat_fn(
        route_ref, retry_messages, int(_s(ctx, "llm.generate_max_tokens") or 32768)
    )
    try:
        return _strict_parse(outcome2.text), False
    except (ValueError, ValidationError) as exc2:
        logger.warning(
            "契约重试仍失败 detail=%s raw[:800]=%r", exc2, outcome2.text[:800]
        )  # 调试日志（硬化期收敛）
        # 降级：正则提取 ```sql 块（parse_contract 内含降级；无 SQL 块则抛 E2002）
        return llm_prompts.parse_contract(outcome2.text), True


def _cache_lookup(ctx: NodeContext, ds_id: int, question: str, dialect: str) -> Any:
    with store_db.get_conn(ctx.db_path) as conn:
        from app.ingest import capability

        rev = store_samples.compute_semantic_rev(conn, ds_id, capability.card_hash(dialect))
        return store_samples.cache_lookup(conn, ds_id, question, rev)


# ---------- clarify_gate（BR-4 / FR-Q8） ----------


async def clarify_gate(state: QueryState, *, ctx: NodeContext) -> dict:
    """高风险歧义澄清门：interrupt({type:'clarify', options})；命中 clarify_pref 免问。"""
    round_ = int(state.get("round", 0))
    await _stage(ctx, "clarify", round_, "start")
    options = state.get("clarify_options") or []

    # 命中偏好免问（BR-4：同数据源同 term 下次免问 + 沉淀提示）
    def _pref_hit() -> str | None:
        with store_db.get_conn(ctx.db_path) as conn:
            for opt in options:
                pref = store_sessions.get_clarify_pref(
                    conn, int(state["datasource_id"]), str(opt.get("label", ""))[:50]
                )
                if pref:
                    return pref.chosen_rule
        return None

    remembered = await anyio.to_thread.run_sync(_pref_hit)
    if remembered:
        await _stage(ctx, "clarify", round_, "done")
        return {
            "clarify_choice": remembered,
            "question": state["question"] + f"\n澄清口径（已沉淀免问）：{remembered}",
        }

    await ctx.emit("clarify_required", {"type": "clarify_required", "options": options}, True)
    await ctx.persist_fn(status="waiting_clarify")
    # interrupt 前无引擎/写库副作用（事件落库即门负载的 app.db 侧事实）
    decision = interrupt({"type": "clarify", "options": options})
    choice = str((decision or {}).get("choice") or "")
    await _stage(ctx, "clarify", round_, "done")
    # 自定义 → 文本追加 question 重进 generate（SSOT §5.2 clarify_gate 失败路径）
    appended = state["question"] + f"\n澄清口径：{choice}" if choice else state["question"]
    return {"clarify_choice": choice, "question": appended}


# ---------- M5 validate（执行序铁律 D-03） ----------


async def validate(state: QueryState, *, ctx: NodeContext) -> dict:
    """清洗 → R15 优先 → auto_limit AST 注入 → R01-R14 → 标识符归一 → EXPLAIN 干跑。

    执行序铁律（P1-15 修复，SSOT D-03）：**R15 先于 auto_limit**——无法解析的
    SQL 必须落 R15-B2 硬阻断（E3010 五模板文案），不得在注入阶段以 ParseError
    冒泡、被 runner 误映射为 E2001「模型端点无响应」；注入自身解析兜底同归 B2
    （fail-closed）。所验即所执：本节点产出终串后禁止任何模块重写 SQL。
    """
    started = time.monotonic()
    round_ = int(state.get("round", 0))
    dialect = str(state["dialect"])
    await _stage(ctx, "validate", round_, "start")

    sql = sql_clean.unicode_clean(state["sql"])

    # ① R15 优先（对清洗后原 SQL；硬阻断：failed 交付原 SQL+原因，不进 correct/门）
    hit = sql_rules.hard_block_r15(sql, dialect=dialect)
    if hit is None:
        # ② auto_limit AST 注入（R15 已过 → 语法可解析；注入内解析兜底归 B2）
        try:
            sql = sql_rules.inject_auto_limit(
                sql, dialect=dialect, limit=int(_s(ctx, "query.auto_limit") or 1000)
            )
        except Exception:
            hit = {
                "rule_id": "R15",
                "level": "red",
                "message": sql_rules.R15_MESSAGES["B2"],
                "assertion": "B2",
            }
    if hit:
        await ctx.emit(
            "risk",
            {
                "type": "risk",
                "risks": [{"rule_id": hit["rule_id"], "level": hit["level"], "message": hit["message"]}],
            },
            True,
        )
        await ctx.emit(
            "error",
            {"type": "error", "code": E_HARD_BLOCK, "message": hit["message"], "retryable": False},
            True,
        )
        await ctx.persist_fn(status="failed", sql_final=sql)
        await _stage(ctx, "validate", round_, "done", int((time.monotonic() - started) * 1000))
        return {
            "sql": sql,
            "hard_blocked": True,
            "status": "failed",
            "risks": [{"rule_id": "R15", "level": "red", "message": hit["message"]}],
        }

    caps, meta = await anyio.to_thread.run_sync(_load_caps_meta, ctx, dialect)
    risks = sql_rules.check_all(
        sql,
        dialect=dialect,
        caps=caps,
        meta=meta,
        ds_mode=str(state.get("ds_mode") or "online"),
        db_name=ctx.datasource.get("db_name"),
        auto_limit=int(_s(ctx, "query.auto_limit") or 1000),
        scan_warn_rows=int(_s(ctx, "query.explain_scan_warn_rows") or 5_000_000),
    )
    if risks:
        await ctx.emit("risk", {"type": "risk", "risks": list(risks)}, True)
    sql = sql_rules.normalize_sql(sql, dialect=dialect)

    # EXPLAIN 干跑（AST 去 LIMIT 副本；失败降级 E3001 标注「未经验证」）
    explain_rows: int | None = None
    explain_skipped = False
    if state.get("ds_mode") == "online":
        explain_rows, explain_skipped = await _dry_run(ctx, sql, dialect)
    elapsed = int((time.monotonic() - started) * 1000)
    await _stage(ctx, "validate", round_, "done", elapsed)
    update: dict[str, Any] = {"sql": sql, "risks": list(risks), "explain_skipped": explain_skipped}
    if explain_rows is not None:
        update["explain_rows"] = explain_rows
    if sql_rules.has_correctable_risk(risks) and str(state.get("ds_mode")) == "online":
        update["needs_correct"] = True
        await ctx.emit(
            "correction",
            {
                "type": "correction",
                "round": round_ + 1,
                "sql": sql,
                "error": "可修正校验风险（R10/R13）",
                "fingerprint": sql_rules.fingerprint(sql, dialect=dialect),
            },
            True,
        )
    return update


def _load_caps_meta(ctx: NodeContext, dialect: str) -> tuple[dict, dict]:
    with store_db.get_conn(ctx.db_path) as conn:
        caps = sql_dialects.load_card(dialect)
        meta = store_datasources.build_meta_view(conn, int(ctx.datasource["id"]))
    return caps, meta


async def _dry_run(ctx: NodeContext, sql: str, dialect: str) -> tuple[int | None, bool]:
    from app.exec import runner as exec_runner

    timeout_s = int(_s(ctx, "query.explain_timeout_s") or 5)

    def _query_fn(stmt: str, _params: Any) -> list[tuple]:
        conn = exec_runner.connect_engine(ctx.datasource, ctx.password, timeout_s=timeout_s)
        try:
            return exec_runner.engine_query(conn, dialect, stmt)
        finally:
            exec_runner._close_quietly(conn)

    def _run() -> dict[str, Any]:
        return sql_explain.dry_run(
            sql,
            dialect=dialect,
            ds_mode=str(ctx.datasource.get("mode", "online")),
            caps=sql_dialects.load_card(dialect),
            query_fn=_query_fn,
            timeout_s=timeout_s,
        )

    result = await anyio.to_thread.run_sync(_run)
    if not result["ok"]:
        logger.info("干跑跳过/失败（E3001 未经验证）: %s", result["error"])
        return None, True
    return result["rows"], False


# ---------- confirm_gate（BR-6 / FR-Q6） ----------


async def confirm_gate(state: QueryState, *, ctx: NodeContext) -> dict:
    """非纯 SELECT → interrupt({type:'confirm_write', risks, est_rows})；取消 → stopped。"""
    round_ = int(state.get("round", 0))
    await _stage(ctx, "confirm", round_, "start")
    payload = {
        "type": "confirm_write",
        "risks": list(state.get("risks") or []),
        "est_rows": state.get("explain_rows") or 0,
    }
    await ctx.emit(
        "confirm_required",
        {"type": "confirm_required", "risks": payload["risks"], "est_rows": payload["est_rows"]},
        True,
    )
    await ctx.persist_fn(status="waiting_confirm")
    decision = interrupt(payload)
    approved = bool((decision or {}).get("approved"))
    if not approved:
        await ctx.emit("stopped", {"type": "stopped", "reason": "user_cancel"}, True)
        await ctx.persist_fn(status="stopped")
        await _stage(ctx, "confirm", round_, "done")
        return {"status": "stopped", "gate_decision": "cancelled", "write_confirmed": False}
    from app.store import executions

    sql = str(state.get("sql") or "")

    def _approve() -> None:
        with store_db.get_conn(ctx.db_path) as conn:
            executions.approve(conn, ctx.query_id, sql)

    await anyio.to_thread.run_sync(_approve)
    await ctx.persist_fn(status="running")
    await _stage(ctx, "confirm", round_, "done")
    return {
        "write_confirmed": True,
        "gate_decision": "approved",
        "approved_sql_hash": executions.sql_hash(sql),
    }


# ---------- M6 execute ----------


async def execute(state: QueryState, *, ctx: NodeContext) -> dict:
    """工作/管理连接分离；CONNECTION_ID；超时矩阵；纯 SELECT 成功全量落盘 CSV。"""
    started = time.monotonic()
    round_ = int(state.get("round", 0))
    dialect = str(state["dialect"])
    await _stage(ctx, "execute", round_, "start")
    sql = state["sql"]

    if state.get("ds_mode") != "online":
        # 离线库：跳过执行（generate_only，FR-D13「复制 SQL」主按钮）
        await _stage(ctx, "execute", round_, "done", int((time.monotonic() - started) * 1000))
        return {"exec_meta": {}, "status": "done"}

    from app.exec import meta as exec_meta_mod
    from app.exec import runner as exec_runner

    def _run() -> exec_runner.ExecOutcome:
        return exec_runner.run_query(
            sql,
            query_id=ctx.query_id,
            ds=ctx.datasource,
            password=ctx.password,
            timeout_s=int(_s(ctx, "query.timeout_s") or 30),
            ch_client_timeout_s=int(_s(ctx, "query.ch_client_timeout_s") or 35),
            ch_readonly=bool(_s(ctx, "query.ch_readonly")),
            pool_size=int(_s(ctx, "query.pool_size") or 2),
            max_rows=int(_s(ctx, "query.max_result_rows") or 10000),
            max_bytes=int(_s(ctx, "query.max_result_mb") or 16) * 1024 * 1024,
        )

    from app.store import executions

    is_write = not sql_rules.is_pure_select_sql(sql, dialect=dialect)
    require_approval = bool(_s(ctx, "query.write_confirm"))

    def _start_write() -> None:
        with store_db.get_conn(ctx.db_path) as conn:
            executions.start(conn, ctx.query_id, sql, require_approval=require_approval)

    def _finish_write(succeeded: bool) -> None:
        with store_db.get_conn(ctx.db_path) as conn:
            executions.finish(conn, ctx.query_id, sql, succeeded=succeeded)

    if is_write:
        # Durable exact-SQL approval is the execution boundary, even after recovery.
        await anyio.to_thread.run_sync(_start_write)
    try:
        outcome = await anyio.to_thread.run_sync(_run)
        if is_write:
            await anyio.to_thread.run_sync(_finish_write, True)
    except exec_runner.ResourceLimitError:
        await ctx.emit(
            "error",
            {
                "type": "error",
                "code": "E3011",
                "retryable": False,
                "message": "查询结果超过行数、字节或时间预算，请缩小查询范围",
            },
            True,
        )
        return {"status": "failed", "result_unverified": True}
    except (exec_runner.ConnectError, exec_runner.EngineError) as exc:
        if is_write:
            await anyio.to_thread.run_sync(_finish_write, False)
            await ctx.emit(
                "error",
                {
                    "type": "error",
                    "code": "E5001",
                    "retryable": False,
                    "message": "写操作结果待核对，已停止自动重试；请核对目标数据库后另行操作",
                },
                True,
            )
            return {"status": "failed", "result_unverified": True}
        if isinstance(exc, exec_runner.EngineError):
            masked = sql_clean.mask_error(str(exc))
            history = list(state.get("error_history") or [])
            history.append(
                CorrectionRound(
                    round=round_ + 1,
                    sql=sql,
                    error=masked,
                    fingerprint=sql_rules.fingerprint(sql, dialect=dialect),
                )
            )
            await _stage(ctx, "execute", round_, "done", int((time.monotonic() - started) * 1000))
            return {"needs_correct": True, "error_history": history}
        masked = sql_clean.mask_error(str(exc))
        await ctx.emit(
            "error",
            {
                "type": "error",
                "code": "E1001",
                "message": f"数据源连接失败（主机不可达或超时）：{masked}",
                "retryable": True,
            },
            True,
        )
        await _stage(ctx, "execute", round_, "done", int((time.monotonic() - started) * 1000))
        return {"status": "failed"}
    except Exception:
        if is_write:
            await anyio.to_thread.run_sync(_finish_write, False)
            raise
        raise
    columns = [c["name"] for c in outcome.columns]
    col_meta, preview_rows, truncated = exec_meta_mod.pack_preview(columns, outcome.rows)
    exec_meta: dict[str, Any] = dict(outcome.exec_meta)
    exec_meta["scan_rows"] = state.get("explain_rows")

    def _persist_and_dump() -> str | None:
        from app.store import queries as store_queries

        with store_db.get_conn(ctx.db_path) as conn:
            meta_view = store_datasources.build_meta_view(conn, int(state["datasource_id"]))
            # F6：WAL 并发写瞬时冲突（locked/busy）短重试一次；仍失败则抛出——
            # 结果落盘是展示依赖，失败应显式报错而非降级静默
            for attempt in (1, 2):
                try:
                    store_queries.save_preview(
                        conn, query_id=ctx.query_id, columns=col_meta, rows=preview_rows, truncated=truncated
                    )
                    break
                except sqlite3.OperationalError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)
            # 生成反馈闭环（演进 #1）：成功执行的真实物理表回写检索权重；
            # 附加产物语义同 CSV 导出——失败静默，绝不污染主查询成功结果
            try:
                from app.retrieval import feedback as retrieval_feedback

                used_tables = sql_rules.tables_in_sql(sql, dialect=dialect)
                if used_tables:
                    retrieval_feedback.record_success(conn, int(state["datasource_id"]), used_tables)
            except Exception:
                logger.info("检索反馈回写失败（静默跳过）: %s", ctx.query_id)
        # F19：引擎网络调用（connect_timeout=10s）移出 app.db 连接持有段，
        # 缩短 sqlite 占用、收窄写锁竞争面
        # data_max_time 采集的二次建连失败 → 降级 None（主查询已成功，
        # 不得因附加采集把结果砸成 E5000）
        dmt: str | None = None
        conn_exec = None
        try:
            conn_exec = exec_runner.connect_engine(
                ctx.datasource,
                ctx.password,
                timeout_s=int(_s(ctx, "query.timeout_s") or 30),
                readonly=(dialect == "clickhouse"),
            )  # CH readonly=1 强制（D05）
            dmt = exec_meta_mod.collect_data_max_time(
                lambda s, p: exec_runner.engine_query(conn_exec, dialect, s, p),
                dialect,
                meta_view,
                list(state.get("hit_tables") or []),
            )
        except Exception:
            # F7：带异常详情——线上须能区分「建连失败」与「代码 bug」
            logger.warning("data_max_time 采集失败（降级跳过）: %s", ctx.query_id, exc_info=True)
        finally:
            if conn_exec is not None:
                exec_runner._close_quietly(conn_exec)
        # 纯 SELECT 成功时全量落盘 exports/（§10.5 D14；写操作不产生导出）
        # F5：CSV 导出是附加产物，与 data_max_time 同级降级——磁盘满/权限错误
        # 不得污染主查询成功结果为 E5000；失败标记 export_failed 供前端提示
        if sql_rules.is_pure_select_sql(sql, dialect=dialect):
            try:
                exec_meta_mod.dump_csv(ctx.query_id, columns, outcome.rows)
            except Exception:
                logger.warning("CSV 导出失败（降级跳过）: %s", ctx.query_id, exc_info=True)
                exec_meta["export_failed"] = True
        return dmt

    data_max_time = await anyio.to_thread.run_sync(_persist_and_dump)
    if data_max_time:
        exec_meta["data_max_time"] = data_max_time
    elapsed = int((time.monotonic() - started) * 1000)
    await _stage(ctx, "execute", round_, "done", elapsed)
    await ctx.emit(
        "result_meta",
        {
            "type": "result_meta",
            "rows": exec_meta.get("rows", 0),
            "scan_rows": exec_meta.get("scan_rows"),
            "duration_ms": exec_meta.get("duration_ms"),
            "data_max_time": exec_meta.get("data_max_time"),
            "truncated": truncated,
        },
        True,
    )
    # result 批量行不入 query_event（D-06/C3.1：走 result_preview；仅推送不持久化）
    await ctx.emit("result", {"type": "result", "columns": col_meta, "rows": preview_rows}, False)
    return {"exec_meta": exec_meta, "result_preview_id": ctx.query_id}


# ---------- verify_result（BR-8，回退全局共享 1 次） ----------


async def verify_result(state: QueryState, *, ctx: NodeContext) -> dict:
    """规则层 5 检查 + 可选 LLM 层；不过且额度未用 → 回退进 correct；仍不过照常交付。"""
    started = time.monotonic()
    round_ = int(state.get("round", 0))
    await _stage(ctx, "verify", round_, "start")
    exec_meta = state.get("exec_meta") or {}

    if not sql_rules.is_pure_select_sql(str(state.get("sql") or ""), dialect=str(state["dialect"])):
        # The target accepted the statement; read-result quality rules do not apply to writes.
        await _stage(ctx, "verify", round_, "done")
        return {
            "needs_correct": False,
            "verify_checks": [],
            "verify_passed": False,
            "result_unverified": True,
            "status": "done",
        }
    preview = await anyio.to_thread.run_sync(_load_preview, ctx)
    columns = (preview or {}).get("columns", [])
    rows = (preview or {}).get("rows", [])
    partition_pruned = _partition_pruned(state)
    outcome = verify_rules.run_rules(
        exec_meta=exec_meta,
        columns=columns,
        rows=rows,
        explain_rows=state.get("explain_rows"),
        partition_pruned=partition_pruned,
        expected_total=(state.get("contract") or {}).get("expected_total"),
        aggregated=_is_aggregated(state.get("sql") or ""),
    )

    passed = outcome.passed
    # 可选 LLM 层（feature.verify_llm；local_only 置灰；E6 出境类目）
    if not passed and verify_llm.is_available(bool(_s(ctx, "feature.verify_llm")), bool(_local_only(ctx))):
        try:
            llm_verdict = await verify_llm.verify_with_llm(
                question=state["question"],
                assumptions=state.get("assumptions") or [],
                columns=columns,
                rows=rows,
                chat_fn=lambda msgs: _chat_text(ctx, str(_s(ctx, "route.fix") or ""), msgs),
            )
            if llm_verdict["passed"]:
                passed = True
        except Exception:
            logger.info("verify LLM 层失败，按规则层结论")

    checks = [dict(c) for c in outcome.checks]
    await ctx.emit(
        "verify",
        {
            "type": "verify",
            "checks": checks,
            "passed": passed,
            "fallback_used": bool(state.get("verify_fallback_used")),
        },
        True,
    )
    elapsed = int((time.monotonic() - started) * 1000)
    await _stage(ctx, "verify", round_, "done", elapsed)

    if passed:
        return {"verify_checks": checks, "verify_passed": True}
    if verify_rules.decide_fallback(bool(state.get("verify_fallback_used")), outcome.fallback_reason):
        # 消耗全局唯一回退额度：理由进 error_history 交 correct（不修正 SQL 在本节点）
        history = list(state.get("error_history") or [])
        history.append(
            CorrectionRound(
                round=round_ + 1,
                sql=state.get("sql", ""),
                error=f"结果校验未过：{outcome.fallback_reason}（消耗回退额度）",
                fingerprint=sql_rules.fingerprint(state.get("sql", ""), dialect=str(state["dialect"])),
            )
        )
        return {
            "verify_checks": checks,
            "verify_passed": False,
            "verify_fallback_used": True,
            "needs_correct": True,
            "error_history": history,
        }
    # 仍不过 → result_unverified=true 照常 END（琥珀「未验证」标注）
    return {"verify_checks": checks, "verify_passed": False, "result_unverified": True}


def _load_preview(ctx: NodeContext) -> dict[str, Any] | None:
    from app.store import queries as store_queries

    with store_db.get_conn(ctx.db_path) as conn:
        p = store_queries.get_preview(conn, ctx.query_id)
        return {"columns": p.columns, "rows": p.rows} if p else None


def _partition_pruned(state: QueryState) -> bool | None:
    for r in state.get("risks") or []:
        if r["rule_id"] == "R14":
            return False
    return None


async def _annotate(ctx: NodeContext, ds_id: int, sql: str) -> str:
    """SQL 详细备注注入（feature.sql_comments 开关）：表/字段中文名注释块。"""
    if not sql or not _s(ctx, "feature.sql_comments"):
        return sql

    def _run() -> str:
        with store_db.get_conn(ctx.db_path) as conn:
            return sql_annotate.annotate_sql(conn, ds_id, sql)

    return await anyio.to_thread.run_sync(_run)


def _is_aggregated(sql: str) -> bool:
    """聚合查询判定（verify 行数偏离检查豁免依据）：GROUP BY 或聚合函数出现即聚合。"""
    return sql_rules.is_aggregated(sql)


def _local_only(ctx: NodeContext) -> bool:
    from app.store import providers as store_providers
    from app.store import settings as store_settings

    with store_db.get_conn(ctx.db_path) as conn:
        routes = {k: str(_s(ctx, k) or "") for k in ("route.generate", "route.fix", "route.ai")}
        local_ids = {p.id for p in store_providers.query_providers(conn) if p.protocol == "ollama"}
        return store_settings.is_local_only(routes, local_ids)


async def _chat_text(ctx: NodeContext, route_ref: str, messages: list[dict[str, str]]) -> str:
    # 思考型模型（glm-5.3）推理与正文共享预算：1024 会被思考吃满致正文为空；
    # 8192 覆盖意图识别级短推理，EMPTY_RESPONSE 预算翻倍恢复兜底（runner._make_chat）
    outcome = await ctx.chat_fn(route_ref, messages, 8192)
    return str(getattr(outcome, "text", ""))


# ---------- correct（BR-9：≤3 轮 + 指纹振荡终止） ----------


async def correct(state: QueryState, *, ctx: NodeContext) -> dict:
    """修正回路：脱敏错误+历史指纹 → 修正路由；round≥3 或指纹振荡 → 终止交付标 failed。"""
    started = time.monotonic()
    dialect = str(state["dialect"])
    round_ = int(state.get("round", 0)) + 1
    max_rounds = int(_s(ctx, "query.max_fix_rounds") or 3)
    await _stage(ctx, "correct", round_, "start")

    current_sql = state.get("sql", "")
    fp = sql_rules.fingerprint(current_sql, dialect=dialect)
    fingerprints = list(state.get("fingerprints") or [])
    history = list(state.get("error_history") or [])
    last_error = history[-1]["error"] if history else ""

    # 终止条件：轮数上限 / 指纹振荡（A→B→A 死循环检测）
    if round_ > max_rounds or fp in fingerprints:
        reason = "轮数上限" if round_ > max_rounds else "指纹振荡"
        await ctx.emit(
            "correction",
            {
                "type": "correction",
                "round": round_,
                "sql": current_sql,
                "error": f"修正终止（{reason}）",
                "fingerprint": fp,
            },
            True,
        )
        await ctx.persist_fn(status="failed", sql_final=current_sql, rounds=round_)
        # P1-14：落 error 事件（E2004）——前端渲染错误卡，不得以 done 静默交付
        await ctx.emit(
            "error",
            {
                "type": "error",
                "code": E_FIX_EXHAUSTED,
                "message": f"修正终止（{reason}），未得出可交付结果（最近一版 SQL 已保留，可调整后重新提问）",
                "retryable": False,
            },
            True,
        )
        await _stage(ctx, "correct", round_, "done", int((time.monotonic() - started) * 1000))
        return {
            "status": "failed",
            "round": round_,
            "needs_correct": False,
            "fingerprints": [*fingerprints, fp],
        }

    # 修正提示词（脱敏错误历史 + 指纹）→ 修正路由（route.fix）
    history_text = "\n".join(f"[第{h['round']}轮] 错误: {h['error']}\nSQL: {h['sql']}" for h in history[-3:])
    messages = [
        {
            "role": "system",
            "content": llm_prompts.render_system_prompt(str(_s(ctx, "prompt.system") or ""), dialect),
        },
        {
            "role": "user",
            "content": (
                f"方言：{dialect}\n问题：{state['question']}\n"
                f"Schema：\n{state.get('schema_text', '')[:4000]}\n"
                f"当前 SQL：\n{current_sql}\n错误（已脱敏）：{last_error}\n"
                f"历史修正：\n{history_text or '（无）'}\n"
                "请输出修正后的严格 JSON 契约（reasoning/assumptions/sql）。"
            ),
        },
    ]
    # 修正轮推理流：live 外显（gen_delta stage=correct）+ 全文落库（reasoning_full）
    # 缓冲为替换语义（provider 的 reasoning delta 是累计快照，见 generate 节点注释）
    fix_reasoning_buf: list[str] = []

    async def _on_delta_fix(kind: str, text: str) -> None:
        if kind == "reasoning":
            fix_reasoning_buf.clear()
            fix_reasoning_buf.append(text)
        await ctx.emit(
            "gen_delta",
            {"type": "gen_delta", "stage": "correct", "round": round_, "kind": kind, "text": text},
            False,
        )

    outcome = await ctx.chat_fn(
        str(_s(ctx, "route.fix") or ""),
        messages,
        int(_s(ctx, "llm.generate_max_tokens") or 32768),
        on_delta=_on_delta_fix,
    )
    contract = llm_prompts.parse_contract(outcome.text)  # 含降级提取
    fix_reasoning = fix_reasoning_buf[-1].strip() if fix_reasoning_buf else ""
    if fix_reasoning:
        await ctx.emit(
            "reasoning_full",
            {"type": "reasoning_full", "round": round_, "text": fix_reasoning},
            True,
        )
    tokens = int(state.get("tokens", 0)) + int(getattr(outcome, "tokens", 0))

    await ctx.emit(
        "correction",
        {"type": "correction", "round": round_, "sql": contract.sql, "error": last_error, "fingerprint": fp},
        True,
    )
    # 修正产生的新 SQL 逐轮重发（sql 事件带 round；详细备注随开关注入）
    fixed_sql = await _annotate(ctx, int(state["datasource_id"]), contract.sql)
    await ctx.emit("sql", {"type": "sql", "sql": fixed_sql, "round": round_}, True)
    await _stage(ctx, "correct", round_, "done", int((time.monotonic() - started) * 1000))
    return {
        "sql": fixed_sql,
        "round": round_,
        "needs_correct": False,
        "fingerprints": [*fingerprints, fp],
        "tokens": tokens,
        "contract": contract.model_dump(),
    }


# 供 runner 装配节点表（langgraph 图构建在 runner.build_graph）
NODE_FUNCS = {
    "supply_schema": supply_schema,
    "generate": generate,
    "clarify_gate": clarify_gate,
    "validate": validate,
    "confirm_gate": confirm_gate,
    "execute": execute,
    "verify_result": verify_result,
    "correct": correct,
}

__all__ = ["NODE_FUNCS"]
