# AItosql 本地 RAG 设计文档

> 版本：v3.2 加固后（2026-10-18）｜范围：`app/retrieval/*`、`app/store/samples.py`、`app/migrations/0009_rag_revisions.sql`

## 1. 是否需要 RAG

**需要，且本项目已经实现了 RAG。** Text2SQL 的 Schema 上下文注入本质就是检索增强生成：

- 库表超过 30 张或全量 Schema 估算超过 `schema.budget_pct` 预算时，不可能把全部元数据塞进 Prompt；
- 必须从「表/字段/注释/字段标准/维度枚举/指标口径/已确认关系/历史样本」中**按问题检索**最相关子集注入；
- 这正是 RAG 的经典形态：离线建索引 → 在线召回 → 融合排序 → 预算装配 → 注入生成。

结论：不是"要不要做 RAG"，而是"已有的 RAG 是否做对了"。审计发现原实现骨架正确（FTS5 bigram + sqlite-vec + RRF 融合），但有 6 处正确性缺陷，本轮已全部修复。

## 2. 修复前缺陷清单（均已修复并附回归测试）

| # | 缺陷 | 影响 | 修复 |
|---|------|------|------|
| 1 | 向量文档只取**最后一个字段**（`grouped[name] = [...]` 每行覆盖），FTS/向量索引内容残缺 | 语义召回几乎无效 | `retrieval/index.py::documents()` 聚合表注释+全部字段+字段标准，测试 `test_document_completeness` |
| 2 | 人工编辑元数据后索引**不刷新**（FTS 只在摄取时重建） | 检索结果陈旧 | `schema_revision` 单调版本号 + ~20 个触发器（0009 迁移）；`ensure_fts()` 检索前惰性比对版本重建，测试 `test_lazy_fts_refresh_after_same_second_manual_edit` |
| 3 | 无关系图扩展：只召回字面命中的表，JOIN 桥接表缺失 | 生成 SQL 缺 JOIN 路径 | `supply.py` 对已确认关系做 BFS 桥接扩展（仅 `status='confirmed'`，pending 关系绝不注入），并追加 `已确认关系: a.col = b.col` 结构化行，测试 `test_confirmed_join_bridge_and_no_pending_injection` |
| 4 | few-shot 样本只按热度排序，与当前问题无关 | 示例干扰生成 | `store/samples.py::query_fewshot(question=)` 词法重叠重排（CJK bigram + 字母数字词），测试 `test_fewshot_relevance` |
| 5 | full 模式**不执行预算**，retrieval 模式首表可超预算 | Prompt 静默超限被 API 拒绝 | 三种模式统一 `fits()` 严格预算；`llm/prompts.py` 固定段超限时显式 `E4420` 报错而非静默截断，测试 `test_all_modes_obey_budget_and_report_only_injected_tables` |
| 6 | 向量模型首次使用时**隐式联网下载**，失败后把 `schema.vector=off` 永久写库 | 违背 local-first；一次网络抖动永久关单向量召回 | `local_files_only=True` + `AITOSQL_MODEL_CACHE` 指定缓存；加载失败 60s 重试窗口，不落库关开关；fastembed 移入可选依赖 `pip install aitosql[vector]`，缺失时自动降级纯 FTS |

## 3. 总体架构

```
┌──────────────────── 离线（摄取/重建）────────────────────┐
│ ingest 完成 / POST /datasources/{id}/reindex             │
│   └→ retrieval/index.py::rebuild(ds_id)                  │
│        ① 读事务取 schema_revision + documents()           │
│        ② 事务外计算 embedding（fastembed BGE-small-zh）    │
│        ③ BEGIN IMMEDIATE 重校验版本一致                   │
│        ④ replace_vectors + rebuild_fts + 版本落账         │
│        （版本漂移 → 放弃安装，绝不写脏索引）                │
├──────────────────── 在线（每次查询）─────────────────────┤
│ graph generate 节点 → retrieval/supply.py::supply()       │
│   ① 模式判定：full / retrieval / auto（55-65% 滞后带）    │
│   ② ensure_fts：版本比对，人工编辑后惰性重建               │
│   ③ 双路召回：FTS top-12 ∥ 向量 top-4（FTS≥6 表跳向量）   │
│   ④ RRF k=60 融合 × 生成反馈乘性加权（确定性排序不变）     │
│   ⑤ need_tables 强制并入（二次供给，图状态限 1 次）         │
│   ⑥ 已确认关系 BFS 桥接扩展 + 关系行注入                  │
│   ⑦ 宽表列级裁剪（>阈值按问题相关性注入字段子集）           │
│   ⑧ fits() 严格预算装配，首表也不放行超预算                │
│   ⑨ SupplyResult.trace 全链路诊断                         │
├──────────────────── few-shot 样本 ────────────────────────┤
│ store/samples.py::query_fewshot(question)                │
│   热度初筛 → CJK bigram/字母数字词重叠重排 → 取 top-N      │
├──────────────────── 反馈闭环 / 日志挖掘 ───────────────────┤
│ execute 成功 → tables_in_sql 提取 → retrieval_feedback    │
│ GET /datasources/{id}/retrieval-insights → 补注释优先级    │
└──────────────────────────────────────────────────────────┘
```

