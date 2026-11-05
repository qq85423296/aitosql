/**
 * mock.ts —— 后端未就绪期的本地 mock（plan §5）。
 * 架构守则（修复回路新增）：mock 数据形状必须与 contracts.md 冻结契约/后端真实包络一致，
 * 禁止按「前端期望形状」自创——本轮已将 E5 快照 / settings / graph layout / tables /
 * suggestions / memory toggle / backup 七处对齐后端真实包络（P0-1/P1-3/4/5/6/8/10）。
 * 启用方式（P0-4）：运行时开关 `?mock=1` 或 localStorage `aitosql.mock=1`；生产构建不打包本模块。
 */
import type { RequestOptions } from './client';
import type {
  AiSuggestion,
  BackupFile,
  ColumnMeta,
  CompletionRunReq,
  Datasource,
  DdlPreview,
  FieldStandard,
  LaneEvent,
  MemoryItem,
  MemoryLogItem,
  MetricStandard,
  PromptPreview,
  Provider,
  Relation,
  Sample,
  Session,
  SessionSnapshot,
  SnapshotItem,
  SettingsMap,
  SuggestionKind,
  DimensionStandard,
  GraphData,
  TableListItem,
  TableMeta,
} from './types';
import { newQueryId, newSessionId } from '../lib/ulid';

/** 补全任务 mock 序号（/ai/completion-runs 与模拟器共用） */
let mockRunSeq = 9000;

/** 补全任务历史（GET /ai/completion-runs?datasource_id= 过滤下拉用） */
const mockRunHistory: import('./types').AiRun[] = [];

/** 显式取消标记（2026-11-05 工作区语义收敛）：close() 仅断流 ≠ 取消；唯一取消通道是 cancel 端点 */
const mockRunCancels = new Set<number>();

// ---------- fixtures（形状 = 原型 data.json ↔ C6 表结构） ----------
const now = () => new Date().toISOString();

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();
const daysAgo = (d: number) => hoursAgo(d * 24);

let datasources: Datasource[] = [
  { id: 1, name: '生产分析库', dialect: 'doris', mode: 'online', host: '192.168.1.10', port: 9030, db_name: 'analytics', username: 'reader', version: '2.1.5', table_count: 87, field_count: 612, last_ingest_at: hoursAgo(2), created_at: daysAgo(30) },
  { id: 2, name: '业务主库', dialect: 'mysql', mode: 'online', host: '127.0.0.1', port: 3306, db_name: 'biz', username: 'reader', version: '8.0.36', table_count: 42, field_count: 388, last_ingest_at: daysAgo(1), created_at: daysAgo(28) },
  { id: 3, name: '日志数仓', dialect: 'clickhouse', mode: 'online', host: '10.0.0.8', port: 8123, db_name: 'logs', username: 'ro', version: '24.3', table_count: 156, field_count: 2041, last_ingest_at: daysAgo(3), created_at: daysAgo(20) },
  { id: 4, name: '核心账务库', dialect: 'mysql', mode: 'offline', db_name: 'finance', table_count: 18, field_count: 126, last_ingest_at: daysAgo(7), created_at: daysAgo(15) },
];

/** 契约 5：数据源口径说明（每数据源一份文本；mock 内存态，PUT 覆盖） */
const dsInstructions = new Map<number, string>([
  [1, '销售额默认含税口径；统计排除 is_test=1 与 is_deleted=1；时间口径默认 created_at。'],
]);

let sessions: Session[] = [
  { id: 's_01J8X4K2ME000000000000001', title: '上个月各渠道销售额环比', datasource_id: 1, pinned: 1, archived: 0, created_at: now(), updated_at: now() },
  { id: 's_01J8X4K2ME000000000000002', title: '退款率异常排查', datasource_id: 1, pinned: 0, archived: 0, created_at: now(), updated_at: now() },
  { id: 's_01J8X4K2ME000000000000003', title: '旧·渠道复盘（归档）', datasource_id: 1, pinned: 0, archived: 1, created_at: now(), updated_at: now() },
];

/** 原型 7 表（生产分析库）：orders / order_items / users / channels / products / refunds / inventory_daily */
let tables: TableMeta[] = [
  { id: 11, datasource_id: 1, name: 'orders', comment_db: '订单主表', comment_custom: '订单主表', row_estimate: 12480392, field_count: 8, partition_type: 'month', updated_at: daysAgo(2) },
  { id: 12, datasource_id: 1, name: 'order_items', comment_db: '订单明细', comment_custom: null, row_estimate: 31204118, field_count: 6, partition_type: 'month', updated_at: daysAgo(12) },
  { id: 13, datasource_id: 1, name: 'users', comment_db: '用户表', comment_custom: null, row_estimate: 842103, field_count: 5, partition_type: null, updated_at: daysAgo(15) },
  { id: 14, datasource_id: 1, name: 'channels', comment_db: '渠道维度表', comment_custom: null, row_estimate: 12, field_count: 3, partition_type: null, updated_at: daysAgo(18) },
  { id: 15, datasource_id: 1, name: 'products', comment_db: '商品表', comment_custom: null, row_estimate: 24512, field_count: 5, partition_type: null, updated_at: daysAgo(3) },
  { id: 16, datasource_id: 1, name: 'refunds', comment_db: null, comment_custom: null, row_estimate: 312440, field_count: 4, partition_type: 'month', updated_at: daysAgo(1) },
  { id: 17, datasource_id: 1, name: 'inventory_daily', comment_db: '库存日报', comment_custom: null, row_estimate: 1204551, field_count: 4, partition_type: 'day', updated_at: daysAgo(9) },
];

