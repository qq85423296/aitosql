/**
 * queryStore —— SSE 装配态 S1-S6（§11.2，D-20）：zustand 只存 SSE 装配态 + 少量门 optimistic UI 态。
 * REST 数据一律归 TanStack Query（V4）。
 *
 * 装配不变量：
 * - appliedSeq 按 query_id 维护已应用的最大 seq；seq 幂等去重（<= 丢弃）
 * - 乱序 fail-loud：seq > lastSeq+1 → 置 outOfOrder，由 useAssembly 触发 REST resync（E5 快照）
 * - 控制帧 stream_resumed 不入 appliedSeq（connect 层旁路）
 * - 非落库事件（P0-2 修复，contracts.md C3.2 冻结）：result 等大体积事件不入 query_event 表、
 *   帧不写 id 字段（无 seq）；applyEvent 对 seq 缺失/0 的事件旁路幂等直接应用——
 *   result 可从 result_preview 幂等重建，无重复风险
 */
import { create } from 'zustand';
import type { MessageModel, QueryStatus, SseEvent, StageView } from '../api/types';

export interface GateDecision {
  gate: 'clarify' | 'confirm';
  /** 单选兼容回显（旧契约） */
  choice?: string;
  /** clarify 多选回显（契约 4） */
  choices?: string[];
  /** clarify 自定义补充回显（契约 4） */
  custom?: string;
  approved?: boolean;
}

export interface QueryStoreState {
  /** 当前会话的消息装配态（按到达顺序） */
  messages: MessageModel[];
  /** query_id → 已应用最大 seq */
  appliedSeq: Record<string, number>;
  /** 断连续播横幅（仅增量场景亮，§11.2 五步-5） */
  resumedBanner: boolean;
  /** 崩溃恢复绿横幅（D03：已自动恢复，无手动续跑按钮） */
  recoveredBanner: boolean;
  /** 乱序标记（fail-loud → resync 进行中） */
  resyncing: boolean;
  /** 门裁决 optimistic 回显：query_id → 已裁决内容（契约 4 扩展 choices/custom） */
  gateDecisions: Record<string, GateDecision>;

  hydrate: (messages: MessageModel[], lastSeq: Record<string, number>) => void;
  reset: () => void;
  upsertQuestion: (m: MessageModel) => void;
  applyEvent: (queryId: string, ev: SseEvent, seq: number | null) => void;
  /** 契约 1：标记/解除排队态（queued 回执后当前轮终态自动开轮，followup_started 解除） */
  markQueued: (queryId: string, queued: boolean) => void;
  setResumedBanner: (v: boolean) => void;
  setRecoveredBanner: (v: boolean) => void;
  setResyncing: (v: boolean) => void;
  decideGate: (queryId: string, d: GateDecision) => void;
}

function patchMessage(
  messages: MessageModel[],
  queryId: string,
  patch: (m: MessageModel) => MessageModel,
): MessageModel[] {
  return messages.map((m) => (m.query_id === queryId ? patch(m) : m));
}

function pushStage(stages: StageView[], s: StageView): StageView[] {
  // stage 事件驱动 8 阶段枚举带 round：同 stage+round 更新，否则追加
  const idx = stages.findIndex((x) => x.stage === s.stage && x.round === s.round);
  if (idx >= 0) {
    const next = [...stages];
    next[idx] = { ...next[idx], ...s };
    return next;
  }
  return [...stages, s];
}

const TERMINAL: QueryStatus[] = ['done', 'failed', 'stopped'];

/**
 * followup_started 落位（契约 3）：排队壳 → running。
 * 首选精确匹配（C2 幂等：前端 client_request_id == 后端 query_id）；
 * 兜底取最早一条排队壳改挂新 query_id（后端重发 id 的防御路径）。
 */
function activateFollowup(messages: MessageModel[], newQid: string): MessageModel[] {
  if (messages.some((m) => m.query_id === newQid && m.queued)) {
    return patchMessage(messages, newQid, (m) => ({ ...m, queued: false, status: 'running' }));
  }
  const idx = messages.findIndex((m) => m.queued);
  if (idx < 0) return messages; // 防御：无排队壳（如页面刷新后历史事件回放）——仅记录 anchor 侧字段
  const next = [...messages];
  next[idx] = { ...next[idx], query_id: newQid, queued: false, status: 'running' };
  return next;
}

