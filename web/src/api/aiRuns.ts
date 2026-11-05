/** AI 补全任务 API（方案 v1.1 §8 新增契约）：创建 / SSE 泳道进度 / 取消 / 查询。
 *
 * run() 全流程封装：真实模式 = POST 创建 + GET stream SSE；
 * mock 模式 = mock.ts mockSimulateCompletion 本地模拟泳道事件流（形状与 SSE 一致）。
 */
import { api, apiBase, getToken } from './client';
import { MOCK_COMPILE_ALLOWED, mockEnabled } from './client';
import type { AiRun, CompletionRunReq, LaneEvent } from './types';

export const aiRunsApi = {
  create: (body: CompletionRunReq) => api.post<{ run_id: number }>('/ai/completion-runs', body),
  get: (id: number) => api.get<AiRun>(`/ai/completion-runs/${id}`),
  list: (datasourceId: number, limit = 10) =>
    api.get<AiRun[]>(`/ai/completion-runs?datasource_id=${datasourceId}&limit=${limit}`),
  cancel: (id: number) => api.post<{ cancelled: boolean }>(`/ai/completion-runs/${id}/cancel`),

  /** 创建并跟随整个补全任务：onStart 收 run_id，onLane 收泳道事件，onEnd 收终帧；返回 {close} 可中断 */
  run(
    body: CompletionRunReq,
    handlers: {
      onStart?: (runId: number) => void;
      onLane: (ev: LaneEvent) => void;
      onEnd: (status: string, made: Record<string, number>, error?: string) => void;
    },
  ): { close: () => void } {
    if (MOCK_COMPILE_ALLOWED && mockEnabled()) {
      // 编译闸门守卫：生产构建此分支被 DCE，mock 模块不进 dist（P0-4 断言）
      let closed = false;
      void import('./mock').then((m) => {
        if (!closed) m.mockSimulateCompletion(body, handlers, () => closed);
      });
      return {
        close: () => {
          closed = true;
        },
      };
    }
    const controller = new AbortController();
    (async () => {
      try {
        const { run_id } = await aiRunsApi.create(body);
        handlers.onStart?.(run_id);
        const res = await fetch(`${apiBase()}/ai/completion-runs/${run_id}/stream`, {
          headers: { Authorization: `Bearer ${getToken()}`, Accept: 'text/event-stream' },
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`stream HTTP ${res.status}`);
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        let buf = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += value;
          const parts = buf.split('\n\n');
          buf = parts.pop() ?? '';
          for (const part of parts) {
            const line = part.split('\n').find((l) => l.startsWith('data:'));
            if (!line) continue;
            const ev = JSON.parse(line.slice(5).trim()) as LaneEvent;
            if (ev.phase === 'run_end') {
              handlers.onEnd(ev.status ?? 'done', ev.made ?? {}, ev.error);
            } else {
              handlers.onLane(ev);
            }
          }
        }
      } catch (err) {
        if (!controller.signal.aborted) handlers.onEnd('failed', {}, (err as Error).message);
      }
    })();
    return {
      close: () => {
        controller.abort();
        // 中断仅断流；任务协作式取消走 cancel()（方案 §3）
      },
    };
  },

  /** 协作式取消（真实模式调后端；mock 由模拟器自检标志位） */
  cancelRun: (id: number) => api.post<{ cancelled: boolean }>(`/ai/completion-runs/${id}/cancel`),
};