let columns: ColumnMeta[] = [
  // orders（原型 COLS 八字段，含标准绑定/分区/分桶）
  { id: 111, table_id: 11, name: 'id', type: 'bigint', comment_db: '订单ID', comment_custom: null, is_pk: 1, is_partition: 0, is_bucket: 1 },
  { id: 112, table_id: 11, name: 'user_id', type: 'bigint', comment_db: '下单用户', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 6 },
  { id: 113, table_id: 11, name: 'channel_id', type: 'int', comment_db: '渠道', comment_custom: '渠道（推测关联）', is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 3 },
  { id: 114, table_id: 11, name: 'amount', type: 'decimal(10,2)', comment_db: '含税金额', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 1 },
  { id: 115, table_id: 11, name: 'status', type: 'varchar', comment_db: '订单状态', comment_custom: 'paid/shipped/done/closed', is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 4 },
  { id: 116, table_id: 11, name: 'is_test', type: 'tinyint', comment_db: '测试单标记', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 5 },
  { id: 117, table_id: 11, name: 'is_deleted', type: 'tinyint', comment_db: '软删除', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 5 },
  { id: 118, table_id: 11, name: 'created_at', type: 'datetime', comment_db: '下单时间', comment_custom: null, is_pk: 0, is_partition: 1, is_bucket: 0, field_standard_id: 7 },
  // order_items
  { id: 121, table_id: 12, name: 'id', type: 'bigint', comment_db: '明细ID', comment_custom: null, is_pk: 1, is_partition: 0, is_bucket: 0 },
  { id: 122, table_id: 12, name: 'order_id', type: 'bigint', comment_db: '所属订单', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 1 },
  { id: 123, table_id: 12, name: 'product_id', type: 'bigint', comment_db: '商品', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0 },
  { id: 124, table_id: 12, name: 'price', type: 'decimal(10,2)', comment_db: '成交单价', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 1 },
  { id: 125, table_id: 12, name: 'qty', type: 'int', comment_db: '件数', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 2 },
  { id: 126, table_id: 12, name: 'created_at', type: 'datetime', comment_db: '创建时间', comment_custom: null, is_pk: 0, is_partition: 1, is_bucket: 0, field_standard_id: 7 },
  // users
  { id: 131, table_id: 13, name: 'id', type: 'bigint', comment_db: '用户ID', comment_custom: null, is_pk: 1, is_partition: 0, is_bucket: 1 },
  { id: 132, table_id: 13, name: 'city', type: 'varchar', comment_db: null, comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0 },
  { id: 133, table_id: 13, name: 'created_at', type: 'datetime', comment_db: '注册时间', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 7 },
  // channels
  { id: 141, table_id: 14, name: 'id', type: 'int', comment_db: '渠道ID', comment_custom: null, is_pk: 1, is_partition: 0, is_bucket: 0 },
  { id: 142, table_id: 14, name: 'channel_name', type: 'varchar(32)', comment_db: '渠道名', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0 },
  // products
  { id: 151, table_id: 15, name: 'id', type: 'bigint', comment_db: '商品ID', comment_custom: null, is_pk: 1, is_partition: 0, is_bucket: 0 },
  { id: 152, table_id: 15, name: 'sku', type: 'varchar', comment_db: 'SKU', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0 },
  { id: 153, table_id: 15, name: 'name', type: 'varchar', comment_db: '商品名', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0 },
  { id: 154, table_id: 15, name: 'category_id', type: 'int', comment_db: null, comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0 },
  { id: 155, table_id: 15, name: 'price', type: 'decimal(10,2)', comment_db: '标价', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0, field_standard_id: 1 },
  // refunds（缺注释表演示）
  { id: 161, table_id: 16, name: 'id', type: 'bigint', comment_db: '退款ID', comment_custom: null, is_pk: 1, is_partition: 0, is_bucket: 0 },
  { id: 162, table_id: 16, name: 'order_id', type: 'bigint', comment_db: '关联订单', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 1 },
  { id: 163, table_id: 16, name: 'amount', type: 'decimal(10,2)', comment_db: null, comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0 },
  { id: 164, table_id: 16, name: 'created_at', type: 'datetime', comment_db: null, comment_custom: null, is_pk: 0, is_partition: 1, is_bucket: 0 },
  // inventory_daily
  { id: 171, table_id: 17, name: 'dt', type: 'date', comment_db: '日期', comment_custom: null, is_pk: 1, is_partition: 1, is_bucket: 0 },
  { id: 172, table_id: 17, name: 'sku_id', type: 'bigint', comment_db: null, comment_custom: null, is_pk: 1, is_partition: 0, is_bucket: 1 },
  { id: 173, table_id: 17, name: 'qty', type: 'int', comment_db: '库存量', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0 },
  { id: 174, table_id: 17, name: 'warehouse', type: 'varchar', comment_db: '仓库', comment_custom: null, is_pk: 0, is_partition: 0, is_bucket: 0 },
];

/** 原型 6 关系：2 推测待确认 + 2 推测已确认 + 2 手动 */
let relations: Relation[] = [
  { id: 21, datasource_id: 1, from_table: 'orders', from_col: 'channel_id', to_table: 'channels', to_col: 'id', source: 'inferred', confidence: 0.85, status: 'pending' },
  { id: 22, datasource_id: 1, from_table: 'orders', from_col: 'user_id', to_table: 'users', to_col: 'id', source: 'inferred', confidence: 0.92, status: 'confirmed' },
  { id: 23, datasource_id: 1, from_table: 'order_items', from_col: 'order_id', to_table: 'orders', to_col: 'id', source: 'inferred', confidence: 0.97, status: 'confirmed' },
  { id: 24, datasource_id: 1, from_table: 'order_items', from_col: 'product_id', to_table: 'products', to_col: 'id', source: 'manual', confidence: 1, status: 'confirmed' },
  { id: 25, datasource_id: 1, from_table: 'refunds', from_col: 'order_id', to_table: 'orders', to_col: 'id', source: 'inferred', confidence: 0.88, status: 'pending' },
  { id: 26, datasource_id: 1, from_table: 'inventory_daily', from_col: 'sku_id', to_table: 'products', to_col: 'id', source: 'manual', confidence: 1, status: 'confirmed' },
];

/** 原型 7 字段标准（refs=引用字段数展示） */
let fieldStandards: FieldStandard[] = [
  { id: 1, name: '金额（含税）', data_type: 'decimal(10,2)', unit: '元', value_desc: '含税成交金额，退款为负', dimension_standard_id: null, description: '财务口径统一含税', refs: 3, created_at: now() },
  { id: 2, name: '数量', data_type: 'int', unit: '件', value_desc: '商品件数', dimension_standard_id: null, description: null, refs: 2, created_at: now() },
  { id: 3, name: '渠道标识', data_type: 'int', unit: '—', value_desc: '订单来源渠道', dimension_standard_id: 1, description: null, refs: 1, created_at: now() },
  { id: 4, name: '订单状态', data_type: 'varchar', unit: '—', value_desc: '生命周期状态机', dimension_standard_id: 2, description: null, refs: 1, created_at: now() },
  { id: 5, name: '是否标记', data_type: 'tinyint', unit: '—', value_desc: '0/1 布尔标记', dimension_standard_id: 3, description: null, refs: 2, created_at: now() },
  { id: 6, name: '用户标识', data_type: 'bigint', unit: '—', value_desc: '全局用户ID', dimension_standard_id: null, description: null, refs: 2, created_at: now() },
  { id: 7, name: '时间戳（创建）', data_type: 'datetime', unit: '—', value_desc: '记录创建时间', dimension_standard_id: null, description: null, refs: 4, created_at: now() },
];
/** 原型 3 维度标准（含枚举值） */
let dimensions: DimensionStandard[] = [
  { id: 1, name: '渠道', description: '订单来源渠道', created_at: now(), values: [
    { id: 1, dimension_id: 1, value: '1', label: '自然流量', sort: 1 },
    { id: 2, dimension_id: 1, value: '2', label: 'SEM投放', sort: 2 },
    { id: 3, dimension_id: 1, value: '3', label: '短视频', sort: 3 },
    { id: 4, dimension_id: 1, value: '4', label: '老客召回', sort: 4 },
  ] },
  { id: 2, name: '订单状态', description: '订单生命周期', created_at: now(), values: [
    { id: 5, dimension_id: 2, value: 'paid', label: '已支付', sort: 1 },
    { id: 6, dimension_id: 2, value: 'shipped', label: '已发货', sort: 2 },
    { id: 7, dimension_id: 2, value: 'done', label: '已完成', sort: 3 },
    { id: 8, dimension_id: 2, value: 'closed', label: '已关闭', sort: 4 },
  ] },
  { id: 3, name: '是否', description: '通用布尔维度', created_at: now(), values: [
    { id: 9, dimension_id: 3, value: '0', label: '否', sort: 1 },
    { id: 10, dimension_id: 3, value: '1', label: '是', sort: 2 },
  ] },
];
/** 原型 5 指标口径 */
let metrics: MetricStandard[] = [
  { id: 1, name: 'GMV（成交总额）', expr: 'SUM(amount)', filter: "status IN ('paid','shipped','done') AND is_test = 0", base_tables: 'orders', description: '含税成交总额', created_at: now() },
  { id: 2, name: '有效订单量', expr: 'COUNT(*)', filter: "is_test = 0 AND is_deleted = 0 AND status <> 'closed'", base_tables: 'orders', description: '排除测试/删除/关闭单', created_at: now() },
  { id: 3, name: '客单价', expr: 'GMV / 有效订单量', filter: '—（复合指标）', base_tables: 'orders', description: '每单平均成交额', created_at: now() },
  { id: 4, name: '退款率', expr: 'COUNT(DISTINCT refunds.order_id) / 有效订单量', filter: '退款以申请时间计', base_tables: 'refunds + orders', description: '分子为 30 天内退款单', created_at: now() },
  { id: 5, name: '环比增长率', expr: '(本期 − LAG(本期)) / LAG(本期)', filter: '—（窗口模板）', base_tables: '通用', description: '按 PARTITION 分组、时间排序', created_at: now() },
];

