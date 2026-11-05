/** C1.3 语义层三组：standards / dimensions / metrics（全局一份跨数据源，C6.5） */
import { api } from './client';
import type { DimensionStandard, FieldStandard, MetricStandard } from './types';

function crud<T>(base: string) {
  return {
    list: () => api.get<T[]>(`/${base}`),
    create: (body: Partial<T>) => api.post<T>(`/${base}`, body),
    patch: (id: number, body: Partial<T>) => api.patch<T>(`/${base}/${id}`, body),
    remove: (id: number) => api.del<void>(`/${base}/${id}`),
  };
}

export const standardsApi = crud<FieldStandard>('standards');
export const dimensionsApi = crud<DimensionStandard>('dimensions');
/** metrics 保存时 sqlglot 校验，未知标识符黄警不阻断（响应可带 warnings） */
export const metricsApi = {
  ...crud<MetricStandard>('metrics'),
  /** C1.3 冻结（Q-11）：POST /metrics 返回 `{id, warnings}`（非完整行）；完整行由调用方 invalidate 后重取 */
  save: (body: Partial<MetricStandard> & { name: string; expr: string }) =>
    api.post<{ id: number; warnings?: string[] }>('/metrics', body),
};
