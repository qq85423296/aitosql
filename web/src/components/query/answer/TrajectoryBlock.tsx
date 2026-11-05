import { useEffect, useMemo, useRef, useState } from 'react';
import { clsx } from 'clsx';
import type { MessageModel, StageName } from '../../../api/types';
import { fmtDuration, fmtInt, fmtRows } from '../../../lib/format';
import { isTerminal } from '../../../stores/queryStore';

/**
 * TrajectoryBlock（DSH 对齐 R2+R3：结构化轨迹时间线 = DSH「过程组」）：
 *
 * DOM 铁律（DSH 轮次结构）：[用户气泡] → [本过程组] → [最终答案 SQL卡/结果表/假设chips] → [TurnFooter]。
 * 本组件是「过程组」，永远在最终答案上方；答案由 Stream 在本组件下方渲染，任何模式不隐藏。
 *
 * 流式时序（R3）：
 * - gen_delta kind=reasoning 流动时：过程组里只有「思考」条目逐字生长（光标+自动锚底）；
 *   后续阶段条目由各自 stage 事件驱动、不提前渲染；SQL 卡由 Stream 侧按 sql 事件门控。
 * - gen_delta kind=content（契约协议 JSON 原文）：不外显——「生成 SQL」条目 shimmer「正在生成 SQL…」，
 *   sql 事件落库（sqlRounds）后才展示该轮 SQL 文本。
 * - 修正轮（stage='correct'）实时思考归入「思考（修正 rN）」条目；correction 事件未到达前先渲染 live 占位。
 *
 * 折叠语义（DSH 工作步骤展示模式）：
 * - 运行中/等待裁决：组头「进行中 · {当前阶段} / 等待裁决…」（+停止按钮），组默认展开；
 *   进行中的条目自动展开，其余条目默认收起、点击展开。
 * - 完成轮次：默认收起为单行组头「已完成分析，用时 X」（点击展开看全部过程行，再点收起）；
 *   进入终态时清除用户手动展开态，保证自动收拢。
 * - variant=compact（折叠四档「摘要」）：每条一行（图标+标题+耗时+摘要），不可展开。
 */

const STAGE_LABEL: Record<StageName, string> = {
  supply: '信封组装',
  generate: '生成',
  validate: '校验',
  clarify: '澄清',
  confirm: '确认',
  execute: '执行',
  verify: '结果校验',
  correct: '修正',
};

interface TrajEntry {
  key: string;
  icon: string;
  title: string;
  /** 耗时（ms）；undefined = 不显示 */
  elapsedMs?: number | null;
  /** 一行摘要 */
  summary?: string;
  tone?: 'dim' | 'ok' | 'warn' | 'err' | 'active';
  /** 进行中（accent + 光标；该条目默认自动展开） */
  live?: boolean;
  /** 展开详情；缺省 = 单行小条（不可展开） */
  detail?: React.ReactNode;
}

/** 修正轮差异概览（简单文字描述，不做真 diff）：行级集合对比 */
/** 思考正文渲染上限（字符）：超长思考展开时截断渲染防页面卡死（全文仍在库里，复制不受影响） */
const THINK_RENDER_CAP = 100_000;

function renderCapped(text: string): string {
  return text.length > THINK_RENDER_CAP
    ? `${text.slice(0, THINK_RENDER_CAP)}\n…（思考过长，界面只渲染前 ${THINK_RENDER_CAP / 10000} 万字符防卡顿）`
    : text;
}

function diffOverview(prev: string | undefined, next: string): string {
  if (!prev) return '基于首轮生成修正';
  if (prev === next) return '与上一轮 SQL 相同（仅重跑）';
  const aLines = prev.split('\n').map((l) => l.trim()).filter(Boolean);
  const bLines = next.split('\n').map((l) => l.trim()).filter(Boolean);
  const aSet = new Set(aLines);
  const bSet = new Set(bLines);
  const added = bLines.filter((l) => !aSet.has(l)).length;
  const removed = aLines.filter((l) => !bSet.has(l)).length;
  const lenDelta = next.length - prev.length;
  return `较上一轮：+${added}/-${removed} 行（${aLines.length}→${bLines.length} 行，字符 ${lenDelta >= 0 ? '+' : ''}${lenDelta}）`;
}

