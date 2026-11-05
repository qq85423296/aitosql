# AItosql 个人版 Text2SQL · 需求规格（spec）

> 阶段产物：01-requirements / spec ｜ 运行：sdlc-20261015015550-sdpj1
> 配套：同目录 `propose.md`（目标/范围/场景/验收标准/风险分析）。
> 溯源：全部条目来自 `整体需求解决方案.md`（SSOT v3.2）及 `数据源模块设计.md`；本文不新造接口/字段，无法验证处以 **TODO** 标注。

---

## 1. 功能清单

### 1.1 查询页（FR-Q1~Q14，原型 `prototype/index.html`）

| ID | 功能 | 验收要点（摘要） | 原型落位 |
|----|------|----------------|---------|
| FR-Q1 | 会话管理：分组（置顶/普通/归档折叠）、行内重命名、删除行内二次确认、⌘K 列表内过滤、计数徽标 | 排序 pinned>updated_at；删除二次确认；归档进折叠组（D11：⌘K=列表内过滤聚焦，非命令面板） | 左侧会话抽屉 |
| FR-Q2 | 数据源切换器（顶部下拉，含离线徽标） | 切离线库：Composer 提示「仅生成不执行」，执行键替换为「复制 SQL」 | 头部下拉 |
| FR-Q3 | SSE 管线阶段卡：阶段推进 + 耗时 + 停止 | stage 8 枚举带 round（§10.3）；Esc/按钮停止触发 stop+KILL | 阶段卡 |
| FR-Q4 | 推理过程块（reasoning 折叠） | 默认收起，点击展开 | 问答 1 |
| FR-Q5 | 假设芯片（assumptions 逐条，impact 分级配色） | 点击「修正」预填 Composer（fixAssume） | 问答 1 假设 chips |
| FR-Q6 | 写操作确认门（风险清单 + 影响行数估算 + 确认/取消） | 取消标注「已取消，语句未执行」；CH 只读联动文案（§9.4） | 问答 2 |
| FR-Q7 | SQL 块（语法高亮只读 + 复制 + 编辑重跑） | 编辑后重跑走新 client_request_id | 问答 1 SQL 卡 |
| FR-Q8 | 澄清门（高风险歧义选项卡 label+expr + 自定义输入） | 裁决写 clarify_pref，下次免问并显示沉淀提示 | 问答 3 |
| FR-Q9 | 结果表（列排序、导出 CSV、复核包复制、存为样例） | 排序按列类型；复核包模板 §11.6；导出走 §10.5 | 问答 1 结果卡 |
| FR-Q10 | 执行元信息（行数/扫描量/耗时/数据截至） | 分区裁剪命中时显示提示 | 结果卡底部 |
| FR-Q11 | 结果校验块（规则层清单 + 未验证标注 + 修正时间线） | 全过绿块；未过照常交付标琥珀「未验证」；修正逐轮可展开 | 问答 1/5 |
| FR-Q12 | 右侧上下文面板（Schema 树：键徽章/+插入表名；查询详情） | 插入表名到 Composer；详情含耗时分解/扫描量/数据时间 | 右侧栏 |
| FR-Q13 | 空态与错误态（新会话引导、模型离线、R15 硬阻断、自动恢复横幅、压缩提示条） | 逐条对照 §13 状态清单 | 新会话/问答 4 |
| FR-Q14 | 追问建议 chips | 点击即作为新提问发送；feature.followups 开关控制 | 问答 1 底部 |

### 1.2 数据源页（FR-D1~D13，原型 `prototype/datasources.html`）