/** 原型 12 条 AI 补全建议：5 注释 · 4 标准绑定 · 2 关系确认 · 1 指标归纳 */
let suggestions: AiSuggestion[] = [
  { id: 31, datasource_id: 1, kind: 'comment', payload_json: JSON.stringify({ table: 'refunds', suggested: '退款表' }), confidence: 0.91, status: 'pending' },
  { id: 32, datasource_id: 1, kind: 'comment', payload_json: JSON.stringify({ table: 'refunds', column: 'amount', suggested: '退款金额（负数）' }), confidence: 0.88, status: 'pending' },
  { id: 33, datasource_id: 1, kind: 'comment', payload_json: JSON.stringify({ table: 'refunds', column: 'created_at', suggested: '退款申请时间' }), confidence: 0.86, status: 'pending' },
  { id: 34, datasource_id: 1, kind: 'comment', payload_json: JSON.stringify({ table: 'inventory_daily', column: 'sku_id', suggested: 'SKU 标识' }), confidence: 0.9, status: 'pending' },
  { id: 35, datasource_id: 1, kind: 'comment', payload_json: JSON.stringify({ table: 'products', column: 'category_id', suggested: '类目标识' }), confidence: 0.82, status: 'pending' },
  { id: 36, datasource_id: 1, kind: 'std_bind', payload_json: JSON.stringify({ table: 'refunds', column: 'amount', standard: '金额（含税）' }), confidence: 0.9, status: 'pending' },
  { id: 37, datasource_id: 1, kind: 'std_bind', payload_json: JSON.stringify({ table: 'order_items', column: 'price', standard: '金额（含税）' }), confidence: 0.87, status: 'pending' },
  { id: 38, datasource_id: 1, kind: 'std_bind', payload_json: JSON.stringify({ table: 'inventory_daily', column: 'qty', standard: '数量' }), confidence: 0.85, status: 'pending' },
  { id: 39, datasource_id: 1, kind: 'std_bind', payload_json: JSON.stringify({ table: 'users', column: 'city', standard: '城市', note: '建议新建维度标准（枚举约 40 个）' }), confidence: 0.62, status: 'pending' },
  { id: 40, datasource_id: 1, kind: 'relation', payload_json: JSON.stringify({ from: 'refunds.order_id', to: 'orders.id' }), confidence: 0.88, status: 'pending' },
  { id: 41, datasource_id: 1, kind: 'relation', payload_json: JSON.stringify({ from: 'orders.channel_id', to: 'channels.id' }), confidence: 0.85, status: 'pending' },
  { id: 42, datasource_id: 1, kind: 'metric', payload_json: JSON.stringify({ name: '复购率', expr: '90天内下单≥2次用户 / 总下单用户', filter: '' }), confidence: 0.71, status: 'pending' },
];

let samples: Sample[] = [
  { id: 41, datasource_id: 1, question: '上个月各渠道销售额环比', question_norm: '上个月各渠道销售额环比', sql: "WITH monthly AS (SELECT ...) SELECT ... FROM monthly", status: 'on', source: 'manual', validated_at: now(), hit_count: 6, created_at: now() },
  { id: 42, datasource_id: 1, question: '各城市退款率 TOP10', question_norm: '各城市退款率top10', sql: 'SELECT city, ... ', status: 'stale', source: 'manual', stale_reason: '字段 refunds.city 已改名为 region', validated_at: now(), hit_count: 2, created_at: now() },
  { id: 43, datasource_id: 1, question: '昨日 DAU', question_norm: '昨日dau', sql: 'SELECT COUNT(DISTINCT user_id) ...', status: 'off', source: 'manual', created_at: now() },
];

let providers: Provider[] = [
  { id: 1, name: '本地 Ollama', type: 'local', protocol: 'openai_chat', base_url: 'http://127.0.0.1:11434/v1', models: ['qwen3-32b', 'qwen2.5-14b', 'bge-m3'], enabled: 1, created_at: now() },
  { id: 2, name: '云端 OpenAI 兼容', type: 'cloud', protocol: 'openai_chat', base_url: 'https://api.example.com/v1', models: ['gpt-x'], enabled: 0, created_at: now() },
];

let settings: SettingsMap = {
  // 路由值格式（C7 冻结）：{provider_id}:{model}（P0-3）
  'route.generate': '1:qwen3-32b',
  'route.fix': '1:qwen3-32b',
  'route.ai': '1:qwen2.5-14b',
  'query.auto_limit': 1000,
  'query.timeout_s': 30,
  'query.write_confirm': true,
  'query.ch_readonly': true,
  'query.max_fix_rounds': 3,
  'query.pool_size': 2,
  'query.explain_timeout_s': 5,
  'query.explain_scan_warn_rows': 5000000,
  'query.ch_client_timeout_s': 35,
  'schema.mode': 'auto',
  'schema.budget_pct': 60,
  'schema.vector': 'auto',
  'schema.enum_top_n': 8,
  'schema.embed_model': 'BAAI/bge-small-zh-v1.5',
  'schema.embed_dim': 512,
  'prompt.system': '你是 AItosql 的 SQL 生成器（SSOT §8.1 原文可由后端覆盖）…',
  'feature.clarify_gate': true,
  'feature.verify_rules': true,
  'feature.verify_llm': false,
  'feature.followups': true,
  'feature.partition_hint': true,
  'sample.fewshot_n': 3,
  'sample.cache': true,
  'sample.auto_stale': true,
  'memory.enabled': true,
  'memory.max_tokens': 4000,
  'memory.consolidate_min_gap_min': 30,
  'memory.inject': true,
  'ai.completion_workers': 3,
  'ai.sample_values_allowed': false,
  'session.compact_msgs': 40,
  'session.keep_msgs': 6,
  'backup.dir': 'data/backups/',
  'backup.keep': 10,
  'backup.encrypt': false,
  'export.dir': 'data/exports/',
  'export.ttl_h': 24,
  'export.max_total_mb': 2048,
  'server.port': 8321,
  'sse.heartbeat_s': 15,
  'sse.event_payload_kb': 16,
};

let memoryLedger: MemoryItem[] = [
  { id: 51, scope: 'global', topic: '口径', content: '销售额默认含税口径', enabled: 1, token_est: 120 },
  { id: 52, scope: 'datasource', datasource_id: 1, topic: '偏好', content: '环比默认自然月', enabled: 1, token_est: 80 },
  { id: 53, scope: 'global', topic: '怪癖', content: 'refunds.amount 为负数', enabled: 0, token_est: 60 },
];
const memoryLog: MemoryLogItem[] = [
  { id: 61, kind: 'caliber', fact: '用户确认：销售额=SUM(amount) 含税', datasource_id: 1, source_query_id: null, created_at: now() },
  { id: 62, kind: 'correction', fact: '更正假设：环比基准=上月（非去年同期）', datasource_id: 1, source_query_id: null, created_at: now() },
];

let backups: BackupFile[] = [
  { name: 'app-20261014-0400.db', size: 12_582_912, created_at: '2026-10-14T20:00:00Z' },
  { name: 'app-20261013-0400.db', size: 12_411_904, created_at: '2026-10-13T20:00:00Z' },
];

