/** C1.3 建议队列：accept/ignore 批量 {ids}；采纳映射见 C6.8/C6.8+
 *  方案 v1.1 §8 增量：list 支持 run_id/table 过滤；accept 支持 edits 编辑后采纳。 */
import { api } from './client';
import type { SuggestionListRes } from './types';

export const suggestionsApi = {
  /** C1.3 冻结包络（P1-6）：`{groups: {kind: [...]}, total}`（pending 按 kind 分组） */
  list: (datasourceId: number, status?: string, filter?: { runId?: number | null; table?: string | null }) =>
    api.get<SuggestionListRes>(
      `/suggestions?datasource_id=${datasourceId}${status ? `&status=${status}` : ''}` +
        `${filter?.runId ? `&run_id=${filter.runId}` : ''}` +
        `${filter?.table ? `&table=${encodeURIComponent(filter.table)}` : ''}`,
    ),
  /** 批量采纳；edits={建议id: 编辑文本}（comment 类编辑后采纳，方案 §7） */
  accept: (ids: number[], edits?: Record<number, string>) =>
    api.post<{ accepted: number }>('/suggestions/accept', edits ? { ids, edits } : { ids }),
  ignore: (ids: number[]) => api.post<{ ignored: number }>('/suggestions/ignore', { ids }),
};
