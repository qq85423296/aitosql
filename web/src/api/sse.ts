/**
 * sse.ts —— 自研 SSE reader（C3.1）：fetch + ReadableStream 逐帧解析。
 * 选 fetch 而非 EventSource：支持 Last-Event-ID 自定义头与 Bearer（D-06；EventSource 不支持自定义头/POST）。
 *
 * 帧格式：id: {query_id}:{seq}\nevent: {type}\ndata: {json}\n\n
 * - 注释帧 `:hb`（每 sse.heartbeat_s=15s）：过滤，不占 seq 不入表
 * - 控制帧 stream_resumed：连接级，无 seq 无 id，不参与 seq 幂等（旁路上报）
 * - 非落库事件（result 等大体积帧，C3.2 冻结）：**不写 id 字段**——seq 解析为 null，
 *   queryStore.applyEvent 旁路幂等直接应用（P0-2）
 */
import { apiBase, getToken } from './client';
import type { SseEvent } from './types';

export interface SseFrame {
  id?: string; // "{query_id}:{seq}"
  event?: string; // 缺省 = message
  data: string;
}

export interface SseHandlers {
  /** 业务事件：已解析为 SseEvent，frameId/seq 一并给出供幂等 */
  onEvent: (ev: SseEvent, frameId: string | null, seq: number | null) => void;
  /** 控制帧（stream_resumed 等，连接级，不入 appliedSeq） */
  onControl?: (name: string, data: string) => void;
  onOpen?: () => void;
  /** 网络层断开（非主动 abort）；由调用方决定重连/resync */
  onDrop?: (err: Error) => void;
}

export interface SseConnection {
  close: () => void;
}

const CONTROL_FRAMES = new Set(['stream_resumed']);

export function parseFrameId(id: string | undefined): { queryId: string; seq: number } | null {
  if (!id) return null;
  const idx = id.lastIndexOf(':');
  if (idx < 0) return null;
  const seq = Number(id.slice(idx + 1));
  if (!Number.isFinite(seq)) return null;
  return { queryId: id.slice(0, idx), seq };
}

/** 增量解析 SSE 字节流 */
export function createSseParser(handlers: SseHandlers) {
  let buf = '';
  let curId: string | undefined;
  let curEvent: string | undefined;
  let curData: string[] = [];

  function dispatch() {
    const data = curData.join('\n');
    const name = curEvent ?? 'message';
    const frame = { id: curId, event: curEvent, data };
    curId = undefined;
    curEvent = undefined;
    curData = [];
    if (!data) return;
    if (CONTROL_FRAMES.has(name)) {
      handlers.onControl?.(name, data);
      return;
    }
    let parsed: SseEvent;
    try {
      parsed = JSON.parse(data) as SseEvent;
    } catch {
      // 单事件 payload 截断附 truncated:true 仍为合法 JSON；非 JSON 行直接丢弃
      return;
    }
    const fi = parseFrameId(frame.id);
    handlers.onEvent(parsed, frame.id ?? null, fi?.seq ?? null);
  }

  return {
    feed(text: string) {
      buf += text;
      const lines = buf.split(/\r?\n/);
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (line === '') {
          dispatch();
          continue;
        }
        if (line.startsWith(':')) continue; // :hb 心跳注释帧
        const idx = line.indexOf(':');
        const field = idx < 0 ? line : line.slice(0, idx);
        let value = idx < 0 ? '' : line.slice(idx + 1);
        if (value.startsWith(' ')) value = value.slice(1);
        if (field === 'id') curId = value;
        else if (field === 'event') curEvent = value;
        else if (field === 'data') curData.push(value);
      }
    },
    flush() {
      if (curData.length) dispatch();
    },
  };
}

export interface ConnectOptions {
  /** Last-Event-ID：增量续播；缺省 = 无头全量自 seq=1 起 */
  lastEventId?: string | null;
  signal?: AbortSignal;
}

export function connectQueryStream(
  queryId: string,
  handlers: SseHandlers,
  opts: ConnectOptions = {},
): SseConnection {
  const controller = new AbortController();
  const signal = opts.signal;
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  (async () => {
    try {
      const res = await fetch(`${apiBase()}/queries/${queryId}/stream`, {
        headers: {
          Authorization: `Bearer ${getToken()}`,
          Accept: 'text/event-stream',
          ...(opts.lastEventId ? { 'Last-Event-ID': opts.lastEventId } : {}),
        },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`);
      handlers.onOpen?.();
      const parser = createSseParser(handlers);
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        parser.feed(value);
      }
      parser.flush();
      handlers.onDrop?.(new Error('SSE stream ended'));
    } catch (err) {
      if (!controller.signal.aborted) handlers.onDrop?.(err as Error);
    }
  })();

  return { close: () => controller.abort() };
}

/** 摄取进度 SSE（POST /datasources/:id/ingest）：事件 {pct, line}，E1002 续摄 */
export function connectIngestStream(
  datasourceId: number,
  handlers: { onProgress: (pct: number, line: string) => void; onDone: (err?: Error) => void },
  opts: { signal?: AbortSignal } = {},
): SseConnection {
  const controller = new AbortController();
  if (opts.signal) {
    if (opts.signal.aborted) controller.abort();
    else opts.signal.addEventListener('abort', () => controller.abort(), { once: true });
  }
  (async () => {
    try {
      const res = await fetch(`${apiBase()}/datasources/${datasourceId}/ingest`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${getToken()}`, Accept: 'text/event-stream' },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`ingest HTTP ${res.status}`);
      const parser = createSseParser({
        onEvent: (ev) => {
          const p = ev as unknown as { pct?: number; line?: string };
          handlers.onProgress(p.pct ?? 0, p.line ?? '');
        },
      });
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        parser.feed(value);
      }
      parser.flush();
      handlers.onDone();
    } catch (err) {
      if (!controller.signal.aborted) handlers.onDone(err as Error);
    }
  })();
  return { close: () => controller.abort() };
}
