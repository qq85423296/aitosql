/** C1.2 表/字段编辑与提示词视图 */
import { api } from './client';
import type { ColumnMeta, TableDetailRes, TableMeta } from './types';

export const tablesApi = {
  /**
   * 表详情（C1.2 冻结，P1-5 修复）：GET /tables/:id 返回 `{table, columns}` 包络——
   * 后端无独立 /tables/:id/columns 路由，字段列由本包络供给（ContextPanel/TableDetail 共用）。
   */
  get: (id: number) => api.get<TableDetailRes>(`/tables/${id}`),
  /** {comment_custom?, …}；人工注释优先，重摄取不覆盖 */
  patch: (id: number, body: { comment_custom?: string | null; partition_type?: string | null }) =>
    api.patch<TableMeta>(`/tables/${id}`, body),
  /** 在线库字段名/类型只读 */
  patchColumn: (
    id: number,
    body: {
      comment_custom?: string | null;
      is_pk?: boolean;
      is_partition?: boolean;
      is_bucket?: boolean;
      field_standard_id?: number | null;
    },
  ) => api.patch<ColumnMeta>(`/columns/${id}`, body),
  /** 提示词视图：与真实注入逐字一致（D-09 同源） */
  promptView: (id: number) => api.get<{ text: string }>(`/tables/${id}/prompt-view`),
};
