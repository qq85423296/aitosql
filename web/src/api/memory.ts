/** C1.6 记忆：总账管理 + 流水账只读 + 手动合并 */
import { api } from './client';
import type { MemoryItem, MemoryLogItem } from './types';

export const memoryApi = {
  /** 总账：按 topic 分组返回 */
  ledger: () => api.get<Record<string, MemoryItem[]>>('/memory'),
  patch: (id: number, body: Partial<Pick<MemoryItem, 'content' | 'topic'>>) =>
    api.patch<MemoryItem>(`/memory/${id}`, body),
  remove: (id: number) => api.del<void>(`/memory/${id}`),
  /** 停用即不注入；C1.6 冻结（P1-8）：请求体 {enabled: boolean} 必填（显式目标态，非服务端取反） */
  toggle: (id: number, enabled: boolean) => api.post<MemoryItem>(`/memory/${id}/toggle`, { enabled }),
  log: () => api.get<MemoryLogItem[]>('/memory/log'),
  /** 手动合并：返回 {merged, dropped} */
  consolidate: () => api.post<{ merged: number; dropped: number }>('/memory/consolidate'),
};
