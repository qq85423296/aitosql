/**
 * useAiCompletionRun —— AI 补全统一工作区的唯一任务控制器（2026-11-05 计划 §4.3）。
 *
 * 语义要点（与后端约束逐条对齐，backend/app/api/ai_runs.py）：
 * 1. 创建防重：creating 期间拒绝二次提交；同数据源已有 queued/running 任务时由 UI 禁新建，
 *    409（E3101）竞态兜底 = 创建阶段失败（onStart 未触发）→ 重新 list 认领活动任务，不误报运行失败；
 * 2. SSE 不可重放：aiRunsApi.run() 仅用于本会话新建任务（实时泳道叠加层）；
 *    离开/重进/断流后以 list/get 持久状态为准轮询恢复，由调用方展示「进度可能略有延迟」，
 *    绝不伪造没有证据的历史泳道；
 * 3. 关闭 ≠ 取消：close() 只断流；唯一取消通道是 cancel() → cancelRun（协作式）；
 * 4. 终态收敛：SSE run_end 与轮询都可能先看到终态——统一经 get() 复核后 finalize，
 *    endedRef 幂等去重，不产生重复结束通知；终态后刷新建议与任务缓存；
 * 5. 地面真值轮询：运行中每 3s get()（单用户本地应用开销可忽略），同时覆盖
 *    「SSE 静默断开但任务仍跑」（run() 对无 run_end 的流结束不发回调）这一缺口。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { aiRunsApi } from '../../api/aiRuns';
import type { AiRun, AiRunStatus, CompletionRunReq } from '../../api/types';
import { toast } from '../ui/Toast';

export interface RunLaneState {
  table: string;
  phase: string;
  line: string;
}

/** 实时泳道叠加层：仅本会话新建任务且 SSE 连接存活期间存在 */
export interface RunLiveState {
  lanes: Record<number, RunLaneState>;
  logs: string[];
  progress: { done: number; total: number; pct: number };
  made: Record<string, number>;
  currentTable: string | null;
}

export interface AiRunController {
  /** 当前跟踪的任务（活动或最近终态）；null = 无任务/终态已复位 */
  run: AiRun | null;
  live: RunLiveState | null;
  /** true = 经 list/get 恢复或 SSE 已丢失，进度来自服务端持久状态（非实时） */
  restored: boolean;
  creating: boolean;
  cancelling: boolean;
  /** 创建阶段失败原因（运行中失败看 run.error / 终态卡片） */
  startError: string | null;
  start: (body: CompletionRunReq) => void;
  cancel: () => void;
  /** 终态复位（「重新分析」回到配置态）；仅终态可复位，不影响历史建议与任务记录 */
  resetTerminal: () => void;
  clearStartError: () => void;
}

const TERMINAL: AiRunStatus[] = ['done', 'failed', 'cancelled'];
const isActive = (r: AiRun | null): r is AiRun => r != null && (r.status === 'queued' || r.status === 'running');

