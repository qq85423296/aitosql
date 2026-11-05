/**
 * useQueryStream —— 单查询 SSE 生命周期（D-06/C3）：
 * 挂流 → 双模重连（Last-Event-ID 增量 / 无头全量）→ 断线自动重连（递增退避）→ 终态停止。
 * seq 幂等与乱序 fail-loud 在 queryStore.applyEvent 内完成。
 */
import { connectQueryStream, parseFrameId, type SseConnection } from '../api/sse';
import { mockEnabled } from '../api/client';
import { useQueryStore } from '../stores/queryStore';

export interface StreamHandle {
  stop: () => void;
}

/** 为 running/waiting_* 查询挂 SSE；返回停止函数 */
export function attachStream(queryId: string): StreamHandle {
  if (mockEnabled()) return { stop: () => {} }; // mock 模式由 useQueryStream.mockRun 驱动
  const store = useQueryStore.getState();
  let conn: SseConnection | null = null;
  let closed = false;
  let retries = 0;

  const connect = (incremental: boolean) => {
    const lastSeq = useQueryStore.getState().appliedSeq[queryId];
    conn = connectQueryStream(
      queryId,
      {
        onEvent: (ev, _frameId, seq) => {
          useQueryStore.getState().applyEvent(queryId, ev, seq);
          // F9：仅 done/stopped 关流——error 后后端保证 done 帧收尾（_fail/E1001/
          // E2004 路径均有 done 或停在 failed 行态由 hydrate 兜底）；error 即关流
          // 会丢弃随后的 meta pre_done/done 帧（appliedSeq 停更、tokens 统计丢失），
          // 且把「error=终态」假设写死，阻塞后端未来引入非终态警告帧
          if (ev.type === 'done' || ev.type === 'stopped') {
            closed = true;
            conn?.close();
          }
        },
        onControl: (name) => {
          // 控制帧 stream_resumed：连接级，无 seq 不入 appliedSeq；横幅仅增量场景亮
          if (name === 'stream_resumed' && incremental) {
            useQueryStore.getState().setResumedBanner(true);
          }
        },
        onDrop: () => {
          if (closed) return;
          // 断线重连：双模归一——已知 seq 走 Last-Event-ID 增量，否则无头全量
          retries += 1;
          if (retries > 5) return;
          const delay = Math.min(1000 * 2 ** (retries - 1), 10_000);
          setTimeout(() => {
            if (!closed) connect(useQueryStore.getState().appliedSeq[queryId] != null);
          }, delay);
        },
      },
      {
        lastEventId:
          incremental && lastSeq != null ? `${queryId}:${lastSeq}` : null,
      },
    );
  };

  // 首次连接：store.appliedSeq 已有该 query 的 last_seq（E5 快照）→ 增量；否则全量
  connect(store.appliedSeq[queryId] != null);

  return {
    stop: () => {
      closed = true;
      conn?.close();
    },
  };
}

// Q-12：原 `useQueryStream` React 绑定为无调用方死代码（装配走 useAssembly/attachStream），已删除。

export { parseFrameId };
