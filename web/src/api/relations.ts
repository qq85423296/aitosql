/** C1.2 关系：inferred 默认 pending，confirm 后才注入 */
import { api } from './client';
import type { Relation } from './types';

export const relationsApi = {
  list: (datasourceId: number) => api.get<Relation[]>(`/relations?datasource_id=${datasourceId}`),
  create: (body: {
    datasource_id: number;
    from_table: string;
    from_col: string;
    to_table: string;
    to_col: string;
  }) => api.post<Relation>('/relations', body),
  confirm: (id: number) => api.post<Relation>(`/relations/${id}/confirm`),
  remove: (id: number) => api.del<void>(`/relations/${id}`),
};
