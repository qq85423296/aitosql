/**
 * useAssembly —— 装配五步（D-21 / SSOT §11.2 评审项 32 终稿）：
 * 1. E5 快照 hydrate：进会话先拉 GET /sessions/:id/queries 渲染历史
 * 2. running/waiting_* 的查询挂 SSE
 * 3. 双模重连归一：Last-Event-ID 增量 / 无头全量 seq=1 起（useQueryStream 承载）
 * 4. seq 幂等去重（queryStore.applyEvent）；乱序 fail-loud → REST resync（本 hook 监听并执行）
 * 5. 横幅策略：增量「已从断点续播」（stream_resumed 控制帧驱动）/ 崩溃恢复「已自动恢复」（D03，无手动续跑按钮）
 */
import { useCallback, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { sessionsApi, mapSnapshotItem } from '../api/sessions';
import { mockEnabled } from '../api/client';
import { useQueryStore } from '../stores/queryStore';
import { attachStream } from './useQueryStream';
import { mockRunPipeline } from '../api/mockPipeline';

export function useAssembly(sessionId: string | null) {
  const qc = useQueryClient();
  const streamsRef = useRef<Map<string, { stop: () => void }>>(new Map());
  const resyncing = useQueryStore((s) => s.resyncing);

  const stopAll = useCallback(() => {
    streamsRef.current.forEach((h) => h.stop());
    streamsRef.current.clear();
  }, []);

  /** 步骤 1+2：hydrate + 挂流 */
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    const store = useQueryStore.getState();
    store.reset();

    (async () => {
      const snap = await sessionsApi.snapshot(sessionId);
      if (cancelled) return;
      // E5 冻结包络（C1.1-E5）：items 平铺 + last_seq_by_query 顶层字典；preview 随映射入 result（P1-1）
      const messages = snap.items.map((it) => mapSnapshotItem(it, sessionId));
      useQueryStore.getState().hydrate(messages, snap.last_seq_by_query);
      // 崩溃恢复检测（D03/R-3）：快照项 recovered:true → 绿横幅「已自动恢复」
      if (snap.items.some((it) => it.recovered)) {
        useQueryStore.getState().setRecoveredBanner(true);
      }
      for (const m of messages) {
        if (m.status === 'running' || m.status.startsWith('waiting_')) {
          streamsRef.current.set(m.query_id, attachStream(m.query_id));
        }
      }
    })().catch(() => {
      if (!cancelled) useQueryStore.getState().hydrate([], {});
    });

    return () => {
      cancelled = true;
      stopAll();
    };
  }, [sessionId, stopAll]);

  /** 步骤 4：乱序 fail-loud → REST resync（重拉 E5 快照重建装配态） */
  useEffect(() => {
    if (!resyncing || !sessionId) return;
    (async () => {
      stopAll();
      const snap = await sessionsApi.snapshot(sessionId);
      const messages = snap.items.map((it) => mapSnapshotItem(it, sessionId));
      useQueryStore.getState().hydrate(messages, snap.last_seq_by_query);
      useQueryStore.getState().setResyncing(false);
      for (const m of messages) {
        if (m.status === 'running' || m.status.startsWith('waiting_')) {
          streamsRef.current.set(m.query_id, attachStream(m.query_id));
        }
      }
    })().catch(() => useQueryStore.getState().setResyncing(false));
  }, [resyncing, sessionId, stopAll]);

  /** 终态 invalidate 三键（V4/D-20）：done/failed/stopped 后刷新 REST 缓存 */
  const messages = useQueryStore((s) => s.messages);
  const invalidatedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const m of messages) {
      if (
        (m.status === 'done' || m.status === 'failed' || m.status === 'stopped') &&
        !invalidatedRef.current.has(m.query_id)
      ) {
        invalidatedRef.current.add(m.query_id);
        streamsRef.current.get(m.query_id)?.stop();
        streamsRef.current.delete(m.query_id);
        void qc.invalidateQueries({ queryKey: ['sessions'] });
        void qc.invalidateQueries({ queryKey: ['queries'] });
        void qc.invalidateQueries({ queryKey: ['samples'] });
      }
    }
  }, [messages, qc]);

  /**
   * 契约 3 流切换兜底：followup_started 把排队壳置为 running 后，此处给新 query_id 挂流
   * （幂等：streamsRef 已有句柄则跳过；queued 排队壳不挂流——尚无事件可播；
   *  stoppedRef 防 stop 竞态：本地已停但状态未落终态的消息不重挂）
   */
  useEffect(() => {
    for (const m of messages) {
      if (
        (m.status === 'running' || m.status.startsWith('waiting_')) &&
        !m.queued &&
        !stoppedRef.current.has(m.query_id) &&
        !streamsRef.current.has(m.query_id)
      ) {
        streamsRef.current.set(m.query_id, attachStream(m.query_id));
      }
    }
  }, [messages]);

  /** 发起新查询：登记消息壳 + 挂流（mock 模式走 mockRunPipeline 驱动事件；F7 离线只跑到 validate） */
  const beginQuery = useCallback((queryId: string, opts: { offline?: boolean } = {}) => {
    if (mockEnabled()) {
      const stop = mockRunPipeline(queryId, (ev, seq) =>
        useQueryStore.getState().applyEvent(queryId, ev, seq), opts);
      streamsRef.current.set(queryId, { stop });
    } else {
      streamsRef.current.set(queryId, attachStream(queryId));
    }
  }, []);

  /** 本地已主动停止的查询集合：watcher 挂流兜底时跳过，防 stop→终态竞态重挂 */
  const stoppedRef = useRef<Set<string>>(new Set());

  const stopQuery = useCallback((queryId: string) => {
    stoppedRef.current.add(queryId);
    streamsRef.current.get(queryId)?.stop();
    streamsRef.current.delete(queryId);
  }, []);

  return { beginQuery, stopQuery };
}
