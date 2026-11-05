/** C1.4 样例库：revalidate 走 EXPLAIN 干跑；question_norm 服务端归一化 */
import { api } from './client';
import type { Sample } from './types';

export const samplesApi = {
  list: (datasourceId?: number) =>
    api.get<Sample[]>(`/samples${datasourceId ? `?datasource_id=${datasourceId}` : ''}`),
  create: (body: { datasource_id: number; question: string; sql: string }) =>
    api.post<Sample>('/samples', body),
  patch: (id: number, body: Partial<Pick<Sample, 'question' | 'sql' | 'status'>>) =>
    api.patch<Sample>(`/samples/${id}`, body),
  remove: (id: number) => api.del<void>(`/samples/${id}`),
  /** EXPLAIN 干跑复验：通过 → 回 on，validated_at 刷新 */
  revalidate: (id: number) => api.post<Sample>(`/samples/${id}/revalidate`),
};