export function TrajectoryBlock({
  msg,
  running,
  variant = 'full',
  onStop,
}: {
  msg: MessageModel;
  running: boolean;
  /** full=过程组（可整组收展，条目逐条点击展开）；compact=每条一行（折叠四档「摘要」） */
  variant?: 'full' | 'compact';
  onStop?: () => void;
}) {
  const stages = msg.stages;
  const totalMs = useMemo(
    // F10 同口径：成功终态优先 result_meta.duration_ms（后端权威计时）
    () =>
      msg.status === 'done' && msg.resultMeta?.duration_ms != null
        ? msg.resultMeta.duration_ms
        : stages.reduce((acc, s) => acc + (s.elapsed_ms ?? 0), 0),
    [stages, msg.status, msg.resultMeta?.duration_ms],
  );
  const active = [...stages].reverse().find((s) => s.status === 'start');
  const waiting = msg.status === 'waiting_clarify' || msg.status === 'waiting_confirm';
  const terminal = isTerminal(msg.status);

  // R3 折叠语义：运行中/等待裁决默认展开；终态默认收起为单行组头；用户点击覆盖
  const [openOverride, setOpenOverride] = useState<boolean | null>(null);
  // 条目展开态：默认收起，live 条目自动展开；toggled 记录用户逐条点击（翻转默认态）
  const [toggled, setToggled] = useState<Set<string>>(new Set());
  const thinkRef = useRef<HTMLDivElement>(null);

  // 进入终态 → 清除手动展开覆盖，自动收拢为组头（DSH 默认态）
  useEffect(() => {
    if (terminal) setOpenOverride(null);
  }, [terminal]);

  const groupOpen = openOverride ?? (running || waiting);

  const preview = msg.genPreview;
  // 思考实时流自动锚底
  useEffect(() => {
    const el = thinkRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [preview?.reasoning]);

  const stageElapsed = (stage: StageName, round?: number): number | null => {
    const hit = stages.find((s) => s.stage === stage && (round == null || s.round === round) && s.status === 'done');
    return hit?.elapsed_ms ?? null;
  };
  const isActive = (stage: StageName, round?: number) =>
    !!running && !!active && active.stage === stage && (round == null || active.round === round);

  // R3 流式时序：SQL 文本只在 sql 事件落库（sqlRounds）后展示；历史/非运行场景回退 msg.sql（contract/sql_final）
  const sqlFromEvent = (rounds: number[]): string | undefined => {
    for (const r of rounds) {
      const v = msg.sqlRounds?.[r];
      if (v) return v;
    }
    return undefined;
  };

  const entries = useMemo((): TrajEntry[] => {
    const list: TrajEntry[] = [];
    const has = (st: StageName) => stages.some((s) => s.stage === st);

    // 1. 信封组装
    if (has('supply')) {
      list.push({
        key: 'supply',
        icon: '📥',
        title: STAGE_LABEL.supply,
        elapsedMs: stageElapsed('supply'),
        live: isActive('supply'),
        summary: msg.meta?.hit_tables?.length ? `命中 ${msg.meta.hit_tables.length} 表` : undefined,
        detail: (
          <div className="traj-kv">
            命中表：{msg.meta?.hit_tables?.length ? msg.meta.hit_tables.join('、') : '（无记录）'}
          </div>
        ),
      });
    }

    // 2. 思考（generate 轮 round=0：reasoning_full 全文；contract 兜底转存；运行中 genPreview 实时流）
    const think0 = msg.reasoningFull?.find((r) => r.round === 0)?.text;
    const liveThink = isActive('generate') && !!preview?.reasoning && !think0;
    if (think0 || liveThink) {
      const text = think0 ?? preview?.reasoning ?? '';
      list.push({
        key: 'think-0',
        icon: '💭',
        title: liveThink ? '思考中' : '思考',
        elapsedMs: stageElapsed('generate'),
        live: liveThink,
        summary: liveThink
          ? `…${text.slice(-48).trimStart()}`
          : text.split('\n', 1)[0]?.slice(0, 48) || undefined,
        detail: (
          <div className="traj-mono traj-scroll" ref={liveThink ? thinkRef : undefined}>
            {renderCapped(text)}
            {liveThink && <span className="gen-cursor" aria-hidden />}
          </div>
        ),
      });
    }

    // 3. 生成 SQL（R3：content 协议 JSON 不外显——进行中 shimmer「正在生成 SQL…」；sql 事件后才展示文本）
    const genSql = sqlFromEvent([0, 1]) ?? (!running && !waiting ? (msg.sql ?? undefined) : undefined);
    const liveGen = isActive('generate');
    if (genSql || has('generate') || liveGen) {
      list.push({
        key: 'gen-sql',
        icon: '🧾',
        title: '生成 SQL',
        elapsedMs: stageElapsed('generate'),
        live: liveGen && !genSql,
        summary:
          liveGen && !genSql ? undefined : msg.reasoning?.split('\n', 1)[0]?.slice(0, 60) || undefined,
        detail: genSql ? (
          <div>
            {msg.assumptions.length > 0 && (
              <div className="traj-kv">假设：{msg.assumptions.map((a) => a.text).join('；')}</div>
            )}
            <pre className="traj-pre">{genSql}</pre>
          </div>
        ) : liveGen ? (
          <div className="traj-shimmer">正在生成 SQL…</div>
        ) : undefined,
      });
    }

    // 3.5 llm_retry 小条（模型调用退避重试，契约 3）
    for (const [i, r] of (msg.retries ?? []).entries()) {
      list.push({
        key: `retry-${i}`,
        icon: '↻',
        title: `模型重试 第 ${r.attempt} 次（${r.failure_class}，${fmtDuration(r.delay_ms)}后${r.route ? ` · ${r.route}` : ''}）`,
        tone: 'dim',
      });
    }

    // 4. 校验（risk 命中列表，级别色点）
    if (has('validate') || msg.risks.length > 0) {
      list.push({
        key: 'validate',
        icon: '🛡',
        title: STAGE_LABEL.validate,
        elapsedMs: stageElapsed('validate'),
        live: isActive('validate'),
        tone: msg.risks.some((r) => r.level === 'red') ? 'err' : msg.risks.length ? 'warn' : 'ok',
        summary: msg.risks.length ? `${msg.risks.length} 条风险命中` : '通过',
        detail: (
          <div>
            {msg.risks.length === 0 && <div className="traj-kv">无风险规则命中</div>}
            {msg.risks.map((r, i) => (
              <div className="traj-check" key={i}>
                <span className={clsx('risk-dot', r.level)} aria-hidden />
                <span>
                  {r.rule_id} · {r.message}
                </span>
              </div>
            ))}
          </div>
        ),
      });
    }

    // 5. 澄清门
    if (has('clarify') || msg.clarify_options) {
      const waitC = msg.status === 'waiting_clarify';
      list.push({
        key: 'clarify',
        icon: '❓',
        title: STAGE_LABEL.clarify,
        elapsedMs: stageElapsed('clarify'),
        live: isActive('clarify'),
        tone: waitC ? 'active' : 'dim',
        summary: waitC ? '等待裁决' : msg.clarify_choice ? `已选：${msg.clarify_choice}` : undefined,
        detail: (
          <div className="traj-kv">
            {(msg.clarify_options ?? []).map((o, i) => (
              <div key={i}>
                {o.label}（{o.expr}）
              </div>
            ))}
            {msg.clarify_choice && <div>裁决：{msg.clarify_choice}</div>}
          </div>
        ),
      });
    }

    // 6. 确认门
    if (has('confirm') || msg.est_rows != null) {
      const waitF = msg.status === 'waiting_confirm';
      list.push({
        key: 'confirm',
        icon: '⚠️',
        title: STAGE_LABEL.confirm,
        elapsedMs: stageElapsed('confirm'),
        live: isActive('confirm'),
        tone: waitF ? 'warn' : 'dim',
        summary: waitF ? '等待确认' : msg.est_rows != null ? `预估 ${fmtRows(msg.est_rows)}` : undefined,
        detail: (
          <div className="traj-kv">
            {msg.est_rows != null && <div>预估行数：{fmtRows(msg.est_rows)}</div>}
            状态：{waitF ? '等待用户确认' : msg.stopped === 'user_cancel' ? '用户取消' : '已确认放行'}
          </div>
        ),
      });
    }

    // 7. 执行
    if (has('execute') || msg.resultMeta) {
      const rm = msg.resultMeta;
      list.push({
        key: 'execute',
        icon: '▶️',
        title: STAGE_LABEL.execute,
        elapsedMs: stageElapsed('execute') ?? rm?.duration_ms ?? null,
        live: isActive('execute'),
        summary: rm ? `${fmtRows(rm.rows)} · ${fmtDuration(rm.duration_ms)}` : undefined,
        detail: rm ? (
          <div className="traj-kv">
            <div>行数：{fmtInt(rm.rows)}（{rm.truncated ? 'LIMIT 截断' : '未截断'}）</div>
            <div>扫描：{fmtInt(rm.scan_rows)} 行 · 耗时 {fmtDuration(rm.duration_ms)}</div>
            {rm.data_max_time && <div>数据截至：{rm.data_max_time}</div>}
          </div>
        ) : undefined,
      });
    }

    // 8. 结果校验（抽样对比结论）
    if (has('verify') || msg.verify) {
      const v = msg.verify;
      list.push({
        key: 'verify',
        icon: '✅',
        title: STAGE_LABEL.verify,
        elapsedMs: stageElapsed('verify'),
        live: isActive('verify'),
        tone: v ? (v.passed ? 'ok' : 'warn') : 'dim',
        summary: v ? (v.passed ? '通过' : '未通过') + (v.fallback_used ? ' · 已降级' : '') : undefined,
        detail: v ? (
          <div>
            {v.checks.map((c, i) => (
              <div className="traj-check" key={i}>
                <span aria-hidden>{c.passed ? '✓' : '✗'}</span>
                <span>{c.name}</span>
              </div>
            ))}
            {v.fallback_used && <div className="traj-kv">抽样行数不足，已降级为规则校验</div>}
          </div>
        ) : undefined,
      });
    }

    // 9. 修正轮（每轮：思考 rN 全文 + 修正条目：原因 + 差异概览 + 该轮 SQL + 指纹）
    msg.corrections.forEach((c, i) => {
      const roundThink = msg.reasoningFull?.find((r) => r.round === c.round)?.text;
      const liveFixThink = isActive('correct', c.round) && !!preview?.reasoning && !roundThink;
      if (roundThink || liveFixThink) {
        const text = roundThink ?? preview?.reasoning ?? '';
        list.push({
          key: `think-${c.round}`,
          icon: '💭',
          title: liveFixThink ? `思考（修正 r${c.round}）中` : `思考（修正 r${c.round}）`,
          elapsedMs: stageElapsed('correct', c.round),
          live: liveFixThink,
          summary: liveFixThink
            ? `…${text.slice(-48).trimStart()}`
            : text.split('\n', 1)[0]?.slice(0, 48) || undefined,
          detail: (
            <div className="traj-mono traj-scroll" ref={liveFixThink ? thinkRef : undefined}>
              {renderCapped(text)}
              {liveFixThink && <span className="gen-cursor" aria-hidden />}
            </div>
          ),
        });
      }
      const prevSql = i === 0 ? sqlFromEvent([0, 1]) : msg.corrections[i - 1]?.sql;
      const thisSql = sqlFromEvent([c.round]) ?? c.sql;
      list.push({
        key: `correct-${c.round}`,
        icon: '🔧',
        title: `修正 第${c.round}轮`,
        elapsedMs: stageElapsed('correct', c.round),
        live: isActive('correct', c.round),
        tone: 'warn',
        summary: c.error?.split('\n', 1)[0]?.slice(0, 60) || undefined,
        detail: (
          <div>
            {c.error && <div className="traj-kv">修正原因：{c.error}</div>}
            <div className="traj-kv">{diffOverview(prevSql, thisSql)}</div>
            <pre className="traj-pre">{thisSql}</pre>
            <div className="traj-kv">指纹 {c.fingerprint}</div>
          </div>
        ),
      });
    });

    // 9.4 修正轮 live 占位：correction 事件未到达前（思考/重写 SQL 流式中）也渲染本轮条目
    if (running && active?.stage === 'correct' && !msg.corrections.some((c) => c.round === active.round)) {
      if (preview?.reasoning) {
        const text = preview.reasoning;
        list.push({
          key: `think-live-${active.round}`,
          icon: '💭',
          title: `思考（修正 r${active.round}）中`,
          live: true,
          summary: `…${text.slice(-48).trimStart()}`,
          detail: (
            <div className="traj-mono traj-scroll" ref={thinkRef}>
              {renderCapped(text)}
              <span className="gen-cursor" aria-hidden />
            </div>
          ),
        });
      }
      list.push({
        key: `correct-live-${active.round}`,
        icon: '🔧',
        title: `修正 第${active.round}轮`,
        live: true,
        tone: 'warn',
        detail: <div className="traj-shimmer">正在修正 SQL…</div>,
      });
    }

    // 9.5 inbox_claimed 小条（运行中输入被领取生效，契约 3）
    for (const [i, n] of (msg.inboxNotes ?? []).entries()) {
      list.push({
        key: `inbox-${i}`,
        icon: '⤵',
        title: `已并入${n.kind === 'inject' ? '注入' : '引导'}：${n.content}`,
        tone: 'dim',
      });
    }

    // 10. 终态
    if (terminal) {
      list.push({
        key: 'terminal',
        icon: '●',
        title:
          msg.status === 'done' ? '完成' : msg.status === 'failed' ? '失败' : '已停止',
        elapsedMs: totalMs,
        tone: msg.status === 'done' ? 'ok' : msg.status === 'failed' ? 'err' : 'dim',
        summary: `用时 ${fmtDuration(totalMs)}`,
        detail: msg.error ? (
          <div className="traj-kv">
            {msg.error.code}：{msg.error.message}
            {msg.error.retryable ? '（可重试）' : ''}
          </div>
        ) : undefined,
      });
    }

    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [msg, running, waiting, active, totalMs]);

  const toggleEntry = (key: string) =>
    setToggled((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  /** 条目展开判定：live 默认展开，其余默认收起；用户点击翻转默认态 */
  const entryOpen = (e: TrajEntry): boolean => !!e.detail && (!!e.live !== toggled.has(e.key));

  if (!entries.length && !running) return null;

  const headerTitle = running
    ? `进行中 · ${active ? STAGE_LABEL[active.stage] : '准备中'}…`
    : msg.status === 'waiting_clarify'
      ? '等待澄清裁决…'
      : msg.status === 'waiting_confirm'
        ? '等待执行确认…'
        : msg.status === 'failed'
          ? '已失败'
          : msg.status === 'stopped'
            ? '已停止'
            : '已完成'; // DSH 组头：「已完成，用时 X」（用时在组头右侧 tree-elapsed）

  // compact（折叠四档「摘要」）：每条一行（图标+标题+耗时+摘要），不可展开
  if (variant === 'compact') {
    return (
      <div className="tree-card traj-compact">
        <div className="tree-hd traj-static-hd">
          <span className={clsx('tree-title', terminal && (msg.status === 'failed' ? 'failed' : 'done'))}>
            {headerTitle}
          </span>
          <span className="tree-elapsed">{fmtDuration(totalMs)}</span>
        </div>
        <div className="tree-bd">
          {entries.map((e) => (
            <div className="tree-row" key={e.key}>
              <span className="tree-node traj-line">
                <span className="traj-ico" aria-hidden>{e.icon}</span>
                <span className={clsx('tree-label', e.tone === 'dim' && 'dim', e.live && 'active')}>
                  {e.title}
                  {e.live && <span className="gen-cursor" aria-hidden />}
                </span>
                {e.summary && <span className="traj-summary">{e.summary}</span>}
                <span className="tree-elapsed">{e.elapsedMs != null ? fmtDuration(e.elapsedMs) : ''}</span>
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // full（DSH 过程组）：运行中展开 / 终态默认收起为单行组头，点击整组收展；条目逐条点击展开
  return (
    <div className="tree-card traj-card" aria-live="polite">
      <button
        className="tree-hd"
        data-action="折叠展开过程组"
        aria-expanded={groupOpen}
        onClick={() => setOpenOverride(!groupOpen)}
      >
        <span className={clsx('tree-chev', groupOpen && 'open')} aria-hidden>›</span>
        <span className={clsx('tree-title', terminal && (msg.status === 'failed' ? 'failed' : 'done'))}>
          {headerTitle}
        </span>
        <span className="tree-elapsed">{fmtDuration(totalMs)}</span>
        {running && onStop && (
          <span
            className="tree-stop"
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              onStop();
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.stopPropagation();
                onStop();
              }
            }}
          >
            ■ 停止（Esc）
          </span>
        )}
      </button>
      {groupOpen && (
        <div className="tree-bd">
          {entries.map((e) => {
            const open = entryOpen(e);
            return (
              <div className="tree-row" key={e.key}>
                <button
                  className="tree-node"
                  onClick={() => e.detail && toggleEntry(e.key)}
                  disabled={!e.detail}
                  aria-expanded={e.detail ? open : undefined}
                >
                  <span className={clsx('tree-chev', open && 'open', !e.detail && 'leaf')}>›</span>
                  <span className="traj-ico" aria-hidden>{e.icon}</span>
                  <span
                    className={clsx(
                      'tree-label',
                      (e.tone === 'dim' || !e.tone) && 'dim',
                      e.tone === 'ok' && 'traj-ok',
                      e.tone === 'warn' && 'risk-yellow',
                      e.tone === 'err' && 'risk-red',
                      e.live && 'active',
                    )}
                  >
                    {e.title}
                    {e.live && <span className="gen-cursor" aria-hidden />}
                  </span>
                  {e.summary && <span className="traj-summary">{e.summary}</span>}
                  <span className="tree-elapsed">{e.elapsedMs != null ? fmtDuration(e.elapsedMs) : ''}</span>
                </button>
                {open && e.detail && <div className="tree-body">{e.detail}</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
