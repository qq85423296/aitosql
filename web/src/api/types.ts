/**
 * types.ts —— 前端消费面类型（contracts.md C0-C10 唯一冻结面）。
 * 实体字段名对齐 C6 表结构 ↔ 原型 data.json 19 顶层键（C10.2）；
 * SSE 16 事件判别联合逐字对齐 C3.2；MessageModel 与 QueryState（C5）同构（S1-S6）。
 */

// ---------- C0 全局 ----------
export interface Envelope<T> {
  code: number | string; // 0 成功；错误时取 C8 错误码（E1001…）
  message: string;
  data: T;
}

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status = 0) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export type Dialect = 'mysql' | 'doris' | 'clickhouse';
export type DsMode = 'online' | 'offline';

// ---------- C6.1 datasource ----------
export interface Datasource {
  id: number;
  name: string;
  dialect: Dialect;
  mode: DsMode;
  host?: string | null;
  port?: number | null;
  db_name?: string | null;
  username?: string | null;
  // password 永不回显（C1.2）
  params_json?: string;
  version?: string | null;
  capabilities_json?: string;
  last_ingest_at?: string | null;
  created_at?: string;
  // 列表聚合展示字段（原型对齐）
  table_count?: number;
  field_count?: number;
}

// ---------- C6.2/C6.3 表与字段 ----------
/** 表详情（GET /tables/:id 包络内 table 键，C1.2 冻结）：DB 模型逐字段（model_dump） */
export interface TableMeta {
  id: number;
  datasource_id: number;
  name: string;
  comment_db?: string | null;
  comment_custom?: string | null;
  row_estimate?: number | null;
  field_count?: number | null;
  partition_type?: 'month' | 'day' | null;
  updated_at?: string;
}

/** 表清单项（GET /datasources/:id/tables，C1.2 冻结，SSOT §10.1 字段名为准，P1-5 修复） */
export interface TableListItem {
  id: number;
  name: string;
  /** 模式名（0011；问数 SQL 限定前缀，无则 null） */
  schema?: string | null;
  /** 展示注释 = comment_custom ?? comment_db（服务端合并） */
  comment: string;
  fields: number;
  rows: number | null;
  pk: string[];
  partition: 'month' | 'day' | null;
  updated_at?: string;
  /** 展示增强（可选，原型「标准」角标/筛选）：表内是否有字段绑定了字段标准 */
  bound?: boolean;
}

/** GET /tables/:id 包络（C1.2 冻结）：表详情 + 字段（含绑定标准名） */
export interface TableDetailRes {
  table: TableMeta;
  columns: (ColumnMeta & { standard_name?: string | null })[];
}

export interface ColumnMeta {
  id: number;
  table_id: number;
  name: string;
  type: string;
  comment_db?: string | null;
  comment_custom?: string | null;
  is_pk: 0 | 1;
  is_partition: 0 | 1;
  is_bucket: 0 | 1;
  field_standard_id?: number | null;
  updated_at?: string;
}

// ---------- C6.4 关系 ----------
export type RelationSource = 'fk' | 'inferred' | 'manual';
export type RelationStatus = 'confirmed' | 'pending';
export interface Relation {
  id: number;
  datasource_id: number;
  from_table: string;
  from_col: string;
  to_table: string;
  to_col: string;
  source: RelationSource;
  confidence: number;
  status: RelationStatus;
}

// ---------- C6.5 语义层 ----------
export interface FieldStandard {
  id: number;
  name: string;
  data_type?: string | null;
  unit?: string | null;
  value_desc?: string | null;
  dimension_standard_id?: number | null;
  description?: string | null;
  created_at?: string;
  /** 展示增强（可选，原型「引用字段」计数列） */
  refs?: number;
}
export interface DimensionStandard {
  id: number;
  name: string;
  description?: string | null;
  values?: DimensionValue[];
  created_at?: string;
}
export interface DimensionValue {
  id: number;
  dimension_id: number;
  value: string;
  label?: string | null;
  sort?: number | null;
}
export interface MetricStandard {
  id: number;
  name: string;
  expr: string;
  filter?: string | null; // D-19 保留列名
  base_tables?: string | null;
  description?: string | null;
  created_at?: string;
}

// ---------- C6.6/C6.8 建议 ----------
export type SuggestionKind = 'comment' | 'std_bind' | 'relation' | 'metric';
export type SuggestionStatus = 'pending' | 'accepted' | 'ignored';
export interface AiSuggestion {
  id: number;
  datasource_id: number;
  kind: SuggestionKind;
  payload_json: string; // 四形态见 C6.8；payload 内可含 reason/evidence（方案 v1.1 §4）
  confidence: number;
  status: SuggestionStatus;
  run_id?: number | null; // 产出批次（0007 增量）
}

