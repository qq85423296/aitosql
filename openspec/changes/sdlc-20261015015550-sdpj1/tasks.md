# AItosql 个人版 Text2SQL · 可追溯任务清单（tasks）

> 阶段产物：03-design / tasks ｜ 运行：sdlc-20261015015550-sdpj1
> 上游：同目录 `design.md`（D-xx 设计条目）+ `contracts.md`（C1-C10 契约）；需求追溯：spec.md FR-Q1~Q14 / FR-D1~D13 / FR-S1~S4 / FR-C1~C8 / BR-1~BR-16；交付节奏沿用 SSOT §14（Phase 0-4，golden 滚动门槛 P1≥15/P2≥30/P3≥40/T4.1=50，每 Phase 20% 缓冲，砍项顺序 verify_llm → followups）。
> 追溯约定：任务 ID 沿用 SSOT §14 的 T 编号（保持 §15 矩阵连续）；**每项任务的「追溯」列反向指向 design.md 的 D-xx 条目**；「验证任务」（V 组）对应 design 中全部「按既有证据落地」的外部事实确认；「待确认阻塞」（B 组）对应 design 段 10 TODO。

## 任务总览

- 分层预设：`python-fastapi`（后端：migrations → models → 领域模块 → graph → api → 测试）+ `web-react`（前端：tokens/stores/api → 组件 → 页面 → 走查），工作区无既有代码（glob 实证），全部为新建任务
- 实现任务：**27 项**（T0.1-T0.4 / T1.1-T1.10 / T1.9a-c / T2.1-T2.6 / T3.1-T3.5 / T4.1-T4.4，含本次新增 T0.5）
- 验证任务：**6 项**（V-1~V-6）
- 待确认阻塞：**7 项**（B-1~B-7，= design 段 10 TODO-1~7）

---

## Phase 0 · 工程骨架（约 3 天）

| ID | 任务 | 涉及文件 | 验收标准 | 追溯 | 覆盖需求 | 依赖 |
|----|------|---------|---------|------|---------|------|
| T0.1 | 后端骨架：pyproject.toml（uv，锁次版本）+ FastAPI 入口 + store/db.py + migrations 0001~0005 执行器 | `pyproject.toml`、`app/main.py`、`app/store/db.py`、`app/migrations/0001~0005.sql` | `/api/health` 200；17 张表/视图建全（含 query_event 与本次新增 graph_layout）；迁移幂等、失败拒启报迁移号 | D-01、D-39、D-18 | 全 BR 地基 | — |
| T0.2 | 前端骨架：四页路由 + tokens.css + Rail + CONVENTIONS.md | `web/`（Vite 6 + React 19 + TS + Tailwind v4 + shadcn/ui）、`web/src/theme/tokens.css`、`web/CONVENTIONS.md` | 四页可达；token 单一来源（@theme 桥接 + stylelint 禁裸 hex 生效）；视觉对照 `prototype/*.html` 走查通过 | D-40、D-20 | 全 FR 地基 | — |
| T0.3 | 提供商与密钥：三适配器 + registry + secrets（keyring 五款 fail-closed）+ 提供商 UI | `app/llm/providers.py`、`registry.py`、`secrets.py`、`app/api/providers.py`、`web/.../ProviderList.tsx`、`ProviderDrawer.tsx` | 增删改/测试/模型拉取通；密钥永不回显；keyring 不可用拒启；测试失败红卡含「密钥未外发」；空模型 ID 校验文案一致 | D-34、D-10 | FR-C1/C2、BR-11 | T0.1, T0.2 |
| T0.4 | golden 框架 + CI：runner + ruff/mypy/import-linter 契约 | `tests/golden/runner.py`、`tests/golden/cases/*.yaml`（格式 contracts §18.3 对齐）、CI 配置 | 3 条样例用例跑通；import-linter D1-D6 分层契约检查通过 | D-43 | 质量地基 | T0.1 |
| T0.5 | **（本次新增）**首启引导与数据目录初始化：server.token 生成、data/ 0600、migrations 触发、bge 权重存在性检测 + 缺失引导下载/降级提示 | `scripts/dev.py`、`app/config.py`、`app/main.py` 启动钩子 | 干净目录首启：token 生成且仅 CLI 打印一次；权重缺失时 `schema.vector=off` 降级纯 FTS + 设置页状态可见 | D-42、D-08 | T4.4 前置 | T0.1 |

## Phase 1 · 查询主链路（约 2 周）