## 4. 关键设计决策

### 4.1 索引新鲜度：schema_revision 版本机制

- `0009_rag_revisions.sql` 建 `schema_revision(datasource_id, revision, fts_revision, vector_revision, vector_model, vector_dim)`；
- 对 table_meta / column_meta / relation / field_standard / dimension_standard / dimension_value / metric_standard 的 INSERT/UPDATE/DELETE 建触发器，`revision` 单调递增（同秒内多次编辑用 `MAX(revision+1, strftime)` 保证严格递增）；
- **在线路径零重建成本**：`ensure_fts()` 只在 `revision != fts_revision` 时重建；
- **离线重建防并发写脏**：embedding 在写事务外计算（秒级），安装前 `BEGIN IMMEDIATE` 重校验版本，漂移则整批放弃——宁可重建也不写混合版本索引；
- `rebuild` 并发保护：版本重校验天然拒绝并发安装（测试 `test_concurrent_rebuild_rejection`）。

### 4.2 双路召回与融合

- **关键词路**：FTS5 + bigram 分词（中文无空格场景 substring 匹配），top-12；
- **向量路**：sqlite-vec `schema_vec_v2`，BGE-small-zh-v1.5（512 维，cosine），top-4；模型/维度与索引记录不一致时跳过向量路（防维度错配）；
- **降级矩阵**：fastembed 未装 → 纯 FTS；FTS 命中 ≥6 表 → 跳向量（省 50-200ms）；任一路异常 → full 截断降级（E14）；
- **RRF k=60** 按表去重融合，排序键 `(score DESC, table_name ASC)`——同名不同分也有确定性顺序，验收可复现。

### 4.3 关系图扩展（JOIN 桥接）

- 只对 `status='confirmed'` 的关系扩展——待确认/AI 猜测的关系绝不进 Prompt（测试断言 `untrusted` 不出现）；
- 以召回前 3 名 + need_tables 为种子，BFS 找种子间的 1-2 跳桥接表（如 orders→membership→customers 的 membership）；
- 命中表之间的确认关系追加为 `已确认关系: orders.id = membership.id` 结构化行，给模型明确的 JOIN 条件；
- 桥接表与关系行同样走 `fits()` 预算，不超注。

### 4.4 预算装配

- `fits(text)` 增量估算：`estimate_tokens(已有 + 候选) <= budget_tokens`，三种模式（auto/full/retrieval）统一；
- 被预算挤掉的表记入 `trace.omitted_tables`，可观测；
- 固定 Prompt（系统规则+问题）超限时 `llm/prompts.py` 显式抛 `E4420`——显式失败优于静默截断后生成错 SQL。

### 4.5 可观测性：SupplyResult.trace

每次供给返回全链路诊断：`fts_tables` / `vector_tables`（两路各自命中）、`feedback_tables`（反馈加权的表）、`wide_pruned`（宽表裁剪明细）、`join_expansion`（桥接了哪些表）、`required_tables`、`omitted_tables`、`budget_tokens` / `used_tokens`、`fallback`（降级原因）、`revision`（供给时索引版本）。排障"为什么这张表没进 Prompt"时直接看 trace。

### 4.6 本地优先与依赖治理

