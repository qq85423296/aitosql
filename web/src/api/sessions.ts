/** C1.1 会话 */
import { api } from './client';
import type { MessageModel, QueryStatus, Session, SessionSnapshot, SnapshotItem } from './types';

/**
 * E5 快照项 → 装配态 MessageModel（D-21 步骤 1 hydrate；P0-1/P1-1 修复）。
 * 映射规则（contracts.md C1.1-E5 冻结）：
 * - reasoning/assumptions/sql 取自 item.contract（C4.1 生成契约 JSON）；sql 兜底 sql_final
 * - item.preview → msg.result（历史结果表渲染来源）；preview.truncated → resultMeta.truncated
 * - resultMeta 由持久化标量重建（rows = preview 行数或 0；scan_rows/duration_ms/data_max_time 来自 query 行）
 */
export function mapSnapshotItem(item: SnapshotItem, sessionId: string): MessageModel {
  const preview = item.preview ?? null;
  const isMeta = item.contract?.kind === 'meta'; // 库概况直通：历史回放同映射（回答卡）
  const proc = item.process ?? null; // 过程回放（query_event 重建）
  // 契约 1 缺口防御：后端若把排队轮持久化为 status='queued'（QueryStatus 枚举外），
  // 快照回放时映射为 running + queued 标记（渲染排队小条、不挂流，等 followup_started）
  const rawStatus = item.status as QueryStatus | 'queued';
  const wasQueued = rawStatus === 'queued';
  return {
    query_id: item.query_id,
    session_id: sessionId,
    question: item.question,
    status: wasQueued ? 'running' : rawStatus,
    queued: wasQueued || undefined,
    dialect: item.dialect,
    stages: proc?.stages ?? [],
    answer: isMeta ? item.contract?.reasoning : undefined,
    reasoning: isMeta ? undefined : item.contract?.reasoning,
    assumptions: item.contract?.assumptions ?? [],
    sql: isMeta ? undefined : (item.contract?.sql ?? item.sql_final ?? undefined),
    risks: proc?.risks ?? [],
    clarify_options: proc?.clarify_options ?? undefined,
    clarify_choice: item.clarify_choice ?? null,
    corrections: proc?.corrections ?? [],
    verify: proc?.verify ?? undefined,
    error: proc?.error ?? undefined,
    // R2：完整思考全文回放（reasoning_full 持久事件重建；旧快照无此字段，防御可选链）
    reasoningFull: proc?.reasoning_full ?? undefined,
    // R2 收尾：逐轮 SQL 回放（key 为字符串数字 → 转 Number；轨迹修正差异概览与 live 一致）
    sqlRounds: proc?.sql_rounds
      ? Object.fromEntries(Object.entries(proc.sql_rounds).map(([k, v]) => [Number(k), v]))
      : undefined,
    // R2 收尾：llm_retry / inbox_claimed 回放（轨迹小条）
    retries: proc?.retries ?? undefined,
    inboxNotes: proc?.inbox_notes ?? undefined,
    resultMeta:
      preview || item.duration_ms != null || item.scan_rows != null
        ? {
            rows: preview?.rows.length ?? 0,
            scan_rows: item.scan_rows ?? 0,
            duration_ms: item.duration_ms ?? 0,
            data_max_time: item.data_max_time ?? '',
            truncated: preview?.truncated ?? false,
          }
        : undefined,
    result: preview
      ? { query_id: item.query_id, columns: preview.columns, rows: preview.rows, truncated: preview.truncated }
      : null,
    followups: item.contract?.followups ?? [],
    // R2 收尾：信封命中表回放（proc.hit_tables = meta 事件最后一条非空值）；tokens 缺省回退 0
    meta:
      item.tokens != null || (proc?.hit_tables?.length ?? 0) > 0
        ? { hit_tables: proc?.hit_tables ?? [], tokens: item.tokens ?? 0 }
        : undefined,
    unverified: item.result_unverified,
    created_at: item.created_at,
  };
}

export const sessionsApi = {
  list: () => api.get<Session[]>('/sessions'),
  create: (body: { title?: string; datasource_id?: number | null }) =>
    api.post<Session>('/sessions', body),
  patch: (id: string, body: { title?: string; pinned?: boolean; archived?: boolean }) =>
    api.patch<Session>(`/sessions/${id}`, body),
  remove: (id: string) => api.del<void>(`/sessions/${id}`),
  /** E5 消息快照（C1.1-E5 冻结包络）：{session, items, last_seq_by_query} */
  snapshot: (id: string) => api.get<SessionSnapshot>(`/sessions/${id}/queries`),
};