export function useAiCompletionRun(datasourceId: number, onTerminal?: (run: AiRun) => void): AiRunController {
  const qc = useQueryClient();
  const [run, setRun] = useState<AiRun | null>(null);
  const [live, setLive] = useState<RunLiveState | null>(null);
  const [restored, setRestored] = useState(false);
  const [creating, setCreating] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const connRef = useRef<{ close: () => void } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);
  const runIdRef = useRef<number | null>(null);
  const endedRef = useRef<number | null>(null);
  /** run 的同步镜像：adoptActive 竞态守卫（挂载恢复晚于本会话新建返回时不得覆盖实时态） */
  const runRef = useRef<AiRun | null>(null);
  runRef.current = run;
  const onTerminalRef = useRef(onTerminal);
  onTerminalRef.current = onTerminal;

  const stopPoll = useCallback(() => {
    if (pollRef.current != null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const closeConn = useCallback(() => {
    // 仅断流，不取消任务（计划 §1 原则 3）
    connRef.current?.close();
    connRef.current = null;
  }, []);

  /** 终态一次落定：以 get() 复核后的持久行为准；幂等（endedRef）；刷新建议/任务缓存并通知工作区 */
  const finalize = useCallback(
    (final: AiRun) => {
      if (!TERMINAL.includes(final.status)) return;
      if (endedRef.current === final.id) return;
      endedRef.current = final.id;
      stopPoll();
      closeConn();
      setRun(final);
      setCancelling(false);
      void qc.invalidateQueries({ queryKey: ['suggestions', datasourceId] });
      void qc.invalidateQueries({ queryKey: ['aiRuns', datasourceId] });
      if (final.status === 'done') {
        const total = Object.values(final.made ?? {}).reduce((a, b) => a + b, 0);
        toast(total > 0 ? `AI 补全完成，产出 ${total} 条建议，待评审` : 'AI 补全完成，本次没有生成新建议', total > 0 ? 'ok' : 'info');
      } else if (final.status === 'cancelled') {
        toast('已取消分析', 'info');
      } else {
        toast(`补全失败：${final.error ?? '未知错误'}`, 'err');
      }
      onTerminalRef.current?.(final);
    },
    [closeConn, datasourceId, qc, stopPoll],
  );

  /** 运行期地面真值轮询（无论 SSE 是否存活）：终态即收；3s 间隔，单用户本地应用开销可忽略 */
  const armGroundTruthPoll = useCallback(() => {
    if (pollRef.current != null) return;
    pollRef.current = setInterval(() => {
      const id = runIdRef.current;
      if (id == null) return;
      aiRunsApi
        .get(id)
        .then((r) => {
          if (!mountedRef.current || runIdRef.current !== id) return;
          setRun(r);
          if (TERMINAL.includes(r.status)) finalize(r);
        })
        .catch(() => {
          /* 单次轮询失败静默，下一拍重试 */
        });
    }, 3000);
  }, [finalize]);

  /** 认领服务端活动任务（打开工作区 / 409 冲突时共用）：有则跟踪并轮询；返回是否认领成功 */
  const adoptActive = useCallback(async (): Promise<boolean> => {
    // 已在跟踪活动任务（本会话新建）时不覆盖实时态（防挂载恢复竞态打掉 SSE 泳道）
    if (isActive(runRef.current)) return true;
    const runs = await aiRunsApi.list(datasourceId);
    if (!mountedRef.current || isActive(runRef.current)) return false;
    const active = runs.find((r) => r.status === 'queued' || r.status === 'running');
    if (!active) return false;
    const fresh = await aiRunsApi.get(active.id).catch(() => active);
    if (!mountedRef.current) return false;
    runIdRef.current = fresh.id;
    endedRef.current = null;
    setRun(fresh);
    setRestored(true);
    setLive(null);
    if (TERMINAL.includes(fresh.status)) finalize(fresh);
    else armGroundTruthPoll();
    return true;
  }, [armGroundTruthPoll, datasourceId, finalize]);

  // 挂载时恢复：找到本数据源活动任务则跟踪（离开重进的核心路径）；卸载仅释放连接与轮询
  useEffect(() => {
    mountedRef.current = true;
    void adoptActive().catch(() => {
      /* 列表失败不阻塞新建 */
    });
    return () => {
      mountedRef.current = false;
      stopPoll();
      closeConn(); // 离开路由：释放 SSE，不发取消请求（任务后台续跑）
    };
  }, [adoptActive, closeConn, stopPoll]);

  const start = useCallback(
    (body: CompletionRunReq) => {
      if (creating) return; // 创建中防重
      if (isActive(run)) return; // 有活动任务禁新建（UI 层另有禁用与解释）
      closeConn();
      stopPoll();
      setCreating(true);
      setStartError(null);
      setRestored(false);
      setLive({ lanes: {}, logs: [], progress: { done: 0, total: 0, pct: 0 }, made: {}, currentTable: null });
      let started = false;
      let myRunId: number | null = null;
      connRef.current = aiRunsApi.run(body, {
        onStart: (id) => {
          started = true;
          myRunId = id;
          runIdRef.current = id;
          endedRef.current = null;
          setCreating(false);
          setRun({
            id,
            datasource_id: datasourceId,
            scope_type: body.scope.type,
            scope_table_id: body.scope.table_id ?? null,
            kinds: body.kinds,
            options: body.options ?? { rescan_existing: false, use_sample_values: false },
            status: 'running',
            progress_done: 0,
            progress_total: 0,
            made: {},
            failed: [],
            error: null,
            created_at: null,
            finished_at: null,
          });
          armGroundTruthPoll();
        },
        onLane: (ev) => {
          setLive((prev) => {
            const base: RunLiveState =
              prev ?? { lanes: {}, logs: [], progress: { done: 0, total: 0, pct: 0 }, made: {}, currentTable: null };
            if (ev.phase === 'progress') {
              return {
                ...base,
                progress: { done: ev.done ?? 0, total: ev.total ?? 0, pct: ev.pct ?? 0 },
                made: ev.made ?? base.made,
              };
            }
            const lanes = { ...base.lanes, [ev.lane]: { table: ev.table, phase: ev.phase, line: ev.line } };
            const made = { ...base.made };
            if (ev.made) for (const [k, v] of Object.entries(ev.made)) made[k] = (made[k] ?? 0) + v;
            return {
              ...base,
              lanes,
              logs: [...base.logs.slice(-60), ev.line],
              made,
              currentTable: ev.phase === 'analyzing' || ev.phase === 'validating' ? ev.table : base.currentTable,
            };
          });
        },
        onEnd: (_status, _made, error) => {
          setCreating(false);
          connRef.current = null;
          if (!started) {
            // 创建阶段失败（含 409 冲突）：不是运行终态——优先认领已存在的活动任务
            void adoptActive()
              .then((adopted) => {
                if (!mountedRef.current) return;
                if (adopted) toast('该数据源已有进行中的补全任务，已切换到任务进度', 'info');
                else setStartError(error ?? '创建任务失败');
              })
              .catch(() => {
                if (mountedRef.current) setStartError(error ?? '创建任务失败');
              });
            return;
          }
          // 运行终态/流结束：以 get() 复核持久行为准；仍在跑则转轮询恢复（不伪装结束）
          const id = myRunId;
          if (id == null) return;
          aiRunsApi
            .get(id)
            .then((r) => {
              if (!mountedRef.current || runIdRef.current !== id) return;
              if (r.status === 'queued' || r.status === 'running') {
                setRestored(true);
                setLive(null);
                setRun(r);
                armGroundTruthPoll();
              } else {
                finalize(r);
              }
            })
            .catch(() => {
              // 复核失败不落终态：地面真值轮询仍在，会以持久状态收敛
              if (!mountedRef.current || runIdRef.current !== id) return;
              setRestored(true);
              setLive(null);
            });
        },
      });
    },
    [adoptActive, armGroundTruthPoll, closeConn, creating, datasourceId, finalize, run, stopPoll],
  );

  /** 明确取消（高辨识操作）：仅调协作式取消端点；终态由 SSE 终帧或轮询复核落定 */
  const cancel = useCallback(() => {
    const id = runIdRef.current;
    if (id == null || endedRef.current === id) return;
    setCancelling(true);
    aiRunsApi
      .cancelRun(id)
      .catch((e) => toast(`取消失败：${(e as Error).message}`, 'err'))
      .finally(() => {
        if (mountedRef.current) setCancelling(false);
      });
  }, []);

  const resetTerminal = useCallback(() => {
    setRun((prev) => (prev && TERMINAL.includes(prev.status) ? null : prev));
    setLive(null);
    setStartError(null);
    runIdRef.current = null;
    endedRef.current = null;
  }, []);

  const clearStartError = useCallback(() => setStartError(null), []);

  return { run, live, restored, creating, cancelling, startError, start, cancel, resetTerminal, clearStartError };
}