// ---------- AI 补全任务（方案 v1.1 §8，新增契约） ----------
export type CompletionScopeType = 'datasource' | 'table' | 'relations';
export type CompletionKind = 'comment' | 'relation' | 'std_bind';
export type AiRunStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

export interface AiRun {
  id: number;
  datasource_id: number;
  scope_type: CompletionScopeType;
  scope_table_id?: number | null;
  kinds: CompletionKind[];
  options: { rescan_existing?: boolean; use_sample_values?: boolean };
  status: AiRunStatus;
  progress_done: number;
  progress_total: number;
  made: Record<string, number>;
  failed: { table?: string; kind?: string; error: string }[];
  error?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
}

export interface CompletionRunReq {
  datasource_id: number;
  scope: { type: CompletionScopeType; table_id?: number; table_names?: string[] };
  kinds: CompletionKind[];
  options?: { rescan_existing?: boolean; use_sample_values?: boolean };
}

/** 泳道事件（SSE）：1..N = LLM 泳道（关系/注释/标准均 AI 生成）；progress 帧 = 全局进度 */
export interface LaneEvent {
  lane: number;
  table: string;
  phase: 'waiting' | 'analyzing' | 'validating' | 'done' | 'failed' | 'skipped' | 'run_end' | 'progress';
  line: string;
  made?: Record<string, number>;
  ms?: number;
  error?: string;
  status?: AiRunStatus; // run_end 帧
  lanes?: Record<number, { table: string; phase: string; line: string }>;
  // progress 帧字段
  done?: number;
  total?: number;
  pct?: number;
}

/** 建议队列包络（GET /suggestions，C1.3 冻结，P1-6 修复）：pending 按 kind 分组 + total */
export interface SuggestionListRes {
  groups: Partial<Record<SuggestionKind, AiSuggestion[]>>;
  total: number;
}