export const useQueryStore = create<QueryStoreState>((set, get) => ({
  messages: [],
  appliedSeq: {},
  resumedBanner: false,
  recoveredBanner: false,
  resyncing: false,
  gateDecisions: {},

  hydrate: (messages, lastSeq) =>
    set({
      messages,
      appliedSeq: { ...lastSeq },
      resumedBanner: false,
      recoveredBanner: false,
      resyncing: false,
      gateDecisions: {},
    }),

  reset: () =>
    set({
      messages: [],
      appliedSeq: {},
      resumedBanner: false,
      recoveredBanner: false,
      resyncing: false,
      gateDecisions: {},
    }),

  upsertQuestion: (m) =>
    set((s) =>
      s.messages.some((x) => x.query_id === m.query_id)
        ? { messages: patchMessage(s.messages, m.query_id, () => m) }
        : { messages: [...s.messages, m] },
    ),

  applyEvent: (queryId, ev, seq) => {
    const s = get();
    // 仅「落库事件」（seq ≥ 1）参与幂等/乱序裁决；非落库事件（result 等，无 id 帧 → seq null，
    // 或防御性兼容旧后端 id={qid}:0 → seq 0）旁路直接应用（P0-2）
    const persistedSeq = seq != null && seq >= 1 ? seq : null;
    if (persistedSeq != null) {
      const last = s.appliedSeq[queryId] ?? 0;
      if (persistedSeq <= last) return; // seq 幂等去重
      if (persistedSeq > last + 1) {
        // 乱序 fail-loud：触发 REST resync（由 useAssembly 监听 resyncing）
        set({ resyncing: true });
        return;
      }
      set({ appliedSeq: { ...s.appliedSeq, [queryId]: persistedSeq } });
    }
    set((st) => ({
      messages: (() => {
        const patched = patchMessage(st.messages, queryId, (m) => {
        switch (ev.type) {
          case 'stage':
            return { ...m, stages: pushStage(m.stages, { stage: ev.stage, round: ev.round, status: ev.status, elapsed_ms: ev.elapsed_ms }) };
          case 'gen_delta': {
            const prev = m.genPreview ?? { reasoning: '', content: '' };
            return {
              ...m,
              genPreview: ev.kind === 'reasoning' ? { ...prev, reasoning: ev.text } : { ...prev, content: ev.text },
            };
          }
          case 'contract': {
            if (ev.contract?.kind === 'meta') {
              // 库概况直通：reasoning 即回答正文 → 专用回答卡
              return { ...m, answer: ev.contract.reasoning, genPreview: undefined };
            }
            // 再生成（澄清裁决后重进 generate）：旧轮产物压入历史段，时间线不覆盖丢失
            const newSql = ev.contract?.sql;
            const archive = m.sql && newSql && newSql !== m.sql
              ? [...(m.history ?? []), { reasoning: m.reasoning, assumptions: m.assumptions, sql: m.sql }]
              : (m.history ?? []);
            // R3 思考消失 bug 修复（DSH 流式时序）：contract 到达时不再整槽清空 genPreview——
            // 1) genPreview.reasoning 就地转存 reasoningFull 兜底（round 归属：修正轮取 active correct.round，
            //    否则 generate=0）；reasoning_full 持久事件随后到达按 round upsert 覆盖（幂等替换，思考不消失）；
            // 2) content 槽用 contract.sql 替换（内部协议 JSON 原文不外显），避免清空闪烁。
            let reasoningFull = m.reasoningFull;
            const prev = m.genPreview;
            if (prev?.reasoning) {
              const activeStage = [...m.stages].reverse().find((s) => s.status === 'start');
              const round = activeStage?.stage === 'correct' ? activeStage.round : 0;
              if (!reasoningFull?.some((r) => r.round === round)) {
                reasoningFull = [...(reasoningFull ?? []), { round, text: prev.reasoning }].sort(
                  (a, b) => a.round - b.round,
                );
              }
            }
            return {
              ...m,
              history: archive,
              reasoning: ev.contract?.reasoning ?? m.reasoning,
              assumptions: ev.contract?.assumptions ?? m.assumptions,
              sql: ev.contract?.sql ?? m.sql,
              reasoningFull,
              genPreview: prev ? { ...prev, content: ev.contract?.sql ?? prev.content } : undefined,
            };
          }
          case 'sql':
            // R2：按 round 记录各轮 SQL（轨迹「修正第N轮」差异概览用；msg.sql 保持最新轮）
            return { ...m, sql: ev.sql, sqlRounds: { ...(m.sqlRounds ?? {}), [ev.round]: ev.sql } };
          case 'reasoning_full': {
            // R2：完整思考全文按 round upsert（同 round 重发覆盖，幂等）
            // 防御：历史事件可能被 16KB 通用截断标记（truncated/preview）——回退 preview 并标注
            const rawText = ev.text ?? (ev.truncated ? `${ev.preview ?? ''}\n…（早期事件被截断）` : '');
            const list = [...(m.reasoningFull ?? [])];
            const ridx = list.findIndex((r) => r.round === ev.round);
            if (ridx >= 0) list[ridx] = { round: ev.round, text: rawText };
            else list.push({ round: ev.round, text: rawText });
            list.sort((a, b) => a.round - b.round);
            return { ...m, reasoningFull: list };
          }
          case 'risk':
            return { ...m, risks: ev.risks };
          case 'clarify_required':
            return { ...m, clarify_options: ev.options, status: 'waiting_clarify' };
          case 'confirm_required':
            return { ...m, risks: ev.risks, est_rows: ev.est_rows, status: 'waiting_confirm' };
          case 'correction':
            return {
              ...m,
              corrections: [...m.corrections, { round: ev.round, sql: ev.sql, error: ev.error, fingerprint: ev.fingerprint }],
            };
          case 'meta':
            return { ...m, meta: { hit_tables: ev.hit_tables, route: ev.route, tokens: ev.tokens } };
          case 'result_meta':
            return { ...m, resultMeta: { rows: ev.rows, scan_rows: ev.scan_rows, duration_ms: ev.duration_ms, data_max_time: ev.data_max_time, truncated: ev.truncated } };
          case 'result':
            return { ...m, result: { query_id: queryId, columns: ev.columns, rows: ev.rows, truncated: m.resultMeta?.truncated ?? false } };
          case 'verify':
            return { ...m, verify: { checks: ev.checks, passed: ev.passed, fallback_used: ev.fallback_used } };
          case 'followups':
            return { ...m, followups: ev.items };
          // 契约 3 增量事件：
          case 'llm_retry':
            return {
              ...m,
              retries: [...(m.retries ?? []), { attempt: ev.attempt, delay_ms: ev.delay_ms, failure_class: ev.failure_class, route: ev.route }],
            };
          case 'inbox_claimed':
            return { ...m, inboxNotes: [...(m.inboxNotes ?? []), ...(ev.items ?? [])] };
          case 'followup_started':
            return { ...m, followup_started: ev.query_id };
          case 'stopped':
            // DSH-I 验证：停止仅落终态，**不清空 genPreview/answer/sql**——
            // 已流式渲染的思考与生成输出在停止后保持可见（StageCard 折叠但不丢内容）
            return { ...m, status: 'stopped', stopped: ev.reason };
          case 'done':
            // F8：三态映射——历史回放/契约演进若带 stopped 帧，不再误显「已失败」
            return { ...m, status: ev.status === 'done' ? 'done' : ev.status === 'stopped' ? 'stopped' : 'failed', unverified: ev.unverified };
          case 'error':
            return { ...m, status: 'failed', genPreview: undefined, error: { code: ev.code, message: ev.message, retryable: ev.retryable } };
          default:
            return m;
        }
        });
        // followup_started：anchor 消息记录之外，把排队壳解除 queued 置 running（流切换由 useAssembly 挂接）
        return ev.type === 'followup_started' ? activateFollowup(patched, ev.query_id) : patched;
      })(),
    }));
  },

  setResumedBanner: (v) => set({ resumedBanner: v }),
  setRecoveredBanner: (v) => set({ recoveredBanner: v }),
  setResyncing: (v) => set({ resyncing: v }),
  markQueued: (queryId, queued) =>
    set((s) => ({ messages: patchMessage(s.messages, queryId, (m) => ({ ...m, queued })) })),
  decideGate: (queryId, d) =>
    set((s) => {
      const gateDecisions = { ...s.gateDecisions, [queryId]: d };
      const messages = patchMessage(s.messages, queryId, (m) => {
        if (d.gate === 'clarify') {
          // 契约 4 多选+自定义：clarify_choice 合成串回显（单选场景即 choices[0]，向后兼容）
          const parts = [...(d.choices ?? (d.choice ? [d.choice] : [])), ...(d.custom ? [d.custom] : [])];
          return { ...m, clarify_choice: parts.length ? parts.join('；') : (d.choice ?? null), status: 'running' };
        }
        return {
          ...m,
          status: d.approved ? 'running' : 'stopped',
          stopped: d.approved ? m.stopped : 'user_cancel',
        };
      });
      return { gateDecisions, messages };
    }),
}));

export function isTerminal(status: QueryStatus): boolean {
  return TERMINAL.includes(status);
}