const DEMO_QID = 'q_01J8X4K2ME0000000000000A1';
const DEMO_SQL_FULL =
  "WITH monthly AS (\n  SELECT c.channel_name, DATE_TRUNC('month', o.created_at) AS month,\n    COUNT(*) AS order_cnt, SUM(o.amount) AS gmv\n  FROM orders o JOIN channels c ON o.channel_id = c.id\n  WHERE o.created_at >= '2026-08-01' AND o.created_at < '2026-10-01'\n    AND o.status IN ('paid','shipped','done') AND o.is_test = 0 AND o.is_deleted = 0\n  GROUP BY 1, 2\n)\nSELECT channel_name, month, order_cnt, gmv,\n  ROUND((gmv / LAG(gmv) OVER (PARTITION BY channel_name ORDER BY month) - 1) * 100, 1) AS gmv_mom_pct\nFROM monthly ORDER BY channel_name, month;";

/**
 * E5 快照（C1.1-E5 冻结包络）：{session, items, last_seq_by_query}——
 * items 为持久化字段平铺（contract/preview/clarify_choice），装配映射在前端 mapSnapshotItem。
 */
function demoSnapshot(sessionId: string): SessionSnapshot {
  const item: SnapshotItem = {
    query_id: DEMO_QID,
    question: '上个月各渠道的订单量和销售额，环比增长率是多少？',
    status: 'done',
    contract: {
      reasoning: '关联 orders × channels，按自然月分组，LAG 窗口算环比',
      assumptions: [
        { text: '"上个月" → 2026-09-01 ~ 09-30', impact: 'low', kind: 'time' },
        { text: '环比基准 = 2026-08', impact: 'low', kind: 'time' },
        { text: '销售额 = SUM(amount)，含税', impact: 'low', kind: 'caliber' },
        { text: 'channel_id → channels.id', impact: 'high', kind: 'relation' },
      ],
      sql: DEMO_SQL_FULL,
      followups: ['按渠道钻取到城市维度', '只看短视频渠道每日明细', '环比改成同比去年同月'],
    },
    sql_final: DEMO_SQL_FULL,
    rounds: 2,
    tokens: 3214,
    scan_rows: 842000,
    data_max_time: '2026-09-30',
    duration_ms: 1200,
    result_unverified: false,
    clarify_choice: null,
    preview: {
      columns: [
        { name: 'channel_name', type: 'varchar' },
        { name: 'month', type: 'datetime' },
        { name: 'order_cnt', type: 'bigint' },
        { name: 'gmv', type: 'decimal' },
        { name: 'gmv_mom_pct', type: 'double' },
      ],
      rows: [
        ['SEM 投放', '2026-08', 3812, '486,200.00', null],
        ['SEM 投放', '2026-09', 4207, '551,940.50', 13.5],
        ['自然流量', '2026-08', 6530, '702,118.30', null],
        ['自然流量', '2026-09', 6981, '768,002.15', 9.4],
        ['短视频', '2026-09', 1873, '228,506.75', -12.7],
        ['老客召回', '2026-09', 1356, '235,144.00', 24.9],
      ],
      truncated: false,
    },
    // R2：过程回放（含 reasoning_full 完整思考全文）——历史轮次轨迹演示
    process: {
      stages: [
        { stage: 'supply', round: 0, status: 'done', elapsed_ms: 400 },
        { stage: 'generate', round: 1, status: 'done', elapsed_ms: 900 },
        { stage: 'validate', round: 1, status: 'done', elapsed_ms: 350 },
        { stage: 'execute', round: 1, status: 'done', elapsed_ms: 750 },
        { stage: 'verify', round: 1, status: 'done', elapsed_ms: 350 },
      ],
      risks: [],
      corrections: [],
      verify: {
        checks: [
          { name: '行数非零（6 行）', passed: true },
          { name: '分区裁剪：命中分区键 created_at', passed: true },
        ],
        passed: true,
        fallback_used: false,
      },
      clarify_options: null,
      error: null,
      reasoning_full: [
        {
          round: 0,
          text: '用户问题：上个月各渠道的订单量和销售额，环比增长率。\n\n1. 口径识别：销售额默认含税（orders.amount）；「上个月」→ 2026-09-01 ~ 09-30，环比基准 2026-08（分区键 created_at 月分区裁剪）。\n2. 维度关联：渠道名在 channels.channel_name，orders.channel_id → channels.id（推测关系，置信 0.85，假设已声明）。\n3. 环比计算：channel × 月聚合后 LAG 窗口取上月，(本月/上月 − 1) × 100。\n4. 风险自查：排除 is_test=1 / is_deleted=1；8/9 月数据完整，环比可比。',
        },
      ],
      // R2 收尾：逐轮 SQL / 重试 / 引导 / 信封命中表回放（与 live 轨迹一致）
      sql_rounds: { '0': DEMO_SQL_FULL },
      retries: [{ attempt: 1, delay_ms: 2000, failure_class: 'timeout', route: '1:qwen3-32b' }],
      inbox_notes: [{ kind: 'steer', content: '环比口径改成自然月' }],
      hit_tables: ['orders', 'channels'],
    },
    created_at: now(),
  };
  return {
    session: sessions.find((s) => s.id === sessionId) ?? sessions[0],
    items: [item],
    last_seq_by_query: { [DEMO_QID]: 14 },
  };
}