| ID | 功能 | 验收要点（摘要） |
|----|------|----------------|
| FR-D1 | 数据源列表与 CRUD | 在线/离线徽标、方言标签、最近摄取时间 |
| FR-D2 | 注册向导（在线 3 步：连接→测试→摄取；离线 2 步） | 测试失败红卡 + 下一步锁定（E1001）；可演示失败态 |
| FR-D3 | 在线摄取（三方言元数据 + 键信息探测） | 日志计数「主键/分区键（月·日）/分桶键」；人工注释不被覆盖 |
| FR-D4 | 摄取 SSE 进度与日志 | 进度条 + 逐行日志；中断可续摄（E1002） |
| FR-D5 | 表清单（行数/字段数/注释行内编辑） | 编辑即时保存；重摄取不回滚 |
| FR-D6 | 表详情字段表（键徽章、注释、标准绑定展示） | 在线库字段名/类型只读 |
| FR-D7 | 编辑抽屉 ×2（表编辑 + 字段编辑） | 字段编辑中维度随标准联动 |
| FR-D8 | 提示词视图 | 与真实注入逐字一致；含分区键标注「分区键·按月，查询大表请带时间范围」 |
| FR-D9 | 关系管理（双语、来源 FK/推断/人工、置信度、确认/删除） | 推断关系 pending 确认后才注入 |
| FR-D10 | ER 图谱（SVG 缩放/拖拽/节点信息卡） | 节点含主键/分区/分桶标识；边双语；摆位存储见 TODO-N1 |
| FR-D11 | 字段标准/维度标准/指标口径 CRUD | 维度枚举值管理；指标表达式保存时 sqlglot 校验 |
| FR-D12 | AI 建议队列（comment/std_bind/relation/metric） | 分组评审、批量采纳/忽略；采纳映射 §12.2 |
| FR-D13 | 离线数据源（DDL 解析 / CSV 模板 / AI 草稿） | DDL 部分失败行标红、成功部分可入库；R13 降级黄警（D04）；只生成不执行 |

### 1.3 样例库页（FR-S1~S4，原型 `prototype/samples.html`）

| ID | 功能 | 验收要点 |
|----|------|---------|
| FR-S1 | 样例列表（数据源/状态筛选、命中次数） | on/stale/off 状态徽标 |
| FR-S2 | 新增/编辑/停用样例 | question_norm 归一化（小写/去空白/去标点） |
| FR-S3 | stale 治理（schema 变更自动置过期 + 复验恢复） | stale_reason 记字段名；复验走 EXPLAIN 干跑（§12.1） |
| FR-S4 | 缓存命中（命中样例直返 SQL） | 三条件命中（§12.1 semantic_rev）；标注「复用已验证结果」 |

### 1.4 设置页（FR-C1~C8，原型 `prototype/settings.html`）

| ID | 功能 | 验收要点 |
|----|------|---------|
| FR-C1 | 提供商管理（卡片列表 + 抽屉编辑；内置目录 10 类型；密钥 eye 切换；模型目录 CRUD + 拉取） | 空模型 ID 校验「模型 N: 模型 ID 不能为空」 |
| FR-C2 | 测试连接（成功：延迟+模型数；失败：401/拒绝分支） | 失败红卡注明「密钥未外发」 |
| FR-C3 | 路由策略（生成/修正/AI 补全三路） | 出境清单矩阵联动（§12.5，E6 红标）；verify_llm 在 local_only 置灰（D07） |
| FR-C4 | 提示词维护（系统模板变量校验/恢复默认 + 能力卡方言 Tab + 行为开关 ×5 + 组装预览抽屉） | 预览 8 段与真实注入逐字一致（§10.4） |
| FR-C5 | 查询与安全参数 + 三层防线展示 | 默认值与 SSOT §4 一致 |
| FR-C6 | Schema 注入（模式选择 + 预算滑块 + 枚举上限） | 滞后带说明（55-65%） |
| FR-C7 | 记忆与偏好（机制设置 + 总账管理：开关/编辑/容量条/立即合并 + 流水账视图） | §12.6 验收口径 |
| FR-C8 | 备份（目录/保留份数/加密开关/立即备份/恢复提示）+ 通用 + 关于 | 恢复提示含「不适用自动续跑」（§12.3）；「通用/关于」字段 TODO-N6 |

### 1.5 后端需求（BR-1~BR-16）

