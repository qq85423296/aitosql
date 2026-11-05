/**
 * client.ts —— fetch 封装（C0）：/api 前缀 + Bearer token + envelope 解包 + 错误码抛出 + 超时。
 * mock 开关（修复 P0-4）：**运行时**判定，默认关闭——
 *   优先级：`?mock=1/0` URL 参数 > localStorage `aitosql.mock` > VITE_API_MOCK==='1'（显式开启）。
 *   编译闸门：VITE_API_MOCK==='0'（.env.production，生产构建）时整段 mock 分支被静态折叠，
 *   mock 模块经动态 import() 完全从 dist 剔除；scripts/assert-no-mock.mjs 对产物做断言。
 * mock 数据形状必须与冻结契约（contracts.md）一致，禁止前端自创形状（架构守则）。
 */
import { ApiError, type Envelope } from './types';

const TOKEN_KEY = 'aitosql.token';
const MOCK_KEY = 'aitosql.mock';
const DEFAULT_TIMEOUT_MS = 30_000;

/** 编译期闸门：生产构建（.env.production VITE_API_MOCK=0）下为字面量 false，供打包器 DCE；
 *  任何动态 import('./mock') 的调用点都必须用它守卫（P0-4 产物断言） */
export const MOCK_COMPILE_ALLOWED = import.meta.env.VITE_API_MOCK !== '0';

export function getToken(): string {
  // 首启引导：支持 ?token=xxx 一次性写入 localStorage（本地单用户形态）
  if (typeof window !== 'undefined') {
    const q = new URLSearchParams(window.location.search).get('token');
    if (q) {
      localStorage.setItem(TOKEN_KEY, q);
      const url = new URL(window.location.href);
      url.searchParams.delete('token');
      window.history.replaceState(null, '', url.toString());
      return q;
    }
  }
  return localStorage.getItem(TOKEN_KEY) ?? '';
}
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}
/** 401 广播：App 门禁监听后回到登录页（token 过期/被重置场景） */
export const UNAUTHORIZED_EVENT = 'aitosql:unauthorized';

export function apiBase(): string {
  return import.meta.env.VITE_API_BASE ?? '/api';
}

/** 运行时 mock 判定（P0-4）：默认 false；仅显式开启时走本地 mock（contracts 冻结形状） */
export function mockEnabled(): boolean {
  if (!MOCK_COMPILE_ALLOWED) return false;
  if (typeof window === 'undefined') return false;
  const q = new URLSearchParams(window.location.search).get('mock');
  if (q === '1') return true;
  if (q === '0') return false;
  const ls = localStorage.getItem(MOCK_KEY);
  if (ls != null) return ls === '1';
  return import.meta.env.VITE_API_MOCK === '1';
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE' | 'PUT';
  body?: unknown;
  timeoutMs?: number;
  signal?: AbortSignal;
  /** 跳过 envelope 解包（如 FileResponse 流式导出） */
  raw?: boolean;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  if (MOCK_COMPILE_ALLOWED && mockEnabled()) {
    // 动态 import：mock 模块独立 chunk；编译闸门关闭时此分支被 DCE，mock 代码不进 dist
    const { mockHandle } = await import('./mock');
    return (await mockHandle(path, opts)) as T;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const link = opts.signal;
  if (link) {
    if (link.aborted) controller.abort();
    else link.addEventListener('abort', () => controller.abort(), { once: true });
  }
  try {
    const res = await fetch(`${apiBase()}${path}`, {
      method: opts.method ?? 'GET',
      headers: {
        Authorization: `Bearer ${getToken()}`,
        ...(opts.body != null ? { 'Content-Type': 'application/json' } : {}),
      },
      body: opts.body != null ? JSON.stringify(opts.body) : undefined,
      signal: controller.signal,
    });
    if (opts.raw) {
      if (!res.ok) throw new ApiError(`E${res.status}`, res.statusText, res.status);
      return res as unknown as T;
    }
    const env = (await res.json()) as Envelope<T>;
    if (!res.ok || (typeof env.code === 'string' && env.code.startsWith('E'))) {
      if (res.status === 401) {
        clearToken();
        window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
      }
      throw new ApiError(String(env.code), env.message ?? res.statusText, res.status);
    }
    return env.data;
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>(path, { ...opts, method: 'GET' }),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(path, { ...opts, method: 'PUT', body }),
  del: <T>(path: string, opts?: RequestOptions) => request<T>(path, { ...opts, method: 'DELETE' }),
};

/** 导出 CSV（C1.5）：FileResponse 流式；响应头回显 data_max_time；触发浏览器下载 */
export async function downloadCsv(queryId: string): Promise<void> {
  if (mockEnabled()) return; // mock 无真实文件
  const res = await fetch(`${apiBase()}/queries/${queryId}/export.csv`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) throw new ApiError(String(res.status), '导出失败', res.status);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${queryId}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