const previewFixture: PromptPreview = {
  segments: [
    { name: '系统设定', text: settings['prompt.system'] as string, tokens: 312 },
    { name: '方言能力卡', text: 'doris 能力卡：支持 DATE_TRUNC / LAG；禁用 OUTFILE…', tokens: 96 },
    { name: '记忆', text: '销售额默认含税口径；环比默认自然月', tokens: 120 },
    { name: '会话摘要', text: '', tokens: 0 },
    { name: 'Schema 注入', text: 'orders(id, created_at[分区键], channel_id, amount[金额含税]…) …', tokens: 1860 },
    { name: '样例 few-shot', text: '上个月各渠道销售额环比 → WITH monthly AS …', tokens: 480 },
    { name: '指标口径', text: '复购率 = COUNT(DISTINCT o2.user_id)/COUNT(DISTINCT o1.user_id)', tokens: 88 },
    { name: '用户问题', text: '{{question}}', tokens: 28 },
  ],
  total_tokens: 3334,
  budget: { ctx: 32768, schema_pct: 60, output_reserved: 4096 },
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ---------- 路由分发 ----------
export async function mockHandle(path: string, opts: RequestOptions): Promise<unknown> {
  await sleep(60); // 模拟本地往返
  const m = opts.method ?? 'GET';
  const body = (opts.body ?? {}) as Record<string, unknown>;
  const seg = path.split('?')[0].split('/').filter(Boolean); // query 参数不参与段路由

  // /sessions
  if (seg[0] === 'sessions') {
    if (seg.length === 1) {
      if (m === 'GET')
        return [...sessions].sort(
          (a, b) => b.pinned - a.pinned || b.updated_at.localeCompare(a.updated_at),
        );
      if (m === 'POST') {
        const s: Session = {
          id: newSessionId(),
          title: (body.title as string) ?? '新会话',
          datasource_id: (body.datasource_id as number) ?? 1,
          pinned: 0,
          archived: 0,
          created_at: now(),
          updated_at: now(),
        };
        sessions.push(s);
        return s;
      }
    }
    const id = seg[1];
    if (seg.length === 2 && m === 'PATCH') {
      sessions = sessions.map((s) =>
        s.id === id
          ? {
              ...s,
              title: (body.title as string) ?? s.title,
              pinned: body.pinned == null ? s.pinned : body.pinned ? 1 : 0,
              archived: body.archived == null ? s.archived : body.archived ? 1 : 0,
              updated_at: now(),
            }
          : s,
      );
      return sessions.find((s) => s.id === id);
    }
    if (seg.length === 2 && m === 'DELETE') {
      sessions = sessions.filter((s) => s.id !== id);
      return null;
    }
    if (seg[2] === 'queries' && m === 'GET') {
      return demoSnapshot(id); // C1.1-E5 冻结包络
    }
  }

  // /datasources
  if (seg[0] === 'datasources') {
    if (seg.length === 1) {
      if (m === 'GET') return datasources;
      if (m === 'POST') {
        const d: Datasource = {
          id: Math.max(...datasources.map((x) => x.id)) + 1,
          name: body.name as string,
          dialect: body.dialect as Datasource['dialect'],
          mode: body.mode as Datasource['mode'],
          host: (body.host as string) ?? null,
          port: (body.port as number) ?? null,
          db_name: (body.db_name as string) ?? null,
          username: (body.username as string) ?? null,
          created_at: now(),
        };
        datasources.push(d);
        return d;
      }
    }
    const id = Number(seg[1]);
    if (seg.length === 2 && m === 'PATCH') return { ...datasources.find((d) => d.id === id), ...body };
    if (seg.length === 2 && m === 'DELETE') {
      datasources = datasources.filter((d) => d.id !== id);
      return null;
    }
    if (seg[2] === 'test') {
      const d = datasources.find((x) => x.id === id);
      if (!d || d.mode === 'offline')
        throw Object.assign(new Error('数据源连接失败'), { code: 'E1001' });
      return { version: d.version ?? '8.0.x', readonly: d.dialect === 'clickhouse' };
    }
    if (seg[2] === 'tables') {
      // C1.2 冻结清单形状（P1-5）：{id, name, comment, fields, rows, pk, partition, updated_at}
      // bound（展示增强）：表内是否存在已绑定字段标准的列
      return tables
        .filter((t) => t.datasource_id === id)
        .map((t): TableListItem => {
          const cols = columns.filter((c) => c.table_id === t.id);
          return {
            id: t.id,
            name: t.name,
            comment: t.comment_custom ?? t.comment_db ?? '',
            fields: cols.length || (t.field_count ?? 0),
            rows: t.row_estimate ?? null,
            pk: cols.filter((c) => c.is_pk).map((c) => c.name),
            partition: t.partition_type ?? null,
            updated_at: t.updated_at,
            bound: cols.some((c) => c.field_standard_id != null),
          };
        });
    }
    if (seg[2] === 'import-ddl' && seg.length === 3) {
      const preview: DdlPreview = {
        tables: [{ name: 'orders_snapshot', comment: '离线导入', columns: [{ name: 'id', type: 'bigint' }, { name: 'amount', type: 'decimal(12,2)' }] }],
        errors: [{ line: 12, message: '第 12 行语法不支持（已跳过）' }],
      };
      return preview;
    }
    if (seg[2] === 'import-ddl' && seg[3] === 'commit') return { imported: 1 };
    if (seg[2] === 'graph' && seg.length === 3) {
      const g: GraphData = {
        nodes: tables.filter((t) => t.datasource_id === id).map((t) => ({ table: t.name, comment: t.comment_db })),
        edges: relations.filter((r) => r.datasource_id === id),
      };
      return g;
    }
    // C1.2 冻结摆位端点（P1-4）：嵌套 /graph/layout + {node_positions} 包络
    if (seg[2] === 'graph' && seg[3] === 'layout') {
      if (m === 'GET') return { datasource_id: id, node_positions: {}, updated_at: null };
      return null;
    }
    // 契约 5：口径说明文档（GET {content} / PUT {content}）
    if (seg[2] === 'instruction') {
      if (m === 'GET') return { content: dsInstructions.get(id) ?? '' };
      if (m === 'PUT') {
        dsInstructions.set(id, String(body.content ?? ''));
        return null;
      }
    }
  }

  // /tables /columns（P1-5：无独立 /tables/:id/columns 路由，字段随 {table, columns} 包络返回）
  if (seg[0] === 'tables') {
    const id = Number(seg[1]);
    if (seg.length === 2 && m === 'GET') {
      const t = tables.find((x) => x.id === id);
      return { table: t, columns: columns.filter((c) => c.table_id === id) };
    }
    if (seg.length === 2 && m === 'PATCH') {
      tables = tables.map((t) => (t.id === id ? { ...t, ...body } : t));
      return tables.find((t) => t.id === id);
    }
    if (seg[2] === 'prompt-view') {
      const t = tables.find((x) => x.id === id);
      return {
        text: `表 ${t?.name}（${t?.comment_custom ?? t?.comment_db ?? ''}）\n` +
          columns
            .filter((c) => c.table_id === id)
            .map((c) => `  ${c.name} ${c.type}${c.is_partition ? ' [分区键]' : ''}${c.is_pk ? ' [主键]' : ''} -- ${c.comment_custom ?? c.comment_db ?? ''}`)
            .join('\n'),
      };
    }
  }
  if (seg[0] === 'columns' && m === 'PATCH') {
    const id = Number(seg[1]);
    columns = columns.map((c) => (c.id === id ? { ...c, ...body } : c));
    return columns.find((c) => c.id === id);
  }

  // /relations
  if (seg[0] === 'relations') {
    if (seg.length === 1 && m === 'GET') {
      // 支持 ?datasource_id= 过滤（relationsApi.list 必带）
      const dsId = Number((path.split('?')[1] ?? '').match(/datasource_id=(\d+)/)?.[1]);
      return Number.isFinite(dsId) && dsId > 0 ? relations.filter((r) => r.datasource_id === dsId) : relations;
    }
    if (seg.length === 1 && m === 'POST') {
      const r: Relation = {
        id: Math.max(...relations.map((x) => x.id)) + 1,
        datasource_id: body.datasource_id as number,
        from_table: body.from_table as string,
        from_col: body.from_col as string,
        to_table: body.to_table as string,
        to_col: body.to_col as string,
        source: 'manual',
        confidence: 1.0,
        status: 'confirmed',
      };
      relations.push(r);
      return r;
    }
    if (seg[2] === 'confirm') {
      relations = relations.map((r) => (r.id === Number(seg[1]) ? { ...r, status: 'confirmed' } : r));
      return relations.find((r) => r.id === Number(seg[1]));
    }
    if (m === 'DELETE') {
      relations = relations.filter((r) => r.id !== Number(seg[1]));
      return null;
    }
  }

  // /standards /dimensions /metrics
  if (seg[0] === 'standards') {
    if (seg.length === 1 && m === 'GET') return fieldStandards;
    if (seg.length === 1 && m === 'POST') {
      const x = { id: Math.max(0, ...fieldStandards.map((s) => s.id)) + 1, created_at: now(), ...body } as FieldStandard;
      fieldStandards.push(x);
      return x;
    }
    if (m === 'PATCH') {
      fieldStandards = fieldStandards.map((x) => (x.id === Number(seg[1]) ? { ...x, ...body, id: x.id } : x));
      return fieldStandards.find((x) => x.id === Number(seg[1]));
    }
    if (m === 'DELETE') fieldStandards = fieldStandards.filter((x) => x.id !== Number(seg[1]));
    return fieldStandards.find((x) => x.id === Number(seg[1]));
  }
  if (seg[0] === 'dimensions') {
    if (seg.length === 1 && m === 'GET') return dimensions;
    if (seg.length === 1 && m === 'POST') {
      const dimId = Math.max(0, ...dimensions.map((d) => d.id)) + 1;
      const vals = ((body.values as DimensionStandard['values']) ?? []).map((v, i) => ({
        id: Date.now() % 100000 + i,
        dimension_id: dimId,
        value: v?.value ?? '',
        label: v?.label ?? '',
        sort: i + 1,
      }));
      const x = { id: dimId, created_at: now(), ...body, values: vals } as unknown as DimensionStandard;
      dimensions.push(x);
      return x;
    }
    if (m === 'PATCH') {
      dimensions = dimensions.map((x) => {
        if (x.id !== Number(seg[1])) return x;
        const vals = Array.isArray(body.values)
          ? (body.values as DimensionStandard['values'])!.map((v, i) => ({
              id: v?.id ?? Date.now() % 100000 + i,
              dimension_id: x.id,
              value: v?.value ?? '',
              label: v?.label ?? '',
              sort: i + 1,
            }))
          : x.values;
        return { ...x, ...body, id: x.id, values: vals };
      });
      return dimensions.find((x) => x.id === Number(seg[1]));
    }
    if (m === 'DELETE') dimensions = dimensions.filter((x) => x.id !== Number(seg[1]));
    return dimensions.find((x) => x.id === Number(seg[1]));
  }
  if (seg[0] === 'metrics') {
    if (seg.length === 1 && m === 'GET') return metrics;
    if (seg.length === 1 && m === 'POST') {
      const x = { id: Math.max(0, ...metrics.map((m2) => m2.id)) + 1, created_at: now(), ...body } as MetricStandard;
      metrics.push(x);
      return { ...x, warnings: ['未知标识符 o2.user_id（黄警不阻断）'] };
    }
    if (m === 'PATCH') {
      metrics = metrics.map((x) => (x.id === Number(seg[1]) ? { ...x, ...body, id: x.id } : x));
      return metrics.find((x) => x.id === Number(seg[1]));
    }
    if (m === 'DELETE') metrics = metrics.filter((x) => x.id !== Number(seg[1]));
    return metrics.find((x) => x.id === Number(seg[1]));
  }

  // /suggestions（C1.3 冻结包络，P1-6）：{groups: {kind: [...]}, total}
  // 方案 v1.1 §8 过滤增量：?run_id= / ?table=；accept 支持 edits 编辑后采纳 + 写穿 fixtures
  if (seg[0] === 'suggestions') {
    if (seg.length === 1 && m === 'GET') {
      const qs = path.split('?')[1] ?? '';
      const runId = Number(qs.match(/run_id=(\d+)/)?.[1]) || null;
      const tableF = qs.match(/table=([^&]+)/)?.[1];
      const tableOf = (s: AiSuggestion): string | null => {
        try {
          const p = JSON.parse(s.payload_json) as Record<string, string>;
          return p.table ?? (typeof p.from === 'string' ? p.from.split('.')[0] : null);
        } catch {
          return null;
        }
      };
      let pending = suggestions.filter((s) => s.status === 'pending');
      if (runId != null) pending = pending.filter((s) => s.run_id === runId);
      if (tableF) pending = pending.filter((s) => tableOf(s) === decodeURIComponent(tableF));
      const groups = {} as Record<SuggestionKind, AiSuggestion[]>;
      for (const s of pending) (groups[s.kind] ??= []).push(s);
      return { groups, total: pending.length };
    }
    if (seg[1] === 'accept') {
      const ids = (body.ids as number[]) ?? [];
      const edits = (body.edits as Record<string, string>) ?? {};
      suggestions = suggestions.map((s) => {
        if (!ids.includes(s.id)) return s;
        // 编辑后采纳：comment 类以编辑文本替换 suggested
        let payloadJson = s.payload_json;
        if (s.kind === 'comment' && edits[String(s.id)]) {
          try {
            const p = JSON.parse(s.payload_json) as Record<string, string>;
            p.suggested = edits[String(s.id)];
            payloadJson = JSON.stringify(p);
          } catch { /* 保持原文 */ }
        }
        // C6.8/C6.8+ 写穿 fixtures（与后端采纳映射一致）
        try {
          const p = JSON.parse(payloadJson) as Record<string, string>;
          const t = tables.find((x) => x.name === p.table);
          if (s.kind === 'comment' && t) {
            if (p.column) {
              columns = columns.map((c) =>
                c.table_id === t.id && c.name === p.column ? { ...c, comment_custom: p.suggested } : c);
            } else {
              tables = tables.map((x) => (x.id === t.id ? { ...x, comment_custom: p.suggested } : x));
            }
          } else if (s.kind === 'std_bind' && t) {
            const std = fieldStandards.find((x) => x.name === p.standard);
            if (std) {
              columns = columns.map((c) =>
                c.table_id === t.id && c.name === p.column ? { ...c, field_standard_id: std.id } : c);
            }
          } else if (s.kind === 'relation') {
            relations.push({
              id: Math.max(...relations.map((r) => r.id)) + 1,
              datasource_id: s.datasource_id,
              from_table: p.from.split('.')[0], from_col: p.from.split('.')[1],
              to_table: p.to.split('.')[0], to_col: p.to.split('.')[1],
              source: 'inferred', confidence: s.confidence, status: 'confirmed',
            });
          }
        } catch { /* payload 异常仅标记状态 */ }
        return { ...s, payload_json: payloadJson, status: 'accepted' as const };
      });
      return { accepted: ids.length };
    }
    if (seg[1] === 'ignore') {
      const ids = (body.ids as number[]) ?? [];
      suggestions = suggestions.map((s) => (ids.includes(s.id) ? { ...s, status: 'ignored' } : s));
      return { ignored: ids.length };
    }
  }

  // /ai/completion-runs（方案 v1.1 §8 新增契约；进度流由 mockSimulateCompletion 驱动）
  if (seg[0] === 'ai' && seg[1] === 'completion-runs') {
    if (seg.length === 2 && m === 'POST') {
      // 与后端一致（2026-11-05 语义收敛）：同数据源已有 queued/running 任务 → 409 E3101
      const active = mockRunHistory.find(
        (r) => r.datasource_id === (body.datasource_id as number) && (r.status === 'running' || r.status === 'queued'),
      );
      if (active) throw Object.assign(new Error('该数据源已有补全任务进行中，请等待完成后重试'), { code: 'E3101', status: 409 });
      mockRunSeq += 1;
      return { run_id: mockRunSeq };
    }
    if (seg.length === 2 && m === 'GET') {
      const qs2 = path.split('?')[1] ?? '';
      const dsId2 = Number(qs2.match(/datasource_id=(\d+)/)?.[1]) || 1;
      return mockRunHistory.filter((r) => r.datasource_id === dsId2);
    }
    const rid = Number(seg[2]);
    // 显式取消（协作式）：打标记，由模拟器在安全点落定 cancelled；与「断流 ≠ 取消」分离
    if (seg[3] === 'cancel') {
      mockRunCancels.add(rid);
      return { cancelled: true };
    }
    if (seg.length === 3 && m === 'GET') {
      // 优先返回模拟器真实状态（轮询/恢复路径与后端 get 语义一致），未知 id 退回静态行
      const found = mockRunHistory.find((r) => r.id === rid);
      if (found) return { ...found };
      return {
        id: rid, datasource_id: 1, scope_type: 'datasource', scope_table_id: null,
        kinds: ['comment', 'relation', 'std_bind'], options: {}, status: 'done',
        progress_done: 7, progress_total: 7, made: { comment: 4, std_bind: 2 }, failed: [],
        error: null, created_at: now(), finished_at: now(),
      };
    }
  }

  // /samples
  if (seg[0] === 'samples') {
    if (seg.length === 1 && m === 'GET') return samples;
    if (seg.length === 1 && m === 'POST') {
      const s: Sample = {
        id: Math.max(...samples.map((x) => x.id)) + 1,
        datasource_id: body.datasource_id as number,
        question: body.question as string,
        sql: body.sql as string,
        status: 'on',
        source: 'manual',
        created_at: now(),
      };
      samples.push(s);
      return s;
    }
    const id = Number(seg[1]);
    if (seg[2] === 'revalidate') {
      samples = samples.map((s) => (s.id === id ? { ...s, status: 'on', stale_reason: null, validated_at: now() } : s));
      return samples.find((s) => s.id === id);
    }
    if (m === 'PATCH') {
      samples = samples.map((s) => (s.id === id ? { ...s, ...body } : s));
      return samples.find((s) => s.id === id);
    }
    if (m === 'DELETE') {
      samples = samples.filter((s) => s.id !== id);
      return null;
    }
  }

  // /queries
  if (seg[0] === 'queries') {
    if (seg.length === 1 && m === 'POST') {
      const qid = (body.client_request_id as string) ?? newQueryId();
      return { query_id: qid, stream: `/api/queries/${qid}/stream` };
    }
    if (seg[2] === 'resume' || seg[2] === 'stop') return null;
    // 契约 2：运行中引导/注入（mock 恒受理）
    if (seg[2] === 'steer') return { inbox_id: 'ib_mock_1', queued: true };
    if (seg[2] === 'save-sample') return { sample_id: 44 };
  }

  // /providers
  if (seg[0] === 'providers') {
    if (seg.length === 1 && m === 'GET') return providers;
    if (seg.length === 1 && m === 'POST') {
      const p: Provider = {
        id: Math.max(...providers.map((x) => x.id)) + 1,
        name: body.name as string,
        type: (body.type as string) ?? 'cloud',
        protocol: 'openai_chat',
        base_url: body.base_url as string,
        models: (body.models as string[]) ?? [],
        enabled: 1,
        created_at: now(),
      };
      providers.push(p);
      return p;
    }
    const id = Number(seg[1]);
    if (seg[2] === 'test') {
      const p = providers.find((x) => x.id === id);
      if (!p?.enabled) throw Object.assign(new Error('连接失败：401（密钥未外发）'), { code: 'E1001' });
      return { latency_ms: 182, models: p.models };
    }
    if (seg[2] === 'models') return providers.find((x) => x.id === id)?.models ?? [];
    if (m === 'PATCH') {
      providers = providers.map((p) => (p.id === id ? { ...p, ...body } : p));
      return providers.find((p) => p.id === id);
    }
    if (m === 'DELETE') {
      providers = providers.filter((p) => p.id !== id);
      return null;
    }
  }

  // /settings（C1.6 冻结包络，P1-3）：GET={settings:{k:{value,desc}}, local_only, verify_llm_greyed}；PUT ← {values}
  if (seg[0] === 'settings') {
    if (seg[1] === 'prompt-preview') return previewFixture;
    if (m === 'GET') {
      const routes = [settings['route.generate'], settings['route.fix'], settings['route.ai']].map(String);
      const localOnly = routes.every((r) => {
        const pid = Number(r.split(':')[0]);
        const p = providers.find((x) => x.id === pid);
        return !r || (p != null && p.type === 'local');
      });
      return {
        settings: Object.fromEntries(
          Object.entries(settings).map(([k, v]) => [k, { value: k === 'server.token' ? !!v : v, desc: '' }]),
        ),
        local_only: localOnly,
        verify_llm_greyed: localOnly,
      };
    }
    if (m === 'PUT') {
      const values = (body.values ?? {}) as Partial<SettingsMap>;
      settings = { ...settings, ...values };
      return null;
    }
  }

  // /memory
  if (seg[0] === 'memory') {
    if (seg[1] === 'log') return memoryLog;
    if (seg[1] === 'consolidate') return { merged: 2, dropped: 1 };
    if (seg.length === 1 && m === 'GET') {
      const grouped: Record<string, MemoryItem[]> = {};
      for (const it of memoryLedger) (grouped[it.topic] ??= []).push(it);
      return grouped;
    }
    const id = Number(seg[1]);
    if (seg[2] === 'toggle') {
      // C1.6 冻结（P1-8）：{enabled} 必填，显式目标态
      const enabled = body.enabled ? 1 : 0;
      memoryLedger = memoryLedger.map((x) => (x.id === id ? { ...x, enabled: enabled as 0 | 1 } : x));
      return memoryLedger.find((x) => x.id === id);
    }
    if (m === 'PATCH') {
      memoryLedger = memoryLedger.map((x) => (x.id === id ? { ...x, ...body } : x));
      return memoryLedger.find((x) => x.id === id);
    }
    if (m === 'DELETE') {
      memoryLedger = memoryLedger.filter((x) => x.id !== id);
      return null;
    }
  }

  // /backup（C1.6 冻结包络，P1-10）：list={items, restore_hint}；create={file}
  if (seg[0] === 'backup') {
    if (seg[1] === 'list') {
      return { items: backups, restore_hint: '恢复后进行中的查询一律标记失败，不适用自动续跑' };
    }
    if (m === 'POST') {
      const b: BackupFile = { name: `app-${Date.now()}.db`, size: 12_600_000, created_at: now() };
      backups = [b, ...backups].slice(0, 10);
      return { file: b.name };
    }
  }

  throw new Error(`mock 未覆盖：${m} ${path}`);
}

// ---------- AI 补全任务模拟器（方案 v1.1 §3.1 泳道事件流，形状与后端 SSE 一致） ----------

/** 标准绑定启发式（与后端 std_bind 提示词同语义）：列名/类型 → 标准名 */
function guessStandard(col: ColumnMeta): string | null {
  if (col.field_standard_id) return null;
  const n = col.name.toLowerCase();
  if (col.type.startsWith('decimal') || /amount|price|fee|cost/.test(n)) return '金额（含税）';
  if (col.type === 'int' && /qty|count|num/.test(n)) return '数量';
  if (/user_id/.test(n)) return '用户标识';
  if (col.type.includes('datetime') || n.endsWith('_at')) return '时间戳（创建）';
  if (/status/.test(n)) return '订单状态';
  if (col.type === 'tinyint' || /^is_/.test(n)) return '是否标记';
  if (/channel/.test(n)) return '渠道标识';
  return null;
}

/**
 * mock 补全模拟：lane 0 关系规则快道先行，1..workers 泳道按表名顺序取表；
 * 每表产出 0–2 条 comment（缺注释列/表）+ 0–1 条 std_bind（启发式），
 * 建议实时写入 mock 建议池（run_id 关联），评审抽屉 invalidate 后可见。
 *
 * 语义收敛（2026-11-05 工作区，对齐后端）：
 * - isCancelled 参数实为「断流」（detached，close() 触发）：仅停止事件回调，任务继续跑完/可被取消——
 *   「关闭 ≠ 取消」；唯一取消通道是 cancel 端点（mockRunCancels）；
 * - 同数据源活动任务互斥（409 等价：onEnd 先于 onStart 报错，控制器据此认领活动任务）；
 * - mockRunHistory 行随运行实时更新（progress/made），get(id) 轮询恢复可见真实进度。
 */
export async function mockSimulateCompletion(
  body: CompletionRunReq,
  handlers: {
    onStart?: (runId: number) => void;
    onLane: (ev: LaneEvent) => void;
    onEnd: (status: string, made: Record<string, number>, error?: string) => void;
  },
  isCancelled: () => boolean = () => false,
): Promise<void> {
  // 互斥（等价后端 409 E3101）：同数据源已有 queued/running 任务时拒绝新建
  const conflict = mockRunHistory.find(
    (r) => r.datasource_id === body.datasource_id && (r.status === 'running' || r.status === 'queued'),
  );
  if (conflict) {
    handlers.onEnd('failed', {}, '该数据源已有补全任务进行中，请等待完成后重试（E3101）');
    return;
  }
  mockRunSeq += 1;
  const runId = mockRunSeq;
  handlers.onStart?.(runId);
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const dsTables = tables.filter((t) => t.datasource_id === body.datasource_id);
  const scoped =
    body.scope.type === 'relations'
      ? []
      : body.scope.type === 'table'
        ? dsTables.filter((t) => t.id === body.scope.table_id)
        : dsTables;
  const items = scoped.map((t) => t.name).sort();
  const made: Record<string, number> = {};
  const nextSugId = () => Math.max(0, ...suggestions.map((s) => s.id)) + 1;

  const pushSuggestion = (kind: SuggestionKind, payload: Record<string, string>, confidence: number) => {
    suggestions.push({
      id: nextSugId(), datasource_id: body.datasource_id, kind,
      payload_json: JSON.stringify(payload), confidence, status: 'pending', run_id: runId,
    });
    made[kind] = (made[kind] ?? 0) + 1;
  };

  // 任务历史登记（批次过滤下拉 + 恢复轮询 get 可见；运行中实时更新）
  const hist: import('./types').AiRun = {
    id: runId, datasource_id: body.datasource_id,
    scope_type: body.scope.type, scope_table_id: body.scope.table_id ?? null,
    kinds: body.kinds, options: body.options ?? { rescan_existing: false, use_sample_values: false }, status: 'running',
    progress_done: 0, progress_total: items.length + (body.kinds.includes('relation') ? 1 : 0),
    made: {}, failed: [], error: null, created_at: now(), finished_at: null,
  };
  mockRunHistory.unshift(hist);

  const detached = isCancelled; // 断流仅停回调，任务继续（对齐后端：close 不取消）
  const cancelled = () => mockRunCancels.has(runId);
  const emitLane = (ev: LaneEvent) => {
    if (!detached()) handlers.onLane(ev);
  };
  /** 单项落定：进度 +1、合并 made 入历史行、广播全局帧（对齐后端 completion._settle） */
  const settle = () => {
    hist.progress_done += 1;
    hist.made = { ...made };
    emitLane({
      lane: -1, table: '*', phase: 'progress', line: '',
      done: hist.progress_done, total: hist.progress_total,
      pct: hist.progress_total ? Math.round((hist.progress_done / hist.progress_total) * 100) : 100,
      made,
    });
  };
  const finish = (status: 'done' | 'cancelled') => {
    hist.status = status;
    hist.made = { ...made };
    hist.finished_at = now();
    if (status === 'done') hist.progress_done = hist.progress_total;
    mockRunCancels.delete(runId);
    if (!detached()) handlers.onEnd(status, made);
  };

  // lane 0：关系规则快道（零出境）
  if (body.kinds.includes('relation')) {
    emitLane({ lane: 0, table: body.scope.type === 'table' ? items[0] : '*', phase: 'analyzing', line: '规则推断关系（命名/主外键同型，零出境）…' });
    await sleep(600);
    if (cancelled()) return finish('cancelled');
    const relTargets = items.length ? items : dsTables.map((t) => t.name);
    let relMade = 0;
    for (const tn of relTargets) {
      const t = tables.find((x) => x.name === tn);
      if (!t) continue;
      for (const c of columns.filter((x) => x.table_id === t.id && !x.is_pk && x.name.endsWith('_id'))) {
        const prefix = c.name.slice(0, -3);
        const target = tables.find((x) => x.datasource_id === body.datasource_id && (x.name === `${prefix}s` || x.name === prefix) && x.id !== t.id);
        if (!target) continue;
        const from = `${tn}.${c.name}`;
        const to = `${target.name}.id`;
        const dup = suggestions.some(
          (s) => s.kind === 'relation' && s.status === 'pending' && s.payload_json.includes(from) && s.payload_json.includes(to),
        ) || relations.some((r) => r.from_table === tn && r.from_col === c.name && r.to_table === target.name);
        if (dup) continue;
        pushSuggestion('relation', { from, to, via: 'name_match', reason: '命名惯例/主外键同名同型' }, 0.6);
        relMade += 1;
      }
    }
    emitLane({ lane: 0, table: '*', phase: 'done', line: `关系推断完成：+${relMade} 条建议`, made: { relation: relMade } });
    settle();
  }

  // LLM 泳道：每 lane 顺序取表（同样输入同样分配）
  const llmKinds = body.kinds.filter((k) => k === 'comment' || k === 'std_bind');
  if (llmKinds.length && items.length) {
    const workers = Math.min(3, items.length);
    const queue = [...items];
    await Promise.all(
      Array.from({ length: workers }, (_, i) => i + 1).map(async (lane) => {
        for (;;) {
          if (cancelled()) return;
          const tn = queue.shift();
          if (!tn) return;
          const t = tables.find((x) => x.name === tn)!;
          const t0 = Date.now();
          emitLane({ lane, table: tn, phase: 'analyzing', line: `分析 ${tn}（${t.field_count ?? '?'} 字段）…` });
          await sleep(500 + lane * 150);
          const cols = columns.filter((c) => c.table_id === t.id);
          const madeHere: Record<string, number> = {};
          if (body.kinds.includes('comment')) {
            if (!t.comment_db && !t.comment_custom) {
              pushSuggestion('comment', { table: tn, column: '', suggested: `${tn.replace(/_/g, ' ')} 业务表`, reason: '表名语义推断', current: '' }, 0.55);
              madeHere.comment = (madeHere.comment ?? 0) + 1;
            }
            for (const c of cols.filter((x) => !x.comment_db && !x.comment_custom).slice(0, 2)) {
              pushSuggestion('comment', { table: tn, column: c.name, suggested: `${c.name} 字段（待人工润色）`, reason: '命名与类型推断', current: '' }, 0.5);
              madeHere.comment = (madeHere.comment ?? 0) + 1;
            }
          }
          if (body.kinds.includes('std_bind')) {
            for (const c of cols) {
              const std = guessStandard(c);
              if (!std) continue;
              const dup = suggestions.some((s) => s.kind === 'std_bind' && s.status === 'pending' && s.payload_json.includes(`"${tn}"`) && s.payload_json.includes(`"${c.name}"`));
              if (dup) continue;
              pushSuggestion('std_bind', { table: tn, column: c.name, standard: std, reason: '类型与命名命中标准目录', current: '（未绑定）' }, 0.66);
              madeHere.std_bind = (madeHere.std_bind ?? 0) + 1;
              break; // 每表最多 1 条演示
            }
          }
          emitLane({ lane, table: tn, phase: 'validating', line: '校验建议（目标存在性/判重）' });
          await sleep(220);
          const ms = Date.now() - t0;
          const summary = Object.entries(madeHere).map(([k, v]) => `${k}+${v}`).join(' · ') || '无新增';
          emitLane({ lane, table: tn, phase: 'done', line: `${tn}：${summary}（${(ms / 1000).toFixed(1)}s）`, made: madeHere, ms });
          settle();
        }
      }),
    );
  }
  if (cancelled()) return finish('cancelled');
  finish('done');
}