| ID | 需求 | 实现锚点（SSOT） |
|----|------|----------------|
| BR-1 | 数据源摄取：在线三方言 + 离线三通道 | ingest/* |
| BR-2 | schema 供给：双路召回 RRF 融合 + 预算装配 + 滞后带 + need_tables 二次供给 | retrieval/* |
| BR-3 | 生成契约：JSON Schema 校验 + 重试 1 次 + 降级提取 | graph/nodes.py generate |
| BR-4 | 澄清门：interrupt/resume + clarify_pref 沉淀 + 命中免问 | clarify_gate |
| BR-5 | AST 校验 R01-R15（含 R15 五断言硬阻断） | sqlcheck/rules.py |
| BR-6 | 写操作确认门：interrupt + CAS 裁决 + CH 只读文案 | confirm_gate |
| BR-7 | 执行：超时/LIMIT AST 注入/工作·管理连接分离/CONNECTION_ID/元信息采集/CSV 落盘 | exec/* |
| BR-8 | verify_result：规则层 5 检查 + 回退全局 1 次（D09）+ 可选 LLM 层 | verify/* |
| BR-9 | 修正回路：≤3 轮 + SQL 指纹振荡检测 + 终止照常交付 | correct |
| BR-10 | SSE 16 事件 + query_event 落库 + 幂等续播 + stop/KILL + D03 自动续跑 | graph/runner.py、api/sse.py |
| BR-11 | 提供商适配（OpenAI 兼容/Anthropic/Ollama）+ 密钥 Fernet/keyring 五款 | llm/* |
| BR-12 | 提示词组装：8 段信封 + 5 步裁剪 + 预览逐字一致 | llm/prompts.py |
| BR-13 | 样例库：stale 治理 + 复验 + semantic_rev 缓存命中 | store/samples.py |
| BR-14 | AI 建议队列：四类 payload + 采纳映射 | store/suggestions.py |
| BR-15 | 备份：VACUUM INTO 滚动 + 密钥剥离 + 可选加密（D08）+ 恢复语义 | store/backup.py |
| BR-16 | 记忆与会话压缩：flush 三入口 + 水位线合并 + 压缩抢救 + 注入 | store/memory.py |

---

## 2. 数据字典

> 存储：SQLite 3.46+（WAL），双库：`app.db`（终态权威，入备份）/ `checkpoints.db`（进度权威，不入备份）。DDL 原文见 SSOT §3.1~§3.4（migrations 0001~0004）。下列字段说明为需求侧语义注解，类型与约束以 DDL 为准。

### 2.1 datasource（数据源）

| 字段 | 类型/约束 | 说明 |
|------|-----------|------|
| id | INTEGER PK | |
| name | TEXT NOT NULL | 数据源名 |
| dialect | TEXT CHECK(mysql/doris/clickhouse) | 方言 |
| mode | TEXT CHECK(online/offline) | 在线直连 / 离线配置 |
| host/port/db_name/username | — | 连接信息（离线库可空） |
| password_enc | BLOB | Fernet 密文；离线库 NULL；备份剥离为 NULL |
| params_json | TEXT DEFAULT '{}' | 连接参数 |
| version | TEXT | 注册时探测（MySQL <8 标注能力降级） |
| capabilities_json | TEXT DEFAULT '{}' | 能力卡（版本探测产出） |
| last_ingest_at / created_at | TEXT | 摄取时间 / 创建时间 |

### 2.2 table_meta（表元数据）

| 字段 | 类型/约束 | 说明 |
|------|-----------|------|
| id | INTEGER PK | |
| datasource_id | FK→datasource | |
| name | TEXT | UNIQUE(datasource_id, name) |
| comment_db / comment_custom | TEXT | 库注释 / 人工注释（人工优先，重摄取不覆盖） |
| row_estimate / field_count | INTEGER | 行数估算（R09 大表阈值 100 万）/ 字段数 |
| partition_type | TEXT | month \| day \| NULL（表级冗余） |
| updated_at | TEXT | |

### 2.3 column_meta（字段元数据）

| 字段 | 类型/约束 | 说明 |
|------|-----------|------|
| id | INTEGER PK | UNIQUE(table_id, name)；idx_column_table |
| table_id | FK→table_meta | |
| name / type | TEXT NOT NULL | 在线库只读，离线库可修正 |
| comment_db / comment_custom | TEXT | 双语注释 |
| is_pk / is_partition / is_bucket | INTEGER 0/1 | 主键 / 分区键 / 分桶键（摄取探测 + 人工修正开关） |
| field_standard_id | FK→field_standard | 标准绑定 |
| updated_at | TEXT | |

### 2.4 relation（表关系）

| 字段 | 类型/约束 | 说明 |
|------|-----------|------|
| from_table/from_col/to_table/to_col | TEXT NOT NULL | 关系两端 |
| source | TEXT CHECK(fk/inferred/manual) | 三来源 |
| confidence | REAL DEFAULT 1.0 | inferred 时 0~1 |
| status | TEXT DEFAULT 'confirmed' CHECK(confirmed/pending) | inferred 默认 pending，确认后才注入提示词 |

### 2.5 语义层（全局一份，跨数据源复用）

- **field_standard**：id / name UNIQUE / data_type / unit / value_desc / dimension_standard_id FK / description / created_at。
- **dimension_standard**：id / name UNIQUE / description / created_at。
- **dimension_value**：id / dimension_id FK / value / label / sort（注入上限 schema.enum_top_n=8）。
- **metric_standard**：id / name UNIQUE / **expr** NOT NULL / **filter**（TODO-N3：SQL 上下文关键字列名，实现期统一加引号或参数化构造）/ base_tables / description / created_at；保存时 sqlglot 校验，未知标识符黄警不阻断；复合指标注入时展开一次不递归。

### 2.6 ai_suggestion（AI 建议队列）

| 字段 | 类型/约束 | 说明 |
|------|-----------|------|
| kind | TEXT CHECK(comment/std_bind/relation/metric) | 四类 payload 结构见 SSOT §12.2 |
| payload_json | TEXT NOT NULL | 目标对象内嵌于 payload（无独立 target 列，以 SSOT 为准） |
| confidence | REAL | |
| status | TEXT DEFAULT 'pending' CHECK(pending/accepted/ignored) | 采纳映射：comment→comment_custom；std_bind→field_standard_id；relation→relation(confirmed)；metric→metric_standard |

### 2.7 session（会话）/ clarify_pref（口径偏好）

- **session**：id TEXT PK（`s_`+ULID）/ title（首问完成自动生成=问题去空白前 18 字，重命名后不再自动改写）/ datasource_id FK / pinned / archived / created_at / updated_at；索引 idx_session_list(archived, pinned DESC, updated_at DESC)。v3.1 增补：**summary**（滚动摘要，有损链）、**summary_upto**（摘要覆盖到的最后 query_id）。
- **clarify_pref**：id / datasource_id / term / chosen_rule / created_at；UNIQUE(datasource_id, term)；命中免问。

### 2.8 query（查询）/ result_preview（结果预览）

**query**：id TEXT PK（`q_`+ULID = client_request_id = thread_id，幂等键）/ session_id FK / datasource_id / dialect / question NOT NULL / contract_json / sql_final / **status** CHECK(running/waiting_clarify/waiting_confirm/done/failed/stopped) DEFAULT 'running' / rounds / tokens / scan_rows / data_max_time / duration_ms / result_unverified（0/1）/ clarify_choice / created_at。

**result_preview**：query_id PK FK / columns_json / rows_json（截断 50KB）/ truncated。密级：含真实数据行。

### 2.9 query_event（事件溯源，migration 0003）

| 字段 | 说明 |
|------|------|
| query_id FK ON DELETE CASCADE | 级联自闭环；不设 retention（D12 关闭，TODO-N8） |
| seq | 单调递增，续播接缝；PRIMARY KEY(query_id, seq) |
| type | §10.3 事件类型；控制帧 stream_resumed 不入表 |
| payload | 错误类落库前过脱敏（§12.5 第三出口）；result 批量行不入此表；单事件 ≤ sse.event_payload_kb(16KB) 超出截断标注 |

### 2.10 sample（样例库）

id / datasource_id FK / question / **question_norm**（小写/去空白/去标点）/ sql / status CHECK(on/stale/off) DEFAULT 'on' / source DEFAULT 'manual' / **semantic_rev**（语义版本指纹 = sha256(schema_rev, clarify_pref_rev, dialect_card_hash, sem_bind_rev, prompt_sys_hash)，懒惰失效）/ validated_at / stale_reason（记字段名）/ hit_count / created_at；索引 idx_sample_norm(datasource_id, question_norm)。**缓存命中三条件**：question_norm 精确 ∧ status=on ∧ semantic_rev 相等。

### 2.11 provider / setting

- **provider**：id / name / type / protocol DEFAULT 'openai_chat' / base_url / api_key_enc BLOB（Fernet，备份剥离）/ models_json DEFAULT '[]' / enabled / created_at。
- **setting**：key PK / value_json——配置键值全表见 SSOT §4（关键键摘录见 §5.6 本文）。

### 2.12 检索层（migration 0002 + 运行期）

- **schema_fts**：FTS5 虚拟表（datasource_id UNINDEXED, table_name, col_name, comment），tokenize=unicode61，中文由应用层 bigram 展开后写入；**只消费消毒后文本**（ingest/sanitize.py 唯一写入入口）。
- **schema_vec**：运行期按 `schema.embed_dim`（默认 512）建表，不写死 migration；换模型流程：改 setting → 重建 vec 表（期间向量检索临时 off，降级纯 FTS）→ 完成自动恢复。

### 2.13 记忆模块（migration 0004，v3.1）

| 表 | 要点 |
|----|------|
| memory_log（流水账） | 追加只写从不修改；kind CHECK(caliber/correction/quirk/preference)；fact 原文；datasource_id NULL=全局；source_query_id 溯源 |
| memory（总账） | scope CHECK(global/datasource)；topic 分组；content；enabled（停用即不注入）；token_est；≤ memory.max_tokens(4000) |
| memory_watermark | 单行 CHECK(id=1)；last_log_id 增量合并水位线 |
| session.summary / summary_upto | 会话滚动摘要（见 2.7） |

### 2.14 文件存储（非 DB）

| 路径 | 内容 | 治理 |
|------|------|------|
| data/app.db | 终态权威（生命周期+事件流+产出物） | 入备份；0600 权限 |
| data/checkpoints.db | 图进度 + interrupt 门负载 | **不入备份**（最大明文聚合体）；备份恢复后 running 一律 failed+标注 |
| data/backups/ | VACUUM INTO 滚动备份 | keep=10；可选加密（D08） |
| data/exports/ | 纯 SELECT 全量 CSV（`{query_id}.csv`，UTF-8 BOM + RFC4180） | 瞬态目录：TTL 24h 定时清理 + 2048MB LRU + 启动清扫孤儿文件；不入备份、gitignore |

---

## 3. 接口契约（摘要）

> 全量端点表见 SSOT §10.1（30+ 端点，全部 `/api` 前缀 + Bearer token，CORS/Origin 限定 127.0.0.1）。此处列契约骨架与关键端点。

### 3.1 查询主链路端点

| 端点 | 契约要点 |
|------|---------|
| POST /queries | 请求 `{session_id, datasource_id, question, client_request_id}`；响应 202 `{query_id, stream}`；同 client_request_id 重发返回同一 query_id（幂等） |
| GET /queries/:id/stream | SSE；帧 `id: {query_id}:{seq}`；Last-Event-ID 增量 / 无头全量自 seq=1 |
| POST /queries/:id/resume | 门裁决 `{gate:'clarify'\|'confirm', choice?, approved?}`，CAS（仅 waiting_* 态） |
| POST /queries/:id/stop | 停止 + 引擎 KILL（CH readonly 无 KILL 通道，UI 注明） |
| POST /queries/:id/save-sample | 存为样例（同步直写 memory_log） |
| GET /queries/:id/export.csv | FileResponse 流式；响应头回显 data_max_time；仅纯 SELECT 成功时存在文件 |
| GET /sessions/:id/queries | 会话消息快照（E5）：query+result_preview+query_event 聚合，含 last_seq 与 clarify_choice，不依赖 checkpoint |

### 3.2 SSE 十六事件（终稿）

`stage`（8 枚举 supply/generate/validate/clarify/confirm/execute/verify/correct，带 round/status/elapsed_ms）· `contract` · `sql`（带 round，逐轮重发）· `risk` · `clarify_required` · `confirm_required`（含 est_rows）· `correction`（判定入回路即发）· `meta`（两次全字段：post_supply / pre_done）· `result_meta` · `result` · `verify` · `followups` · `stopped` · `done` · `error`。另有连接级控制帧 `stream_resumed`（无 seq 无 id 不入表）与心跳注释帧 `:hb`（每 sse.heartbeat_s=15s，不占 seq）。

### 3.3 生成契约（M3 输出 JSON Schema）

required：`reasoning`(≤1200 字符) / `assumptions[]`{text, impact:high|low, kind?} / `sql`；可选：`need_tables[]`（二次供给仅 1 次）、`followups[]`（≤3）。解析策略：校验失败 → 原样回喂重试 1 次 → 正则提取 ```sql 块降级渲染（标注「未结构化输出」）。

### 3.4 错误码

| 码 | 含义 | 前端表现 |
|----|------|---------|
| E1001 | 数据源连接失败 | 向导红卡 + 下一步锁定 |
| E1002 | 摄取中断 | 进度条标红，可重试（续摄） |
| E2001 | 模型端点无响应 | 错误卡 + [重试][切换路由]；自动续跑场景见 §13 |
| E2002 | 契约解析失败（重试后） | 降级 SQL 卡 |
| E3001 | EXPLAIN 不支持/失败 | 跳过干跑，标注「未经验证」 |
| E3010 | R15 硬阻断 | 错误卡（分方言文案五模板，SSOT §12.7）+ 交付原 SQL |
| E4001 | 密钥解密失败 | 设置页提示重新录入（不迁移） |

### 3.5 QueryState 与状态机

- QueryState TypedDict 全字段见 SSOT §5.1（含 verify_fallback_used 全局唯一回退额度凭证、resupply_used、write_confirmed 等）。
- 执行序铁律（validate）：Unicode 清洗 → R04 auto_limit AST 注入 → R15 优先 → R01-R14 → 标识符归一（normalize_identifiers→qualify）→ EXPLAIN 干跑（AST 去 LIMIT 副本，干跑串永不进 execute）。**所验即所执**：validate 后禁止任何重写，confirm_gate 展示串与 execute 逐字相同。
- 双库写入顺序：终态事件与 status 在 app.db 单事务先提交，checkpoint 后提交。
- 幂等/恢复七条 + 超时矩阵（30/5/35s）见 SSOT §5.4；备份恢复 ≠ 进程重启（§12.3）。

---

## 4. 规则集规格

### 4.1 AST 校验 R01-R15（sqlcheck/rules.py）

R15 硬阻断族（优先于 R01-R14，五断言：B1 多语句 / B2 解析失败 fail-closed / B3 exp.Into 文件变体 / B4 EXPLAIN ANALYZE / B5 契约外语句族 CALL·DO）；红警：R01 语句类型白名单外（fail-closed）、R02 UPDATE/DELETE 无 WHERE、R05 DDL/DCL、R06 非纯 SELECT（含 REPLACE INTO→confirm_gate）、R10 JOIN 缺 ON（进修正回路）、R13 幻觉标识符（online 红 → correct/need_tables；offline 黄警照常交付 D04）；黄警：R03 未用主键、R04 纯 SELECT 无 LIMIT（自动修正）、R07 未注册数据源/跨库、R08 函数不在能力卡、R09 大表 SELECT *、R11 子查询 >3 层、R12 大表前缀通配 LIKE、R14 分区表 WHERE 未含分区键。处置判据：可解释/可量化→告警+确认门；不可逆但可拍板→确认门；不可解释/绕过门本身→硬阻断（§1 P3 补注）。

### 4.2 verify_result 规则层（全过才 pass；回退共用全局 1 次额度 D09）

行数为 0（回退：放宽最近一个枚举/范围过滤）/ 关键列（SELECT 首列）全 NULL（回退）/ 行数 < EXPLAIN 估计 1%（回退）/ 汇总对账偏差 >0.1%（unverified 标注）/ 分区裁剪确认（仅记录）。LLM 层（feature.verify_llm）：local_only 置灰禁用（D07）；输入截断前 5 行 × 每格 50 字符；出境清单 E6 红标。

### 4.3 干跑安全四则 + EXPLAIN 三方言矩阵

禁 ANALYZE；干跑串永不进 execute；仅 online 且能力卡 explain≠none；失败降级 E3001。MySQL `EXPLAIN FORMAT=JSON`（rows 字段）/ Doris `EXPLAIN` 文本（cardinality regex，golden 固化）/ CH `EXPLAIN ESTIMATE`。

### 4.4 注入消毒（ingest/sanitize.py，FTS/vec 写入端唯一入口）

零宽字符剔除、bidi 控制符剔除、单值 ≤200 字符；必须在 bigram 展开与 embedding 之前执行。

### 4.5 提示词组装（llm/prompts.py）

信封 8 段 = system + dialect_card + memory + session_summary + schema + samples + metrics(命中) + question；输出预留 min(4096, 25%×total_ctx)；total_ctx 缺省 32768；裁剪 5 步：①减样例 ②减枚举(top8→top4) ③retrieval 重建减表 ④减记忆条目（最旧未命中） ⑤尾部确定性截断；token 估算 max(len//1.7 中文, tiktoken cl100k 英文)，偏差 ±15-20% 留余量。

### 4.6 检索供给（retrieval/）

FTS5 top-12（bm25 聚合到表级）+ 向量 top-4（distance≤0.35，FTS 命中 ≥6 表不触发）→ RRF k=60 按 table_id 去重 → `rrf_score DESC, table_name ASC` 确定性排序；库表 ≤30 或估算 ≤预算 → full 模式；模式切换滞后带 55-65% 保持上次模式；检索失败降级 full 截断。

---

## 5. 非功能要求（NFR）

### 5.1 性能（T4.3 口径，D02）

| 项 | 指标 |
|----|------|
| 首 token | 有 GPU 32B FP16 <3s；无 GPU ≤8B 量化 <3s；无 GPU 32B 量化记录基线不设硬指标；云端 <3s（网络另计） |
| 摄取 | 500 表 <60s |
| 前端 | 首屏 <1.5s；消息流 >200 条虚拟滚动（V2）；ResultTable >100 行虚拟化（行高 32px/overscan 10，V1） |
| 执行延迟 | execute 含 CSV 落盘时间，验收单列 |
| 超时矩阵 | query.timeout_s=30 / explain_timeout_s=5 / ch_client_timeout_s=35 |

### 5.2 安全与数据边界

- 监听 127.0.0.1:8321；Bearer token（首启生成）；CORS/Origin 同限定。
- 密钥：keyring 五款 → AITOSQL_FERNET_KEY 环境变量（仅内存）→ 首启生成；keyring 不可用 fail-closed 拒启；轮换走 E4001 重新录入不迁移；数据文件 0600；备份双封且剥离密文。
- 脱敏规则表统一适用三出口（回喂 LLM / 前端回显 / query_event 落库）：连接串→[HOST]/[PORT]、账号密码→[USER]/[CREDENTIAL]、IP 白名单仅放行 127.0.0.1/localhost、SQL 字面值→`?`。
- 出境清单矩阵 E1-E8；绝不外发：凭证、完整结果集、连接地址明文；local_only（三路全本地）= 零出境。
- SQL 安全三层防线 + R15 硬阻断；CH 强制 readonly=1，MySQL 只读建议级（纵深不对称 D05）。

### 5.3 可靠性

- 幂等：client_request_id 重发不重复执行；SSE seq 单调 + 幂等去重；门裁决与 stop CAS。
- 崩溃恢复：D03 启动清扫自动续跑（七条终稿 SSOT §5.4）；写操作续跑重过确认门；门不自动放行。
- 备份：每 4h VACUUM INTO，keep=10，可选加密；恢复语义 ≠ 进程重启。
- 降级链：检索失败→full 截断；契约解析失败→重试→降级 SQL 卡；EXPLAIN 失败→E3001 标注；vec 重建期→纯 FTS；记忆合并失败→静默下轮重试；模型离线→E2001。
- **强制约束（N7）**：会话压缩必须先完成 flush（待压缩轮次事实抢救进流水账）；flush 失败时压缩推迟，保证「40 轮压缩事实零丢失」golden 口径。

### 5.4 可维护性与工程质量

- 分层依赖守则 D1-D6（import-linter 进 CI）；三方库 import 收口（langgraph→graph/、sqlglot→sqlcheck/、fastembed→retrieval/vector.py）；SQL 文本拼装限 store/ + retrieval/ 两文件；前后端唯一耦合面 /api + SSE。
- pytest + golden（P1 末 ≥15 / P2 末 ≥30 / P3 末 ≥40 / T4.1 =50，三方言）；ruff + mypy strict-ish；uv.lock 锁次版本，升级须过 golden 三方言 + T4.2 门禁。
- 前端守则 V1-V4（虚拟化、token 单一来源 stylelint 禁裸 hex、REST 归 TanStack Query / zustand 只留 SSE 装配态+UI 态）。
- 业务 ID：`s_`/`q_` + ULID；时区全程本地；相对时间生成前归一为绝对区间（「上个月」默认自然月整月）。

### 5.5 兼容性

- MySQL ≥8.0（低于 8 注册探测标注能力降级）；Doris（2.1.5 实测基线）；ClickHouse（readonly=1）。
- Python 3.13 / Node（React 19 + Vite 6）；单命令启动 `scripts/dev.py`（uvicorn + 静态 web/dist 内嵌）。

### 5.6 配置项（setting 键，全表 40+ 见 SSOT §4；关键摘录）

| key | 默认 | 说明 |
|-----|------|------|
| route.generate / route.fix / route.ai | provider:model 引用 | 三路路由；全本地=local_only |
| query.auto_limit / timeout_s / max_fix_rounds | 1000 / 30 / 3 | 执行护栏 |
| query.write_confirm / ch_readonly | true / true | 写确认门 / CH 强制只读 |
| schema.mode / budget_pct / enum_top_n | auto / 60 / 8 | 供给预算（滞后带 55-65%） |
| schema.embed_model / embed_dim | BAAI/bge-small-zh-v1.5 / 512 | D01 定版，联动登记 |
| feature.clarify_gate / verify_rules / verify_llm / followups / partition_hint | true/true/false/true/true | 行为开关（砍项顺序：先 verify_llm 后 followups） |
| sample.fewshot_n / cache / auto_stale | 3 / true / true | 样例与缓存 |
| memory.enabled / max_tokens / consolidate_min_gap_min / inject | true / 4000 / 30 / true | 记忆模块 |
| session.compact_msgs / keep_msgs | 40 / 6 | 会话压缩 |
| backup.dir / keep / encrypt | data/backups/ / 10 / false | 备份 |
| export.dir / ttl_h / max_total_mb | data/exports/ / 24 / 2048 | 导出瞬态目录 |
| server.token / port | 首启生成 / 8321 | 本机服务 |
| sse.heartbeat_s / event_payload_kb | 15 / 16 | SSE |

---

## 6. 待确认 / TODO 汇总（承接 propose.md §8）

| # | 项 | 默认假设 | 责任阶段 |
|---|----|---------|---------|
| TODO-N1 | graph_layout 摆位存储 DDL 缺失 | 补 migration `{datasource_id, node_positions_json}` 或存 setting | 技术设计 |
| TODO-N2 | ai_suggestion 用词漂移（rejected/target） | 以 SSOT §3.1 为准 | 开发（走查核对原型文案） |
| TODO-N3 | metric_standard.filter 关键字列名 | 保留列名 + 统一引号/参数化 | 技术设计 |
| TODO-N4 | bge 权重首启下载依赖外网 | 首启检测缺失则引导下载 + 设置页状态显示 | 技术设计/发布 |
| TODO-N5 | 干净机器依赖安装策略（uv/npm） | uv.lock + 前端 dist 预构建随包，文档注明联网首装 | 发布（T4.4） |
| TODO-N6 | 设置页「通用/关于」字段未定义 | 通用=端口/数据目录展示；关于=版本/文档链接 | 原型设计 |
| TODO-N7 | 压缩 flush 失败边界 | flush 失败则压缩推迟（强制约束，已入 §5.3） | 开发（T3.5） |
| TODO-N8 | query_event 无 retention 体积增长 | 不做清理，文档提示；备份侧 keep=10 限流 | 技术设计（可选策略） |
| TODO-N9 | 导出 TTL 无 UI 提示 | 导出成功 toast 带「文件保留 24 小时」 | 原型设计 |

## 7. 验收口径索引

需求级验收标准 14 条见 `propose.md` §9；任务级验收（T0.1~T4.4，含每条交付物与依赖）以 SSOT §14 为执行依据；需求-任务追溯矩阵见 SSOT §15（无孤儿需求、无孤儿任务，本次分析复核一致）。
