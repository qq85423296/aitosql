/** C1.5 查询主链路 + C2 幂等 */
import { api, downloadCsv } from './client';
import type { QueryRecord } from './types';

export interface CreateQueryReq {
  session_id: string;
  datasource_id: number;
  question: string;
  client_request_id: string; // q_+ULID，前端缺省生成（D-22）；同 id 重发不重复执行
}
export interface CreateQueryRes {
  /** 契约 1：会话忙时为 null（status='queued' 排队回执，当前查询终态后自动开轮）；
   *  防御性允许 null，前端据 queued/query_id 判定走排队壳还是直接挂流 */
  query_id: string | null;
  stream: string;
  /** 排队回执标记（契约 1） */
  queued?: boolean;
  /** 排队 inbox 项 id（契约 1，展示/调试用途） */
  inbox_id?: string;
  /** 排队锚定的活动查询 id（契约 1） */
  anchor_query_id?: string | null;
  status?: string;
}

/** 门裁决请求体（契约 4：clarify 支持多选 choices + 自定义补充 custom；向后兼容单选 choice） */
export interface ResumeReq {
  gate: 'clarify' | 'confirm';
  /** 单选兼容字段（旧契约；多选时前端可同时回传首个选项，后端忽略） */
  choice?: string;
  /** clarify 多选选项 label 列表 */
  choices?: string[];
  /** clarify 自定义补充文本 */
  custom?: string;
  approved?: boolean;
}

/** 运行中输入（契约 2）：POST /queries/:id/steer，仅活动查询受理 */
export interface SteerReq {
  content: string;
  kind?: 'steer' | 'inject'; // 缺省 steer
}

export const queriesApi = {
  /** POST /queries → 202 {query_id, stream}；会话忙时 {query_id:null, queued:true, inbox_id, anchor_query_id, stream}（契约 1） */
  create: (body: CreateQueryReq) => api.post<CreateQueryRes>('/queries', body),
  get: (id: string) => api.get<QueryRecord>(`/queries/${id}`),
  /** 门裁决（CAS）：仅 waiting_* 态受理，否则 409；clarify 支持 choices+custom（契约 4） */
  resume: (id: string, body: ResumeReq) => api.post<QueryRecord>(`/queries/${id}/resume`, body),
  /** 运行中引导/注入（契约 2）：返回 {inbox_id, queued:true} */
  steer: (id: string, body: SteerReq) =>
    api.post<{ inbox_id: string; queued: boolean }>(`/queries/${id}/steer`, body),
  /** 停止（引擎 KILL）；CH readonly 无 KILL 通道（UI 注明） */
  stop: (id: string) => api.post<void>(`/queries/${id}/stop`),
  /** 存为样例：同步直写 memory_log（D-14 入口 1） */
  saveSample: (id: string) => api.post<{ sample_id: number }>(`/queries/${id}/save-sample`),
  /** 全量落盘导出：FileResponse 流式；响应头回显 data_max_time；仅纯 SELECT 成功时文件存在 */
  exportCsv: (id: string) => downloadCsv(id),
};
