/** C1.2 数据源与元数据 */
import { api } from './client';
import type {
  Datasource,
  DdlPreview,
  DsTestResult,
  GraphData,
  GraphLayout,
  TableListItem,
} from './types';

export interface CreateDatasourceReq {
  name: string;
  dialect: 'mysql' | 'doris' | 'clickhouse';
  mode: 'online' | 'offline';
  host?: string;
  port?: number;
  db_name?: string;
  username?: string;
  password?: string;
}

/** 摆位读取包络（GET /datasources/:id/graph/layout，C1.2 冻结） */
interface GraphLayoutRes {
  datasource_id: number;
  node_positions: GraphLayout;
  updated_at?: string | null;
}

export const datasourcesApi = {
  list: () => api.get<Datasource[]>('/datasources'),
  create: (body: CreateDatasourceReq) => api.post<Datasource>('/datasources', body),
  patch: (id: number, body: Partial<CreateDatasourceReq>) =>
    api.patch<Datasource>(`/datasources/${id}`, body),
  remove: (id: number) => api.del<void>(`/datasources/${id}`),
  /** 测试连接：200 {version, readonly} / 502 {code:E1001} */
  test: (id: number) => api.post<DsTestResult>(`/datasources/${id}/test`),
  /** 摄取：SSE 进度 {pct, line}，走 sse.ts connectIngestStream；中断续摄 E1002 */
  /** DDL 预览/入库：部分失败行标红、成功可入库 */
  importDdlPreview: (id: number, ddl: string) =>
    api.post<DdlPreview>(`/datasources/${id}/import-ddl`, { ddl }),
  importDdlCommit: (id: number, ddl: string) =>
    api.post<{ imported: number; errors?: { line: number; message: string }[] }>(
      `/datasources/${id}/import-ddl/commit`,
      { ddl },
    ),
  /** 元数据字典模板（11 列中文表头）：多表批量入库，行级错误不阻断 */
  importCsvTemplate: (id: number, csv_text: string) =>
    api.post<{
      tables: number;
      columns: number;
      schemas: string[];
      errors: { row: number; table: string; message: string }[];
    }>(`/datasources/${id}/import-csv-template`, { csv_text }),
  /** 表清单（C1.2 冻结字段：name/comment/fields/rows/pk/partition，P1-5） */
  tables: (id: number) => api.get<TableListItem[]>(`/datasources/${id}/tables`),
  /** ER 图谱 {nodes, edges}（D-18） */
  graph: (id: number) => api.get<GraphData>(`/datasources/${id}/graph`),
  /** 摆位读写（C1.2 冻结，P1-4）：嵌套路径 /graph/layout + {node_positions} 包络 */
  graphLayout: {
    get: async (id: number): Promise<GraphLayout> => {
      const r = await api.get<GraphLayoutRes>(`/datasources/${id}/graph/layout`);
      return r?.node_positions ?? {};
    },
    put: (id: number, layout: GraphLayout) =>
      api.put<void>(`/datasources/${id}/graph/layout`, { node_positions: layout }),
  },
  /** 契约 5：数据源口径说明文档（GET 加载 / PUT {content} 保存；注入生成提示词的数据源口径段）。
   *  防御：GET 包络缺 content 字段时前端按空串处理（可选链在调用侧） */
  instruction: {
    get: (id: number) => api.get<{ content: string }>(`/datasources/${id}/instruction`),
    put: (id: number, content: string) => api.put<void>(`/datasources/${id}/instruction`, { content }),
  },
};
