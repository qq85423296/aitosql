"""AI 补全编排引擎（方案 v1.1 §3.1 泳道协程池 / §4 证据包+五关校验 / §5 准确性管线）。

- 泳道：asyncio 工作池（默认 3，设置 ai.completion_workers 1–6）；项=一张表，按表名排序，
  泳道固定取队——同样输入同样分配（可复现排查）。
- 单写者收口：建议落库统一经 _db_lock 串行（SQLite WAL 防 database locked）；
  LLM 调用裸 await，不持 sqlite 连接（F-3）。
- 关系推断由 LLM 语义裁决（证据包=当前表字段+全库表索引+已有关系；审计枢纽列提示词显式排除），
  与注释/标准同走泳道；无规则快道。
- 五关校验：JSON 解析 → Schema → 目标存在性（幻觉防火墙）→ 置信度钳制（无依据 ≤0.6）→
  判重/人工值跳过。解析失败自我修正重试 1 次。
- 采样值画像：仅 online + 设置 ai.sample_values_allowed + run option use_sample_values，
  每表一次 SELECT … LIMIT 50，TOP-5 值/NULL 数/基数进证据包；默认不出境。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.errors import BizError
from app.store import ai_runs as store_runs
from app.store import datasources as store_datasources
from app.store import db
from app.store import suggestions as store_suggestions

logger = logging.getLogger("aitosql.ai.completion")

# D1/D2/D6 分层：本模块为 L3 领域模块，仅可 import app.store；
# LLM chat_fn / 采样 query_fn 均由 api 层注入（见 api/ai_runs.py），严禁跨域 import ingest/exec/llm。


def _exists_pending(conn: sqlite3.Connection, ds_id: int, kind: str, payload: dict) -> bool:
    """判重（忽略不回潮）：同数据源同 kind 同 payload 的 pending/ignored 已存在（幂等重跑）。"""
    row = conn.execute(
        "SELECT 1 FROM ai_suggestion WHERE datasource_id = ? AND kind = ?"
        " AND status IN ('pending', 'ignored') AND payload_json = ? LIMIT 1",
        (ds_id, kind, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    return row is not None


def _relation_pair_known(conn: sqlite3.Connection, ds_id: int, from_ref: str, to_ref: str) -> bool:
    """关系对已知判定：已确认 relation 或 pending/ignored 同向建议（忽略不回潮）。"""
    ft, fc = from_ref.split(".", 1)
    tt, tc = to_ref.split(".", 1)
    row = conn.execute(
        "SELECT 1 FROM relation WHERE datasource_id = ? AND from_table = ?"
        " AND from_col = ? AND to_table = ? AND to_col = ? LIMIT 1",
        (ds_id, ft, fc, tt, tc),
    ).fetchone()
    if row is not None:
        return True
    for r in conn.execute(
        "SELECT payload_json FROM ai_suggestion WHERE datasource_id = ? AND kind = 'relation'"
        " AND status IN ('pending', 'ignored')",
        (ds_id,),
    ).fetchall():
        try:
            p = json.loads(r["payload_json"])
        except ValueError:
            continue
        if p.get("from") == from_ref and p.get("to") == to_ref:
            return True
    return False


def _safe_reason(exc: Exception) -> str:
    """失败原因消毒上屏（S-1：底层原文不出网；BizError 文案上游已消毒）。"""
    if isinstance(exc, BizError):
        return exc.message
    if isinstance(exc, TimeoutError):
        return "模型调用超时"
    if isinstance(exc, (ConnectionError, OSError)):
        return "模型端点连接失败"
    return "处理失败（详见服务端日志）"


Emit = Callable[[dict], None]
ChatFn = Callable[[str, list[dict[str, str]], int], Awaitable[str]]
QueryFnFactory = Callable[[], tuple[Any, Any]]  # () → (query_fn|None, close_fn|None)

LLM_KINDS = ("comment", "std_bind", "relation")  # 全部走 LLM 泳道（关系推断为 AI 语义裁决，无规则快道）
MAX_COLS_PER_PROMPT = 50  # 决策点 5：批量阈值（已有信息全量入证据包；宽表截断上限）
MAX_PROMPT_TABLES_SIBLINGS = 30  # 证据包兄弟表清单上限


# ---------- 提示词与解析（§4 单轮结构化生成） ----------


def build_comment_prompt(
    table: str, cols: list[dict[str, Any]], table_comment: str | None, siblings: list[str]
) -> list[dict[str, str]]:
    """comment 批提示词：system 固化 Schema/硬约束；user=证据包。"""
    sys = (
        "你是数据库语义分析助手。为指定的表与字段补全中文业务注释（表/字段中文名 + 业务描述）。\n"
        "硬性约束：只输出 JSON 数组；不得臆造未给出的表名/字段名；每条必须给 reason；"
        "拿不准的 confidence 不超过 0.5。\n"
        "suggestion 格式统一为「中文名｜业务描述」：中文名 ≤10 字（看到名字就知道含义），"
        "业务描述一句话讲清业务口径/用途/取值含义；分隔符为全角竖线 ｜。\n"
        '输出 Schema：[{"target":"table" 或 "col:字段名", "suggestion":"中文名｜业务描述",'
        ' "confidence":0~1, "reason":"一句话依据"}]'
    )
    lines = [
        f"目标表：{table}" + (f"（现有表注释：{table_comment}）" if table_comment else "（表无注释）"),
        f"同库其他表：{'、'.join(siblings) or '（无）'}",
        "字段证据：",
    ]
    for c in cols:
        ev = [c["name"], c["type"]]
        keys = [
            k
            for k, f in (
                ("主键", c.get("is_pk")),
                ("分区", c.get("is_partition")),
                ("分桶", c.get("is_bucket")),
            )
            if f
        ]
        if keys:
            ev.append("/".join(keys))
        if c.get("comment"):
            ev.append(f"现有注释「{c['comment']}」")
        if c.get("top_values"):
            ev.append(f"样本值 {c['top_values']}")
        lines.append("- " + "，".join(ev))
    return [{"role": "system", "content": sys}, {"role": "user", "content": "\n".join(lines)}]


def build_std_bind_prompt(
    table: str, cols: list[dict[str, Any]], standards: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """std_bind 批提示词：standard 必须逐字取自目录（决策点 4：无命中不产建议）。"""
    sys = (
        "你是数据标准治理助手。判断字段应绑定哪个字段标准。\n"
        "硬性约束：只输出 JSON 数组；standard 必须逐字取自给定标准目录，目录无匹配就不要输出该字段；"
        "每条必须给 reason；拿不准的 confidence 不超过 0.5。\n"
        '输出 Schema：[{"column":"字段名", "standard":"目录中的标准名",'
        ' "confidence":0~1, "reason":"一句话依据"}]'
    )
    cat = [
        f"- {s['name']}（{s.get('data_type') or '?'}，单位 {s.get('unit') or '—'}"
        + (f"，枚举维度 {s['dimension']}" if s.get("dimension") else "")
        + "）"
        for s in standards
    ]
    lines = [f"目标表：{table}", "标准目录：", *cat, "字段证据："]
    for c in cols:
        ev = [f"{c['name']} {c['type']}"]
        if c.get("comment"):
            ev.append(f"注释「{c['comment']}」")
        if c.get("top_values"):
            ev.append(f"样本值 {c['top_values']}")
        lines.append("- " + "，".join(ev))
    return [{"role": "system", "content": sys}, {"role": "user", "content": "\n".join(lines)}]


def build_relation_prompt(
    table: str,
    table_comment: str | None,
    cols: list[dict[str, Any]],
    table_index_prompt: list[str],
    existing_pairs: list[str],
) -> list[dict[str, str]]:
    """relation 批提示词：证据包（当前表字段 + 候选目标表主键 + 已有关系）→ AI 判外键。

    决策（替代规则快道）：命名/主外键同型只是线索之一，由模型结合注释语义裁决；
    审计类枢纽列（全库同名共现）在提示词中显式排除；已有关系显式列出防重复。
    """
    sys = (
        "你是数仓建模专家。判断当前表的哪些字段是外键，应关联到哪张候选表的哪个列。\n"
        "硬性约束：只输出 JSON 数组；to_table/to_column 必须逐字取自候选表清单；"
        "每条必须给 reason；拿不准的 confidence 不超过 0.5；没有把握就输出 []。\n"
        "反模式（不要建议）：全库同名共现的审计/枢纽列（如 id、aoto_id、create_by、update_time）"
        "彼此不是外键关系；已存在的关系不要重复建议。\n"
        '输出 Schema：[{"column":"当前表字段名", "to_table":"目标表", "to_column":"目标列",'
        ' "confidence":0~1, "reason":"一句话依据"}]'
    )
    lines = [f"当前表：{table}" + (f"（{table_comment}）" if table_comment else ""), "字段："]
    for c in cols:
        ev = [f"{c['name']} {c['type']}"]
        if c.get("is_pk"):
            ev.append("主键")
        if c.get("comment"):
            ev.append(f"注释「{c['comment']}」")
        if c.get("top_values"):
            ev.append(f"样本值 {c['top_values']}")
        lines.append("- " + "，".join(ev))
    lines.append("候选目标表（表名｜注释｜主键列）：")
    lines.extend(f"- {row}" for row in table_index_prompt)
    if existing_pairs:
        lines.append("已有/已否关系（勿重复建议）：")
        lines.extend(f"- {p}" for p in existing_pairs[:40])
    return [{"role": "system", "content": sys}, {"role": "user", "content": "\n".join(lines)}]


def validate_relation_items(
    items: list[dict[str, Any]],
    table: str,
    col_names: set[str],
    table_index: dict[str, dict[str, Any]],
    existing_pairs: set[str],
) -> list[dict[str, Any]]:
    """relation 五关：源列/目标表/目标列必须真实存在（幻觉防火墙）；自指与已知对剔除。"""
    out = []
    for it in items:
        col = str(it.get("column") or "")
        to_table = str(it.get("to_table") or "")
        to_col = str(it.get("to_column") or "")
        reason = str(it.get("reason") or "").strip()
        if col not in col_names or to_table not in table_index or to_table == table:
            continue
        if to_col not in table_index[to_table]["cols"]:
            continue
        from_ref, to_ref = f"{table}.{col}", f"{to_table}.{to_col}"
        if f"{from_ref}->{to_ref}" in existing_pairs:
            continue
        conf = _clamp_conf(it.get("confidence"), bool(reason), True)
        out.append(
            {
                "from": from_ref,
                "to": to_ref,
                "via": "llm",
                "reason": reason or "（AI 语义推断）",
                "confidence": conf,
            }
        )
    return out


def parse_json_array(raw: str) -> list[dict[str, Any]]:
    """第一关：JSON 解析（截取首个数组，fail-quiet 返回空）。"""
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _clamp_conf(v: Any, has_reason: bool, has_evidence: bool) -> float:
    """第四关：置信度钳制（缺省 0.5；无证据上限 0.6；有证据上限 0.9）。"""
    try:
        c = float(v)
    except (TypeError, ValueError):
        c = 0.5
    cap = 0.9 if has_evidence else (0.6 if has_reason else 0.5)
    return max(0.0, min(c, cap, 1.0))


def validate_comment_items(
    items: list[dict[str, Any]], table: str, col_names: set[str]
) -> list[dict[str, Any]]:
    """comment 五关：Schema → 目标存在 → 钳制 → 组装 payload（判重在落库侧）。"""
    out = []
    for it in items:
        target, sug = str(it.get("target") or ""), str(it.get("suggestion") or "").strip()
        reason = str(it.get("reason") or "").strip()
        if not sug or len(sug) > 200:
            continue
        if target == "table":
            column = ""
        elif target.startswith("col:") and target[4:] in col_names:
            column = target[4:]
        else:
            continue  # 幻觉防火墙：指向不存在的目标
        conf = _clamp_conf(it.get("confidence"), bool(reason), False)
        out.append(
            {
                "table": table,
                "column": column,
                "suggested": sug,
                "reason": reason or "（无依据）",
                "confidence": conf,
            }
        )
    return out


def validate_std_bind_items(
    items: list[dict[str, Any]], table: str, col_names: set[str], std_names: set[str]
) -> list[dict[str, Any]]:
    """std_bind 五关：字段/标准必须真实存在（幻觉防火墙）。"""
    out = []
    for it in items:
        col, std = str(it.get("column") or ""), str(it.get("standard") or "")
        reason = str(it.get("reason") or "").strip()
        if col not in col_names or std not in std_names:
            continue
        conf = _clamp_conf(it.get("confidence"), bool(reason), True)
        out.append(
            {
                "table": table,
                "column": col,
                "standard": std,
                "reason": reason or "（无依据）",
                "confidence": conf,
            }
        )
    return out


# ---------- 采样值画像（§6：默认不出境，开关门控） ----------


def profile_table(
    query_fn: Callable[[str, Any], list[tuple]], dialect: str, table: str, cols: list[str], limit: int = 50
) -> dict[str, dict[str, Any]]:
    """单查询采样：TOP-5 值 / NULL 数 / 基数（Python 侧聚合，一表一次查询）。"""
    if not cols:
        return {}

    def q(n: str) -> str:
        return f"`{n.replace('`', '')}`"  # mysql/doris/clickhouse 均接受反引号

    sql = f"SELECT {', '.join(q(c) for c in cols)} FROM {q(table)} LIMIT {limit}"
    try:
        rows = query_fn(sql, None)
    except Exception:
        logger.info("采样失败（跳过画像）: %s", table, exc_info=True)
        return {}
    prof: dict[str, dict[str, Any]] = {c: {"freq": {}, "nulls": 0} for c in cols}
    for row in rows:
        for i, c in enumerate(cols):
            v = row[i] if i < len(row) else None
            if v is None:
                prof[c]["nulls"] += 1
            else:
                s = str(v)[:40]
                prof[c]["freq"][s] = prof[c]["freq"].get(s, 0) + 1
    out = {}
    for c, p in prof.items():
        top = sorted(p["freq"].items(), key=lambda kv: -kv[1])[:5]
        out[c] = {"top_values": [v for v, _n in top], "nulls": p["nulls"], "cardinality": len(p["freq"])}
    return out


# ---------- 引擎 ----------


class CompletionEngine:
    """一次 ai_run 的执行体：泳道池 + 事件流 + 取消（chat_fn/query_fn_factory 注入式）。"""

    def __init__(
        self,
        run_id: int,
        emit: Emit,
        cancel: asyncio.Event,
        chat_fn: ChatFn | None,
        query_fn_factory: QueryFnFactory | None = None,
    ) -> None:
        self.run_id = run_id
        self.emit = emit
        self.cancel = cancel
        self.chat_fn = chat_fn
        self.query_fn_factory = query_fn_factory
        self._db_lock = asyncio.Lock()  # 单写者收口
        self.made: dict[str, int] = {}
        self.lanes: dict[int, dict] = {}  # lane -> {table, phase, line}

    # -- DB 桥（短持连接；写库串行） --
    async def _db[T](self, fn: Callable[..., T], *args: Any, lock: bool = True, **kwargs: Any) -> T:
        def _wrap() -> T:
            with db.get_conn() as conn:
                return fn(conn, *args, **kwargs)

        if lock:
            async with self._db_lock:
                return await asyncio.to_thread(_wrap)
        return await asyncio.to_thread(_wrap)

    def _lane(self, lane: int, table: str, phase: str, line: str, extra: dict | None = None) -> None:
        self.lanes[lane] = {"table": table, "phase": phase, "line": line}
        ev = {"lane": lane, "table": table, "phase": phase, "line": line, **(extra or {})}
        self.emit(ev)

    async def _settle(self, made: dict[str, int]) -> None:
        """单项落定：进度 +1 并广播全局帧 {phase:"progress", done, total, pct}。"""
        await self._db(store_runs.bump_progress, self.run_id, made=made)
        row = await self._db(store_runs.get_run, self.run_id, lock=False)
        pct = int(row.progress_done * 100 / row.progress_total) if row.progress_total else 0
        self.emit(
            {
                "phase": "progress",
                "done": row.progress_done,
                "total": row.progress_total,
                "pct": pct,
                "made": dict(self.made),
            }
        )

    async def run(self) -> str:
        """执行整个 run；返回终态 status。"""
        run_row = await self._db(store_runs.get_run, self.run_id, lock=False)
        ds = await self._db(store_datasources.get_datasource, run_row.datasource_id, lock=False)
        kinds = [k for k in run_row.kinds if k in LLM_KINDS]
        rescan = bool(run_row.options.get("rescan_existing"))
        use_samples = bool(run_row.options.get("use_sample_values")) and ds.mode == "online"

        # 项清单：scope 决定（relations = 全库表逐表过泳道；table = 单表；
        # datasource + options.table_names = 自选表子集）
        tables = await self._db(store_datasources.query_tables, ds.id, lock=False)
        if run_row.scope_type == "table":
            tables = [t for t in tables if t.id == run_row.scope_table_id]
        else:
            only_names = run_row.options.get("table_names")
            if only_names:
                keep = set(only_names)
                tables = [t for t in tables if t.name in keep]
        items = sorted((t.name for t in tables), key=str)
        total = len(items)
        await self._db(store_runs.mark_running, self.run_id, total)

        # 采样画像 query_fn（api 层注入工厂；离线/未开 → None）
        query_fn, close_eng = (
            self.query_fn_factory() if use_samples and self.query_fn_factory else (None, None)
        )

        try:
            if kinds and items:
                if self.chat_fn is None:
                    for t in items:
                        await self._item_failed(1, t, kinds[0], "未配置 AI 补全路由（route.ai）")
                else:
                    workers = await self._workers_count()
                    await self._run_lanes(ds, run_row, items, kinds, rescan, query_fn, workers)
            return await self._finish("cancelled" if self.cancel.is_set() else "done")
        except Exception:
            logger.warning("补全任务失败 run=%s", self.run_id, exc_info=True)
            return await self._finish("failed", "补全任务中断（详细信息见服务端日志）")
        finally:
            if close_eng:
                close_eng()

    async def _finish(self, status: str, error: str | None = None) -> str:
        await self._db(store_runs.finish_run, self.run_id, status, error)
        self.emit(
            {
                "phase": "run_end",
                "status": status,
                "error": error,
                "made": self.made,
                "lanes": dict(self.lanes),
            }
        )
        return status

    async def _workers_count(self) -> int:
        from app.store import settings as store_settings

        v = await self._db(store_settings.get_setting, "ai.completion_workers", lock=False)
        try:
            return max(1, min(int(v or 3), 6))
        except (TypeError, ValueError):
            return 3

    # -- LLM 泳道池 --
    async def _run_lanes(
        self,
        ds: Any,
        run_row: Any,
        items: list[str],
        llm_kinds: list[str],
        rescan: bool,
        query_fn: Any,
        workers: int,
    ) -> None:
        queue: asyncio.Queue[str] = asyncio.Queue()
        for t in items:
            queue.put_nowait(t)

        async def worker(lane: int) -> None:
            while not queue.empty():
                if self.cancel.is_set():
                    return
                table = await queue.get()
                await self._process_table(lane, ds, table, llm_kinds, rescan, query_fn)

        await asyncio.gather(*(worker(i + 1) for i in range(min(workers, len(items)))))

    async def _process_table(
        self, lane: int, ds: Any, table: str, llm_kinds: list[str], rescan: bool, query_fn: Any
    ) -> None:
        t0 = time.monotonic()
        self._lane(lane, table, "analyzing", f"分析 {table}…")
        try:
            pack = await self._evidence_pack(ds, table, llm_kinds, query_fn)
            cmt_cols = self._comment_cols(pack, rescan)
            bind_cols = self._std_bind_cols(pack, rescan)
            todo = (
                "relation" in llm_kinds  # 关系逐表必评（AI 语义裁决，产出可为空）
                or ("comment" in llm_kinds and (cmt_cols or pack["need_table_comment"]))
                or ("std_bind" in llm_kinds and bind_cols)
            )
            if not todo:
                await self._settle({})
                self._lane(lane, table, "skipped", "无缺失项，跳过")
                return
            made_here: dict[str, int] = {}
            for kind in llm_kinds:
                if self.cancel.is_set():
                    return
                made_here[kind] = await self._llm_kind(ds, table, kind, pack, rescan)
                self.made[kind] = self.made.get(kind, 0) + made_here[kind]
            await self._settle(made_here)
            ms = int((time.monotonic() - t0) * 1000)
            summary = " · ".join(f"{k}+{v}" for k, v in made_here.items() if v) or "无新增"
            self._lane(
                lane, table, "done", f"{table}：{summary}（{ms / 1000:.1f}s）", {"made": made_here, "ms": ms}
            )
        except Exception as exc:
            logger.info("补全单项失败 table=%s: %s", table, exc, exc_info=True)
            await self._item_failed(lane, table, llm_kinds[0], f"失败：{_safe_reason(exc)}")

    async def _item_failed(self, lane: int, table: str, kind: str, error: str) -> None:
        await self._db(store_runs.add_failed, self.run_id, {"table": table, "kind": kind, "error": error})
        row = await self._db(store_runs.get_run, self.run_id, lock=False)
        pct = int(row.progress_done * 100 / row.progress_total) if row.progress_total else 0
        self.emit(
            {
                "phase": "progress",
                "done": row.progress_done,
                "total": row.progress_total,
                "pct": pct,
                "made": dict(self.made),
            }
        )
        self._lane(lane, table, "failed", f"{table}：{error}", {"error": error})

    async def _evidence_pack(
        self, ds: Any, table: str, llm_kinds: list[str], query_fn: Any
    ) -> dict[str, Any]:
        """证据包组装（§4）：元数据 + 兄弟表 + 标准目录 + 采样画像。"""

        def _load(conn: sqlite3.Connection) -> tuple:
            t = store_datasources.get_table_by_name(conn, ds.id, table)
            cols = store_datasources.query_columns(conn, t.id) if t else []
            siblings = [x.name for x in store_datasources.query_tables(conn, ds.id) if x.name != table][
                :MAX_PROMPT_TABLES_SIBLINGS
            ]
            standards = []
            if "std_bind" in llm_kinds:
                rows = conn.execute(
                    "SELECT fs.name, fs.data_type, fs.unit, ds2.name AS dim"
                    " FROM field_standard fs LEFT JOIN dimension_standard ds2"
                    " ON fs.dimension_standard_id = ds2.id"
                ).fetchall()
                standards = [
                    {"name": r["name"], "data_type": r["data_type"], "unit": r["unit"], "dimension": r["dim"]}
                    for r in rows
                ]
            # relation 证据：全库表索引（主键/列清单，校验用）+ 已有关系对（确认+待审+已忽略）
            table_index: dict[str, dict[str, Any]] = {}
            existing_pairs: set[str] = set()
            if "relation" in llm_kinds:
                for tb in store_datasources.query_tables(conn, ds.id):
                    tcols = store_datasources.query_columns(conn, tb.id)
                    table_index[tb.name] = {
                        "comment": tb.comment_custom or tb.comment_db or "",
                        "pk": [c.name for c in tcols if c.is_pk],
                        "cols": {c.name for c in tcols},
                    }
                for r in conn.execute(
                    "SELECT from_table, from_col, to_table, to_col FROM relation WHERE datasource_id = ?",
                    (ds.id,),
                ).fetchall():
                    existing_pairs.add(f"{r['from_table']}.{r['from_col']}->{r['to_table']}.{r['to_col']}")
                for r in conn.execute(
                    "SELECT payload_json FROM ai_suggestion WHERE datasource_id = ?"
                    " AND kind = 'relation' AND status IN ('pending', 'ignored')",
                    (ds.id,),
                ).fetchall():
                    try:
                        p = json.loads(r["payload_json"])
                        if p.get("from") and p.get("to"):
                            existing_pairs.add(f"{p['from']}->{p['to']}")
                    except ValueError:
                        continue
            return t, cols, siblings, standards, table_index, existing_pairs

        t, cols, siblings, standards, table_index, existing_pairs = await self._db(_load, lock=False)
        if t is None:
            raise ValueError(f"表不存在: {table}")

        # 采样画像（开关门控；只对进入提示词的列采样）
        profile: dict[str, dict[str, Any]] = {}
        if query_fn is not None and cols:
            names = [c.name for c in cols][:MAX_COLS_PER_PROMPT]
            profile = await asyncio.to_thread(profile_table, query_fn, ds.dialect, table, names)

        # 列证据集：全量列（≤MAX），comment/std_bind 各自按需过滤（互不干扰）
        pack_cols = []
        for c in cols[:MAX_COLS_PER_PROMPT]:
            existing = c.comment_custom or c.comment_db
            pack_cols.append(
                {
                    "name": c.name,
                    "type": c.type,
                    "is_pk": c.is_pk,
                    "is_partition": c.is_partition,
                    "is_bucket": c.is_bucket,
                    "comment": existing,
                    "bound": bool(c.field_standard_id),
                    **(profile.get(c.name) or {}),
                }
            )
        need_table_comment = not (t.comment_custom or t.comment_db)
        # relation 提示词用紧凑行（表名｜注释｜主键）；排除当前表自身
        table_index_prompt = [
            f"{name}｜{info['comment'] or '—'}｜主键 {','.join(info['pk']) or '（无）'}"
            for name, info in list(table_index.items())[:MAX_PROMPT_TABLES_SIBLINGS]
            if name != table
        ]
        return {
            "table": table,
            "table_comment": t.comment_custom or t.comment_db,
            "need_table_comment": need_table_comment,
            "cols": pack_cols,
            "siblings": siblings,
            "standards": standards,
            "table_index": table_index,
            "table_index_prompt": table_index_prompt,
            "existing_pairs": existing_pairs,
        }

    @staticmethod
    def _comment_cols(pack: dict[str, Any], rescan: bool) -> list[dict[str, Any]]:
        """comment 目标列：缺注释列；rescan 时全量（决策点 3：默认跳过已有注释）。"""
        return [c for c in pack["cols"] if rescan or not c.get("comment")]

    @staticmethod
    def _std_bind_cols(pack: dict[str, Any], rescan: bool) -> list[dict[str, Any]]:
        """std_bind 目标列：未绑定列；rescan 时全量（已有绑定默认跳过）。"""
        return [c for c in pack["cols"] if rescan or not c.get("bound")]

    async def _llm_kind(self, ds: Any, table: str, kind: str, pack: dict[str, Any], rescan: bool) -> int:
        assert self.chat_fn is not None
        if kind == "comment":
            cols = self._comment_cols(pack, rescan)
            if not cols and not (pack["need_table_comment"] or rescan):
                return 0
            messages = build_comment_prompt(
                table,
                cols,
                pack["table_comment"] if not pack["need_table_comment"] else None,
                pack["siblings"],
            )
            raw = await self._chat_with_retry(messages)
            items = validate_comment_items(parse_json_array(raw), table, {c["name"] for c in cols})
            if not pack["need_table_comment"] and not rescan:
                items = [i for i in items if i["column"]]  # 表已有注释且未开重评：丢表级建议
        elif kind == "relation":
            if not pack["cols"] or not pack["table_index"]:
                return 0
            messages = build_relation_prompt(
                table,
                pack["table_comment"],
                pack["cols"],
                pack["table_index_prompt"],
                sorted(pack["existing_pairs"]),
            )
            raw = await self._chat_with_retry(messages)
            items = validate_relation_items(
                parse_json_array(raw),
                table,
                {c["name"] for c in pack["cols"]},
                pack["table_index"],
                pack["existing_pairs"],
            )
        else:  # std_bind
            cols = self._std_bind_cols(pack, rescan)
            if not cols or not pack["standards"]:
                return 0
            messages = build_std_bind_prompt(table, cols, pack["standards"])
            raw = await self._chat_with_retry(messages)
            items = validate_std_bind_items(
                parse_json_array(raw),
                table,
                {c["name"] for c in cols},
                {s["name"] for s in pack["standards"]},
            )
        return await self._save_items(ds.id, kind, items, rescan)

    async def _chat_with_retry(self, messages: list[dict[str, str]]) -> str:
        """LLM 调用 + 解析失败自我修正重试 1 次（§4）；思考模型预算占满自动翻倍重试。

        预算：setting ai.completion_max_tokens（默认 8192，思考模型需含推理 token）；
        finish=length（预算被思考内容占满）→ 同消息 2× 预算重试一次。
        """
        assert self.chat_fn is not None
        base = await self._db(self._chat_budget, lock=False)
        try:
            raw = await self.chat_fn("", messages, base)
        except BizError as exc:
            if "finish=length" not in exc.message:
                raise
            raw = await self.chat_fn("", messages, base * 2)
        if parse_json_array(raw):
            return raw
        retry = [
            *messages,
            {"role": "assistant", "content": raw[:800]},
            {
                "role": "user",
                "content": "上一条输出不是合法 JSON 数组。请只重新输出 JSON 数组，不要任何解释。",
            },
        ]
        return await self.chat_fn("", retry, base)

    @staticmethod
    def _chat_budget(conn: sqlite3.Connection) -> int:
        from app.store import settings as store_settings

        try:
            return max(1024, int(store_settings.get_setting(conn, "ai.completion_max_tokens") or 8192))
        except (TypeError, ValueError):
            return 8192

    async def _save_items(self, ds_id: int, kind: str, items: list[dict[str, Any]], rescan: bool) -> int:
        """第五关：判重 + 人工值跳过，落队（单写者收口）；payload 附 current 现值（§7 diff 评审）。"""

        def _save(conn: sqlite3.Connection) -> int:
            made = 0
            for s in items:
                conf = s.pop("confidence", 0.5)
                if kind == "relation":
                    # 关系对判重（确认 + 待审 + 已忽略，忽略不回潮）
                    if _relation_pair_known(conn, ds_id, s["from"], s["to"]):
                        continue
                    store_suggestions.save_suggestion(
                        conn,
                        datasource_id=ds_id,
                        kind=kind,
                        payload=s,
                        confidence=conf,
                        run_id=self.run_id,
                    )
                    made += 1
                    continue
                t = store_datasources.get_table_by_name(conn, ds_id, s["table"])
                if t is None:
                    continue
                if kind == "comment":
                    if s["column"]:
                        row = conn.execute(
                            "SELECT comment_db, comment_custom FROM column_meta"
                            " WHERE table_id = ? AND name = ?",
                            (t.id, s["column"]),
                        ).fetchone()
                        if row is None:
                            continue
                        if row["comment_custom"] and not rescan:
                            continue  # 人工注释永不覆盖
                        s["current"] = row["comment_custom"] or row["comment_db"] or ""
                    else:
                        if t.comment_custom and not rescan:
                            continue
                        s["current"] = t.comment_custom or t.comment_db or ""
                elif kind == "std_bind":
                    row = conn.execute(
                        "SELECT field_standard_id FROM column_meta WHERE table_id = ? AND name = ?",
                        (t.id, s["column"]),
                    ).fetchone()
                    if row is None:
                        continue
                    if row["field_standard_id"] and not rescan:
                        continue  # 已有绑定默认跳过
                    cur_name = None
                    if row["field_standard_id"]:
                        cur = conn.execute(
                            "SELECT name FROM field_standard WHERE id = ?", (row["field_standard_id"],)
                        ).fetchone()
                        cur_name = cur["name"] if cur else None
                    s["current"] = cur_name or "（未绑定）"
                if _exists_pending(conn, ds_id, kind, s):
                    continue
                store_suggestions.save_suggestion(
                    conn, datasource_id=ds_id, kind=kind, payload=s, confidence=conf, run_id=self.run_id
                )
                made += 1
            return made

        return await self._db(_save)