| ID | 任务 | 涉及文件 | 验收标准 | 追溯 | 覆盖需求 | 依赖 |
|----|------|---------|---------|------|---------|------|
| T1.1 | M1 在线摄取：三方言元数据 + 键探测 + 消毒 + 能力卡 | `app/ingest/online.py`、`keys.py`、`sanitize.py`、`capability.py`、`app/api/datasources.py`（ingest SSE） | 三方言真实库摄取成功；摄取日志计数「主键/分区键（月·日）/分桶键」正确；消毒先于 bigram/embedding（顺序单测）；重摄取不覆盖 comment_custom/field_standard_id | D-32、D-11、D-41 | FR-D3/D4、BR-1 | T0.1 |
| T1.2 | M2 schema 供给：FTS5 bigram + sqlite-vec + RRF 融合 + 预算装配 + 滞后带 + need_tables | `app/retrieval/keyword.py`、`vector.py`、`supply.py` | golden 注入表集命中率 ≥ 基线；`rrf_score DESC, table_name ASC` 确定性排序单测；滞后带 55-65% 保持上次模式；检索失败降级 full 截断 | D-07、D-08、D-33 | BR-2 | T1.1 |
| T1.3 | M3 生成：8 段信封组装 + 5 步裁剪 + 契约校验/重试/降级 | `app/graph/nodes.py`（generate）、`app/llm/prompts.py` | golden 10 条契约合法（C4.1 Schema）；解析失败重试 1 次生效、二次失败降级 SQL 卡；裁剪顺序单测（样例→枚举→减表→记忆→截断）；token 估算偏差口径留余量 | D-09、D-34 | BR-3、BR-12 前半 | T1.2, T0.3 |
| T1.4 | M5 校验：R01-R15 全规则 + 执行序铁律 + 干跑三方言矩阵 | `app/sqlcheck/clean.py`、`rules.py`、`explain.py`、`dialects.py` | 每规则 ≥2 用例；**R15 五断言含探针复现用例**（`SELECT 1;;SELECT 2`、INTO OUTFILE 四语序、INTO @var、CALL、DO、EXPLAIN ANALYZE 内层 Update）；auto_limit AST 注入先于校验；干跑去 LIMIT 副本不进 execute | D-03、D-04、D-35 | BR-5 | T0.1 |
| T1.5 | M6 执行：工作/管理连接分离 + CONNECTION_ID + 超时 + CSV 落盘 | `app/exec/runner.py`、`meta.py` | 三方言执行；exec_meta 五字段齐全（rows/scan_rows/duration_ms/data_max_time/connection_id）；30s/5s/35s 超时矩阵生效；纯 SELECT 成功全量落 `exports/{query_id}.csv`（UTF-8 BOM/RFC4180）；CH readonly=1 强制 | D-36、D-02、D-16 | BR-7 | T1.1 |
| T1.6 | verify_result + 修正回路：规则层 5 检查 + 全局 1 次回退 + 指纹振荡 | `app/verify/rules.py`、`app/graph/nodes.py`（verify/correct） | 故障注入收敛；回退全局共享 1 次（verify_fallback_used 凭证）；仍不过照常交付 result_unverified=true；指纹重复即终止交付标 failed；correction 事件入回路即发 | D-12、D-13、D-37 | BR-8、BR-9 | T1.4, T1.5 |
| T1.7 | 双门：clarify_gate / confirm_gate + resume CAS + clarify_pref 沉淀 | `app/graph/nodes.py`（双门）、`app/api/queries.py`（resume/stop）、`app/store/`（clarify_pref DAO） | 写操作暂停→确认→执行；取消标注「已取消，语句未执行」；澄清裁决写 clarify_pref 且同步直写 memory_log；同数据源同 term 下次免问+沉淀提示；CH 只读文案（§9.4）；非 waiting_* resume 返回 409 | D-05、D-22、D-14 | FR-Q6/Q8、BR-4/6 | T1.3 |
| T1.8 | SSE + 幂等 + 停止 + D03 自动续跑 | `app/graph/runner.py`、`app/api/sse.py`、`app/api/queries.py`、`app/api/sessions.py`（E5） | 16 事件落 query_event（seq 单调）；Last-Event-ID 增量/无头全量续播幂等；同 client_request_id 重发不重复执行；stop+KILL 生效（CH 注明无通道）；启动清扫：running 自动续跑、waiting_* 不自动放行、checkpoint 缺失 failed+标注、写操作续跑重过门；双库写入顺序单测（app.db 先提交） | D-01、D-05、D-06、D-21、D-31 | BR-10 | T1.6 |
| T1.9a | 查询页框架：SessionDrawer + DsSwitcher + 空态/横幅 | `web/.../QueryPage.tsx`、`SessionDrawer.tsx`、`DsSwitcher.tsx` | 对照原型走查；⌘K=列表内过滤聚焦（D11 口径）；删除行内二次确认；归档折叠组；离线徽标与「复制 SQL」联动；横幅两态（续播琥珀/自动恢复绿） | D-40、D-21 | FR-Q1/Q2/Q13 | T1.8 |
| T1.9b | 消息流渲染：answer/ 组件族 11 个 | `StageCard / ReasoningBlock / AssumptionChips / ClarifyCard / SqlBlock / ConfirmBar / ResultTable / VerifyBlock / CorrectionTimeline / ErrorCard / Followups / MetaNote` | 7 种消息态渲染正确（成功/写门/澄清/离线 E2001/0 行未验证/R15 硬阻断 E3010/自动恢复）；Stream >200 条虚拟滚动 + 底部锚定（V2）；ResultTable >100 行虚拟化（行高 32/overscan 10，V1） | D-40、D-20 | FR-Q4~Q11 | T1.9a |
| T1.9c | 管线与右侧面板：StageCard SSE 驱动 + ContextPanel | `StageCard.tsx`、`ContextPanel.tsx`（SchemaTree/QueryDetail） | 阶段卡 8 枚举带 round 由 stage 事件驱动；Esc/按钮停止触发 stop+KILL；Schema 树键徽章 + 「+插入表名」进 Composer；详情含耗时分解/扫描量/数据时间 | D-40、D-06 | FR-Q3/Q12 | T1.9a |
| T1.10 | LLM 层结果校验（可选层） | `app/verify/llm.py`、`web/.../PromptSection.tsx`（开关位） | feature.verify_llm 开关生效；local_only 置灰禁用（D07）；输入截断前 5 行 × 每格 50 字符；出境清单 E6 红标联动 | D-12、D-37 | BR-8 后半 | T1.6 |