// ---------- C6.7 会话 ----------
export interface Session {
  id: string; // s_+ULID
  title: string;
  datasource_id?: number | null;
  pinned: 0 | 1;
  archived: 0 | 1;
  summary?: string | null;
  summary_upto?: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * E5 快照（GET /sessions/:id/queries，C1.1，修复回路冻结于 contracts.md C1.1-E5）：
 * 包络 `{session, items, last_seq_by_query}`——items 平铺持久化字段（含 preview/clarify_choice），
 * 续播游标集中于顶层字典；装配映射（items → MessageModel）在 api/sessions.ts `mapSnapshotItem`。
 */
export interface SnapshotItem {
  query_id: string;
  question: string;
  status: QueryStatus;
  /** 查询方言（回放 SQL 卡/错误卡展示用） */
  dialect?: Dialect;
  /** 生成契约 JSON（已解析为对象；C4.1），历史回放推理/假设来源 */
  contract?: GenerationContract | null;
  sql_final?: string | null;
  rounds?: number;
  tokens?: number;
  scan_rows?: number | null;
  data_max_time?: string | null;
  duration_ms?: number | null;
  result_unverified: boolean;
  clarify_choice?: string | null;
  /** 历史结果预览（result_preview 表）； hydrate 时映射进 MessageModel.result（P1-1） */
  preview?: { columns: ResultColumn[]; rows: unknown[][]; truncated: boolean } | null;
  /** D03 崩溃恢复标记（R-3）：后端恢复路径产出，前端据此亮「已自动恢复」绿横幅 */
  recovered?: boolean;
  /** 过程回放（query_event 重建）：刷新后阶段卡/风险/修正时间线/校验/澄清选项/错误卡可见 */
  process?: {
    stages: StageView[];
    risks: RiskItem[];
    corrections: CorrectionRoundView[];
    verify: VerifyView | null;
    clarify_options: ClarifyOption[] | null;
    error: { code: string; message: string; retryable: boolean } | null;
    /** R2：完整思考全文回放（reasoning_full 持久事件重建；旧快照无此字段，防御可选） */
    reasoning_full?: { round: number; text: string }[] | null;
    /** R2 收尾：逐轮 SQL 回放（sql 事件按 round 收集；key 为字符串数字，装配时转 Number） */
    sql_rounds?: Record<string, string> | null;
    /** R2 收尾：llm_retry 回放（轨迹重试小条） */
    retries?: LlmRetryView[] | null;
    /** R2 收尾：inbox_claimed 回放（轨迹「已并入引导/注入」小条） */
    inbox_notes?: InboxNoteView[] | null;
    /** R2 收尾：meta 事件里最后一条非空 hit_tables（信封组装条目展开用） */
    hit_tables?: string[] | null;
  } | null;
  created_at?: string;
}

export interface SessionSnapshot {
  session: Session;
  items: SnapshotItem[];
  /** query_id → 已持久化最大 seq（装配-续播接缝，SSE Last-Event-ID 依据） */
  last_seq_by_query: Record<string, number>;
}

// ---------- C6.9 查询 ----------
export type QueryStatus =
  | 'running'
  | 'waiting_clarify'
  | 'waiting_confirm'
  | 'done'
  | 'failed'
  | 'stopped';

export interface QueryRecord {
  id: string; // q_+ULID = client_request_id = thread_id
  session_id: string;
  datasource_id: number;
  dialect: Dialect;
  question: string;
  contract_json?: string | null;
  sql_final?: string | null;
  status: QueryStatus;
  rounds?: number;
  tokens?: number;
  scan_rows?: number | null;
  data_max_time?: string | null;
  duration_ms?: number | null;
  result_unverified: 0 | 1;
  clarify_choice?: string | null;
  created_at: string;
}

export interface ResultColumn {
  name: string;
  type: string;
}
export interface ResultPreview {
  query_id: string;
  columns: ResultColumn[];
  rows: unknown[][];
  truncated: boolean;
}

// ---------- C3.2 SSE 十六事件（逐字） ----------
export type StageName =
  | 'supply'
  | 'generate'
  | 'validate'
  | 'clarify'
  | 'confirm'
  | 'execute'
  | 'verify'
  | 'correct';

export interface RiskItem {
  rule_id: string; // R01-R15
  level: 'red' | 'yellow';
  message: string;
}
export interface Assumption {
  text: string;
  impact: 'high' | 'low';
  kind?: 'time' | 'caliber' | 'relation' | 'enum' | 'other';
}
export interface ClarifyOption {
  label: string;
  expr: string;
}

export type SseEvent =
  | { type: 'stage'; stage: StageName; round: number; status: 'start' | 'done'; elapsed_ms?: number }
  // gen_delta：生成过程实时流（不落库、无 seq，替换式渲染；契约到达后清空）
  // R2：stage 扩到 'correct'（修正轮也有实时思考流；stage 本就是 string 类型，天然兼容）
  | { type: 'gen_delta'; stage: string; round: number; kind: 'reasoning' | 'content'; text: string }
  // R2 新持久事件 reasoning_full：完整思考全文（generate 轮 round=0，修正轮 round=1..N 各一条）
  // truncated/preview：历史事件被 16KB 截断标记时的回退字段（防御可选）
  | { type: 'reasoning_full'; round: number; text: string; truncated?: boolean; preview?: string }
  | { type: 'contract'; contract: GenerationContract }
  | { type: 'sql'; sql: string; round: number }
  | { type: 'risk'; risks: RiskItem[] }
  | { type: 'clarify_required'; options: ClarifyOption[] }
  | { type: 'confirm_required'; risks: RiskItem[]; est_rows: number }
  | { type: 'correction'; round: number; sql: string; error: string; fingerprint: string }
  | { type: 'meta'; phase: 'post_supply' | 'pre_done'; hit_tables: string[]; route: { generate: string; fix: string }; tokens: number }
  | { type: 'result_meta'; rows: number; scan_rows: number; duration_ms: number; data_max_time: string; truncated: boolean }
  | { type: 'result'; columns: ResultColumn[]; rows: unknown[][] }
  | { type: 'verify'; checks: { name: string; passed: boolean }[]; passed: boolean; fallback_used: boolean }
  | { type: 'followups'; items: string[] }
  // DSH 对齐增量（契约 3）：
  // llm_retry —— 模型调用重试（500/429/超时/传输错误退避），渲染为阶段卡内灰字小条
  | { type: 'llm_retry'; attempt: number; delay_ms: number; failure_class: string; route?: string }
  // inbox_claimed —— 运行中输入（steer/inject）已被领取生效，渲染为系统小提示
  | { type: 'inbox_claimed'; items: { kind: 'steer' | 'inject'; content: string }[] }
  // followup_started —— 排队的下一轮已自动开始，前端把流切换到新 query_id
  | { type: 'followup_started'; query_id: string }
  | { type: 'stopped'; reason: 'user_stop' | 'user_cancel' }
  // F8：done.status 补 'stopped'——后端已对 stopped 跳过 done 帧（graph/runner
  // ._finalize），此处补全仅为防御：历史事件回放/未来演进若出现该帧，映射不失真
  | { type: 'done'; query_id: string; status: 'done' | 'failed' | 'stopped'; unverified: boolean }
  | { type: 'error'; code: string; message: string; retryable: boolean };

/** 连接级控制帧（无 seq 无 id 不入表，不参与 seq 幂等） */
export const CONTROL_STREAM_RESUMED = 'stream_resumed';

// ---------- C4.1 生成契约 ----------
export interface GenerationContract {
  reasoning: string;
  assumptions: Assumption[];
  need_tables?: string[];
  sql: string;
  followups?: string[];
  /** kind=meta：库概况直通答案（reasoning 即回答正文，无 SQL） */
  kind?: 'meta';
}

// ---------- MessageModel（S1-S6 同构装配态，对齐 C5 QueryState） ----------
export interface CorrectionRoundView {
  round: number;
  sql: string;
  error: string;
  fingerprint: string;
}
export interface VerifyView {
  checks: { name: string; passed: boolean }[];
  passed: boolean;
  fallback_used: boolean;
}
export interface ResultMetaView {
  rows: number;
  scan_rows: number;
  duration_ms: number;
  data_max_time: string;
  truncated: boolean;
}
export interface StageView {
  stage: StageName;
  round: number;
  status: 'start' | 'done';
  elapsed_ms?: number;
}

/** llm_retry 装配视图（契约 3）：模型调用退避重试提示条 */
export interface LlmRetryView {
  attempt: number;
  delay_ms: number;
  failure_class: string;
  route?: string;
}

/** inbox_claimed 装配视图（契约 3）：运行中输入被领取生效 */
export interface InboxNoteView {
  kind: 'steer' | 'inject';
  content: string;
}

export interface MessageModel {
  query_id: string;
  session_id: string;
  question: string;
  datasource_id?: number;
  dialect?: Dialect;
  ds_mode?: DsMode;
  status: QueryStatus;
  stages: StageView[];
  reasoning?: string;
  /** 库概况直通答案正文（contract.kind=meta → 专用回答卡渲染，历史回放同映射） */
  answer?: string;
  assumptions: Assumption[];
  sql?: string; // 与 confirm_gate 展示、execute 逐字同串（所验即所执）
  contractUnstructured?: boolean; // E2002 降级：正则提取 ```sql 块
  risks: RiskItem[];
  clarify_options?: ClarifyOption[];
  clarify_choice?: string | null;
  est_rows?: number;
  corrections: CorrectionRoundView[];
  resultMeta?: ResultMetaView;
  result?: ResultPreview | null;
  verify?: VerifyView;
  followups: string[];
  meta?: { hit_tables: string[]; route?: { generate: string; fix: string }; tokens: number };
  /** 生成中实时预览（gen_delta 装配；contract/error/done 到达即清空，不入快照） */
  genPreview?: { reasoning: string; content: string };
  /** R2：完整思考全文（reasoning_full 持久事件装配，按 round 存；generate=0，修正=1..N） */
  reasoningFull?: { round: number; text: string }[];
  /** R2 前端装配辅助：各轮 SQL（sql 事件按 round 记录；轨迹「修正第N轮」差异概览用，不入快照） */
  sqlRounds?: Record<number, string>;
  /** 历史段（澄清重跑等再生成的旧轮产物，时间线展示：默认折叠置于上方，不被覆盖丢失） */
  history?: { reasoning?: string; assumptions: Assumption[]; sql?: string }[];
  /** 排队中（契约 1：会话忙时 POST /queries 返回 queued 回执；当前轮终态后自动开轮）。
   *  前端装配态字段，不入快照：queued 期间 status 保持 running 但不算活动轮（渲染排队小条、不参与 steer 目标选择） */
  queued?: boolean;
  /** 模型重试记录（llm_retry 事件装配，StageCard 内灰字小条） */
  retries?: LlmRetryView[];
  /** 运行中输入被领取（inbox_claimed 事件装配，渲染「已并入引导：xxx」系统小提示） */
  inboxNotes?: InboxNoteView[];
  /** followup_started 装配：排队下一轮的新 query_id（useAssembly 据此给新轮挂流） */
  followup_started?: string | null;
  error?: { code: string; message: string; retryable: boolean };
  stopped?: 'user_stop' | 'user_cancel';
  unverified: boolean;
  created_at?: string;
}

// ---------- C6.11 样例 ----------
export type SampleStatus = 'on' | 'stale' | 'off';
export interface Sample {
  id: number;
  datasource_id: number;
  question: string;
  question_norm?: string; // 小写/去空白/去标点（服务端归一化）
  sql: string;
  status: SampleStatus;
  source?: string;
  semantic_rev?: string;
  validated_at?: string | null;
  stale_reason?: string | null;
  hit_count?: number;
  created_at?: string;
}

// ---------- C6.12 提供商 ----------
export interface Provider {
  id: number;
  name: string;
  type: string; // local | cloud …
  protocol: string; // openai_chat
  base_url: string;
  models: string[]; // models_json 解析后
  enabled: 0 | 1;
  created_at?: string;
}

// ---------- C7 设置（键值全表，40+ 键；此处为前端消费的子集类型） ----------
export interface SettingsMap {
  'route.generate': string;
  'route.fix': string;
  'route.ai': string;
  'query.auto_limit': number;
  'query.timeout_s': number;
  'query.write_confirm': boolean;
  'query.ch_readonly': boolean;
  'query.max_fix_rounds': number;
  'query.pool_size': number;
  'query.explain_timeout_s': number;
  'query.explain_scan_warn_rows': number;
  'query.ch_client_timeout_s': number;
  'schema.mode': string; // auto|full|retrieval
  'schema.budget_pct': number;
  'schema.vector': string;
  'schema.enum_top_n': number;
  'schema.embed_model': string;
  'schema.embed_dim': number;
  'prompt.system': string;
  'feature.clarify_gate': boolean;
  'feature.verify_rules': boolean;
  'feature.verify_llm': boolean;
  'feature.followups': boolean;
  'feature.partition_hint': boolean;
  'sample.fewshot_n': number;
  'sample.cache': boolean;
  'sample.auto_stale': boolean;
  'memory.enabled': boolean;
  'memory.max_tokens': number;
  'memory.consolidate_min_gap_min': number;
  'memory.inject': boolean;
  'session.compact_msgs': number;
  'session.keep_msgs': number;
  'backup.dir': string;
  'backup.keep': number;
  'backup.encrypt': boolean;
  'export.dir': string;
  'export.ttl_h': number;
  'export.max_total_mb': number;
  'server.token'?: string;
  'server.auth'?: boolean;
  'server.port': number;
  'sse.heartbeat_s': number;
  'sse.event_payload_kb': number;
  'ai.completion_workers': number; // 方案 v1.1 §3.1：补全泳道数 1–6
  'ai.sample_values_allowed': boolean; // 方案 v1.1 §6：采样值出境门控（默认 false）
  [key: string]: unknown;
}

/** C4.2 组装预览响应 */
export interface PromptPreview {
  segments: { name: string; text: string; tokens: number }[];
  total_tokens: number;
  budget: { ctx: number; schema_pct: number; output_reserved: number };
}

// ---------- C1.6 记忆 ----------
export interface MemoryItem {
  id: number;
  scope: 'global' | 'datasource';
  datasource_id?: number | null;
  topic: string;
  content: string;
  enabled: 0 | 1;
  token_est?: number;
}
export interface MemoryLogItem {
  id: number;
  kind: 'caliber' | 'correction' | 'quirk' | 'preference';
  fact: string;
  datasource_id?: number | null;
  source_query_id?: string | null;
  created_at?: string;
}

// ---------- 备份 ----------
export interface BackupFile {
  name: string;
  size: number;
  created_at: string;
  encrypted?: boolean; // .enc 后缀（D08）
}

// ---------- C1.2 杂项 ----------
export interface DsTestResult {
  version: string;
  readonly: boolean;
}
export interface DdlPreview {
  tables: { name: string; comment?: string; columns: { name: string; type: string }[] }[];
  errors: { line: number; message: string }[];
}
export interface GraphData {
  nodes: { table: string; comment?: string | null }[];
  edges: Relation[];
}
/**
 * D-18 graph_layout 摆位（C6.17；C1.2 冻结，P1-4 修复）：
 * 端点 GET/PUT `/datasources/:id/graph/layout`；GET 响应 `{datasource_id, node_positions, updated_at}`，
 * PUT 请求 `{node_positions}`；本类型 = node_positions 值（表名 → 坐标）。
 */
export type GraphLayout = Record<string, { x: number; y: number }>;

/** 备份列表包络（GET /backup/list，C1.6 冻结，P1-10 修复）：restore_hint 为 FR-C8 恢复提示原文 */
export interface BackupListRes {
  items: BackupFile[];
  restore_hint: string;
}