- 嵌入模型 `local_files_only=True`，缓存目录 `AITOSQL_MODEL_CACHE`（默认 `data/models/`）——运行时零网络；
- fastembed 为可选依赖（`pyproject.toml [project.optional-dependencies] vector`），核心安装不拉 torch/onnxruntime；
- 向量加载失败不永久落库 `schema.vector=off`（60s 重试窗口内重试），一次抖动不永久降级；
- 导入契约 `D3 fastembed 唯一 import 方 = app.retrieval.vector` 由 import-linter 强制。

### 4.7 生成反馈闭环（演进 #1，已实现 2026-10-19）

- **回写**：execute 成功（行返回/写成功）→ `sqlcheck.rules.tables_in_sql()` 从最终 SQL 提取真实物理表（CTE 名剔除，sqlglot 唯一入口契约不破）→ `retrieval_feedback` 表 UPSERT 累计（0010 迁移）；回写是附加产物，失败静默不污染主结果；
- **加权**：召回时 `boosts()` 给每表 `log1p(success_count)` 增量，RRF 分数乘性加权 `score × (1 + 0.2·log1p(n))`——log 刻度让高频表优势收敛，新召回表不被永久压制；只影响排序不改变召回集合；`(score DESC, name ASC)` 确定性锚点不变；
- **证据**：`test_feedback_record_and_boost_reorders_rrf`（UPSERT/反超/确定性）、`test_supply_applies_feedback_boost`（端到端排序+trace）、`test_execute_success_records_feedback`（节点级回写计数）。

### 4.8 列级召回（演进 #2，已实现 2026-10-19）

- 字段数超过 `schema.wide_table_columns`（默认 200，0 关闭）的宽表，注入时按问题相关性裁剪到 `schema.wide_table_keep`（默认 60）；
- **必留**：主键/分区键/分桶键、绑定字段标准的列、已确认关系的关联键（JOIN 条件不丢）；
- **相关性**：其余列按 `lexical_terms(问题) ∩ lexical_terms(名+类型+注释)` 重叠数排序取 top，同分按有注释优先、列 id 升序（确定性）；
- 注入文本追加 `[宽表：共 N 列，按问题相关性注入 M 列]` 标记；无新索引、纯本地、零额外模型成本；
- **证据**：`test_wide_table_pruned_by_question_relevance`（121 列裁到 ≤12、相关列/主键保留、无关列剔除、低于阈值不触发）。

### 4.9 查询日志挖掘（演进 #3，已实现 2026-10-19）

- `retrieval/insights.py::comment_gaps()`：使用证据 = `retrieval_feedback` 成功计数 ∪ 历史 `query`（status=done）question/sql_final 表名提及扫描（词边界正则，确定性）；
- 对每张使用 ≥min_uses 的表算注释覆盖率（表注释 + 字段注释占比），输出 `uses/coverage/missing/suggestion`，按 使用次数 DESC → 覆盖率 ASC → 表名 ASC 排序；
- 端点 `GET /datasources/{id}/retrieval-insights`；注释质量是召回质量上限，该清单就是"补注释优先级队列"；
- **证据**：`test_comment_gaps_orders_by_usage_and_coverage`（计数合并、覆盖率、缺口明细、排序）。

## 5. 与写安全的边界

RAG 只读元数据库（app.db），**不索引业务行数据**——`documents()` 只取 table/column/relation/standard 元数据与注释，向量化的也只是元数据文本。业务数据不出数据源连接，符合 local-first 与数据不出域原则。

## 6. 验证证据

- `tests/unit/test_retrieval/test_rag_hardening.py`：文档完整性、同秒编辑 FTS 刷新、关系桥接+pending 隔离、全模式预算、few-shot 相关性+数据源隔离、过期向量跳过、真 sqlite-vec cosine 检索、并发重建拒绝；
- `tests/unit/test_retrieval/test_evolution.py`：反馈闭环（提取/加权/节点回写）、宽表列级召回、日志挖掘排序；
- 全量门禁：`ruff check` 0 错误、`mypy app` 0 错误、`lint-imports` 5 契约全 kept、`pytest` 全通过。

## 7. 后续演进方向（未做，按优先级）

1. **rerank 模型**：BGE-reranker 对 RRF 前 8 名精排，token 成本允许时开启；
2. **多数据源联邦**：跨数据源关联分析时按数据源分组召回再合并（当前单查询单数据源，设计已按 ds_id 隔离，扩展成本低）；
3. **反馈衰减**：retrieval_feedback 目前只增不减，可加时间衰减（如月度减半）防止历史热点永久主导排序。