## Phase 2 · 数据源治理（约 2 周）

| ID | 任务 | 涉及文件 | 验收标准 | 追溯 | 覆盖需求 | 依赖 |
|----|------|---------|---------|------|---------|------|
| T2.1 | 数据源 CRUD + 在线向导 3 步 | `app/api/datasources.py`、`web/.../DsList.tsx`、`DsWizard.tsx` | 注册→测试（E1001 失败红卡 + 下一步锁定）→摄取全流程；向导双栏（表单+摘要/预告/安全边界 aside） | D-30、D-32 | FR-D1/D2 | T1.1 |
| T2.2 | 表清单/详情/编辑/提示词视图 | `app/api/tables.py`（+columns）、`web/.../TableList.tsx`、`TableDetail.tsx` | 注释行内编辑即时保存；在线库字段名/类型只读；键开关人工修正；重摄取不回滚人工字段；提示词视图与真实注入逐字一致（同 D-09 生成器，含「分区键·按月」标注） | D-09、D-32、D-38 | FR-D5~D8 | T2.1 |
| T2.3 | 关系管理 + 双语 + ER 图谱（含摆位持久化） | `app/api/relations.py`、`/datasources/:id/graph`、`web/.../RelGraph.tsx` | 图谱缩放/拖拽/节点信息卡与原型一致；边三态（fk 实线蓝/inferred 虚线琥珀/manual 绿）；推断关系 pending 确认后才注入；**摆位保存/读取走 graph_layout（0005），缺失时字典序确定性平铺** | D-18、D-32、D-40 | FR-D9/D10 | T2.2 |
| T2.4 | 标准/维度/指标 CRUD | `app/api/standards.py`、`dimensions.py`、`metrics.py`、`web/.../StandardsTab.tsx` 等三 Tab | 绑定后提示词视图即时变化；维度枚举动态行管理；指标表达式保存时 sqlglot 校验（未知标识符黄警不阻断）；metric_standard.filter 列全链路引号/参数化（D-19 checklist 项） | D-19、D-34、D-38 | FR-D11 | T2.2 |
| T2.5 | AI 补全 + 建议评审队列 | `app/store/suggestions.py`、`app/api/suggestions.py`、`web/.../SuggestionDrawer.tsx` | 建议→评审→采纳→注入闭环；四类 payload（C6.8）落库；采纳映射正确（comment→comment_custom 等四路）；批量采纳/忽略；采纳前不进提示词（P5） | D-34、D-38 | FR-D12、BR-14 | T2.4, T1.3 |
| T2.6 | 离线数据源三通道 | `app/ingest/offline.py`、向导离线支路 | DDL/CSV/AI 三通道；DDL 部分失败行标红、成功部分可入库；离线库查询只生成不执行（generate_only）；R13 降级黄警照常交付（D04）；执行键替换「复制 SQL」 | D-32、D-03 | FR-D13、BR-1 后半 | T2.2 |

## Phase 3 · 样例库与设置收尾（约 1 周）

| ID | 任务 | 涉及文件 | 验收标准 | 追溯 | 覆盖需求 | 依赖 |
|----|------|---------|---------|------|---------|------|
| T3.1 | 样例库全流程：stale 治理 + 复验 + semantic_rev 缓存 | `app/store/samples.py`、`app/api/samples.py`、`web/.../SamplesPage.tsx` | schema 变更→引用样例置 stale（stale_reason 记字段名）→EXPLAIN 复验回 on；缓存命中三条件（question_norm ∧ on ∧ semantic_rev 相等）直返 SQL 标「复用已验证结果」；五因子任一变更缓存失效 | D-15、D-38 | FR-S1~S4、BR-13 | T1.9b |
| T3.2 | 设置剩余分组 + 备份 | `app/api/settings.py`、`backup.py`、`app/store/backup.py`、`web/.../SafetySection.tsx`、`SchemaSection.tsx`、备份分组 | 开关即时生效；备份可恢复且剥离密文、不含 checkpoints.db 与 exports/；恢复提示含「不适用自动续跑」；出境清单矩阵随路由联动（E6 红标）；导出 toast 带「文件保留 24 小时」 | D-16、D-17、D-11 | FR-C5~C8、BR-15 | T0.3 |
| T3.3 | 提示词维护 + 组装预览 | `app/llm/prompts.py`（预览端点）、`app/api/settings.py`（/settings/prompt-preview）、`web/.../PromptSection.tsx`、`PreviewDrawer.tsx` | 预览 8 段与真实注入逐字一致（含输出预留段）；系统模板变量校验/恢复默认；能力卡方言 Tab 编辑 | D-09、D-41 | FR-C4、BR-12 | T1.3 |
| T3.4 | 复核包 + 追问建议 + CSV 导出端点 | `web/.../ResultTable.tsx`（复核/导出按钮）、`app/api/queries.py`（export.csv）、followups 生成接线 | 复核包字段与 C9.1 模板逐字一致；导出 FileResponse 流式 + 响应头回显 data_max_time；TTL/LRU/孤儿清扫生效；追问 chips 点击即新提问（feature.followups 开关） | D-16、D-06 | FR-Q9 复核、FR-Q14、D14 | T1.9b |
| T3.5 | 记忆与会话压缩 | `app/store/memory.py`、`app/api/`（memory 端点）、`web/.../MemorySection.tsx`、flush/合并接线、压缩节点 | 澄清裁决→流水账可见→合并→下次生成注入生效；**40 轮压缩事实零丢失（golden）**；压缩先 flush、flush 失败压缩推迟（spec §5.3 强制）；总账停用即时不再注入；容量条 >80% 琥珀 | D-14、D-38 | BR-16（+T1.7 裁决直写） | T1.7, T3.2 |

## Phase 4 · 硬化与发布（约 1 周）

| ID | 任务 | 验收标准 | 追溯 | 依赖 |
|----|------|---------|------|------|
| T4.1 | golden 扩充至 50 条（三方言，含澄清门/写门/回退/0 行/R15 用例） | 一次通过率达标并记录基线 | D-43 | T1-T3 全量 |
| T4.2 | 崩溃恢复/幂等/长会话故障注入 | 五条全过：① running 重启自动续跑至终态；② waiting_* 恢复后可裁决且不自动放行；③ SSE 续播无重复无丢失（seq 单调+幂等）；④ 写操作 crash 续跑重过确认门；⑤ checkpoint 缺失降级 failed+标注 | D-01、D-05、D-06 | T1.8, T3.5 |
| T4.3 | 性能矩阵 | 有 GPU 32B FP16 首 token <3s；无 GPU ≤8B 量化 <3s；无 GPU 32B 量化记录基线；云端 <3s（网络另计）；摄取 500 表 <60s；首屏 <1.5s；execute 延迟含落盘时间单列 | D-02、D-36 | T1-T3 全量 |
| T4.4 | 单命令启动 + 前端产物内嵌 | 干净机器 `scripts/dev.py` 一键起；`/api/health` 200；web/dist 内嵌（依赖策略按 B-2 裁决执行） | D-42、D-08 | T0.5, T3.x |

---

## 验证任务（V 组：外部事实确认，编码前/编码早期完成）

| ID | 任务 | 方式 | 验收 | 追溯 |
|----|------|------|------|------|
| V-1 | 复核 sqlglot 30.18 探针结论在当前锁定版本可复现 | CI 中重跑 `.roundtable/evidence/probe_r2_r15.py`（固化脚本入 tests/） | 输出与 `probe_r2_out.txt` 逐项一致（INTO 四语序 PARSE_FAIL / `SELECT 1;;SELECT 2`=[Select,None,Select] / CALL→Command / Describe.style=ANALYZE） | D-04 |
| V-2 | 原型设计令牌提取核对 | 从 `prototype/*.html` `:root` 提取 token 值与 tokens.css 对比脚本 | 深色 12 令牌值逐字一致；浅色令牌同名覆盖 | D-40 |
| V-3 | fastembed + bge-small-zh-v1.5 离线/首启行为实测 | 干净 venv 断网/联网两态首启 | 断网时报错可捕获、可降级纯 FTS；联网下载完成时间记录（供 B-1 裁决） | D-08 |
| V-4 | keyring 五款后端可用性矩阵 | Win（WinVault）/macOS（Keychain）/Linux（SecretService/kwallet/无桌面）实测 | 各 OS 首选后端可用；无桌面 Linux 确认 fail-closed 与 `AITOSQL_FERNET_KEY` 回退顺序符合 §12.4 | D-10 |
| V-5 | langgraph ≥0.2 interrupt/Command(resume) API 形态 spike | 最小可运行图：interrupt → checkpointer 落库 → 进程重启 → resume | interrupt 负载可从 checkpoint 恢复；resume 后图续跑；结论回写 design 段 10 TODO-4 | D-05、D-31 |
| V-6 | clickhouse-connect readonly/KILL 行为 + Doris EXPLAIN cardinality 样本 | CH readonly=1 下 KILL 尝试与查询取消实测；Doris 2.1.5 `EXPLAIN` 真实输出取样 | 行为矩阵落文档（供 D-36 UI 文案）；cardinality 正则按真实样本固化进 golden | D-35、D-36 |

## 待确认阻塞项（B 组 = design 段 10 TODO）

| ID | 阻塞内容 | 影响范围 | 建议确认方式/时机 |
|----|---------|---------|------------------|
| B-1 | bge 权重分发策略（TODO-1） | T0.5、T4.4 | 发布形态评审（随包分发 vs 首启下载）；P3 末前裁决 |
| B-2 | 干净机器依赖安装策略（TODO-2） | T4.4 | 同上（uv.lock + dist 预构建随包 or 联网首装文档化） |
| B-3 | query_event retention（TODO-3） | 长期磁盘；备份体积 | 默认不清理 + 文档提示；首个版本周期后复评 |
| B-4 | langgraph interrupt API 细节（TODO-4） | T1.7/T1.8 | V-5 spike 关闭 |
| B-5 | clickhouse-connect KILL/readonly 矩阵（TODO-5） | T1.5、stop 文案 | V-6 实测关闭 |
| B-6 | Doris EXPLAIN 正则形态（TODO-6） | T1.4 干跑行数提取 | V-6 取样固化 |
| B-7 | fastembed 摄取吞吐（TODO-7） | T1.1/T4.3 摄取 500 表 <60s | T1.1 实测，不达标降级 FTS-only 并提示 |

## 反向校验（无孤儿）

- 每个 D-xx 条目至少被一项任务引用：D-01~D-22 决策与 D-30~D-43 模块在 T/V 组均有落点（抽查：D-13→T1.6；D-19→T2.4；D-18→T0.1+T2.3；D-21→T1.8+T1.9a；D-41→T1.1+T3.3；D-43→T0.4+T4.1）。
- 每项任务至少追溯一个 D-xx 且标注覆盖需求（T0.1/T0.2/T0.4 为地基类，覆盖列已注明「全 BR/全 FR 地基」）。
- spec.md FR/BR 65 条与 SSOT §15 矩阵一致，本清单未新增无需求来源的任务（T0.5 例外：源自 propose N4/N5 待确认项的落地，已在覆盖列注明 T4.4 前置）。
