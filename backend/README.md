# AItosql 个人版 · 后端（05-backend）

SSOT v3.2（评审闭环版）契约实现。技术栈：Python 3.13 / FastAPI+uvicorn / LangGraph +
langgraph-checkpoint-sqlite / sqlglot 30.18.* / stdlib sqlite3 + Pydantic v2（无 ORM，
计划 S5 人工批准偏离）/ PyMySQL / clickhouse-connect / httpx / fastembed+sqlite-vec /
keyring+cryptography。

## 目录结构

```
app/
  api/          L1 路由层（16 路由 + schemas/ 契约模型；不含业务规则）
  graph/        L2 编排（state/context/nodes/edges/runner；langgraph 唯此目录）
  ingest/       L3 摄取（sanitize/capability/keys/offline/online）
  retrieval/    L3 供给（keyword FTS5 / vector fastembed / supply RRF+预算装配）
  llm/          L3 生成（secrets/providers/registry/prompts 组装器）
  sqlcheck/     L3 校验（clean/ddl/dialects/rules R01-R15/explain；sqlglot 唯此目录）
  exec/         L3 执行（runner 连接池+KILL / meta 预览+CSV+清扫）
  verify/       L3 结果校验（rules §9.2 五项 / llm 校验层）
  store/        L4 持久化（db/settings/models/providers/datasources/semantics/
                sessions/suggestions/queries/samples/memory/backup）
  migrations/   0001-0005（幂等执行器，失败拒启报迁移号）
configs/dialects/  三方言能力卡（doris 为 SSOT §8.2 原文）
scripts/dev.py     标准启动：单实例锁 + 首启打印一次 server.token
docs/              设计/运维文档（运维-备份与事件生命周期.md 等）
tests/             unit + golden（18 离线用例：mysql 6 / doris 6 / clickhouse 5 / 澄清 1；
                   滚动门槛目标 P2≥30/P3≥40/T4.1=50；golden_engine 真实引擎分组另计）
```

## 环境与命令

```bash
uv sync --extra dev            # 或 pip install -e .[dev]
pytest tests -q                # 单元 + golden 离线用例（golden_engine 真实引擎组默认 skip）
ruff check app scripts tests   # 0 错误为基线
python -m mypy app             # 0 错误为基线
python -m tests.golden.runner  # golden 滚动门槛报告
lint-imports                   # import-linter 分层契约（D1/D3）
python scripts/dev.py          # 标准启动（单实例锁 + 首启打印一次 token）
```

**标准启动命令是 `python scripts/dev.py`**。直接 `uvicorn app.main:app` 仅供受控
单进程开发/测试（ASGI 工具导入、测试挂载等）；应用 lifespan 会自行抢数据目录
`.aitosql.lock` 单实例锁，多 worker 或多进程指向同一数据目录时**启动即明确失败**
（拒绝静默双份 D03 续跑/KILL 寻址错乱）。外部进程管理器自行 fork 的形态无法可靠
探测，部署纪律 = 同一数据目录仅允许一个实例（见 docs/运维-备份与事件生命周期.md §5）。

真实引擎集成测试（需容器/服务，CI 分组，缺环境明确 skip）：

```bash
$env:AITOSQL_TEST_ENGINE="1"
$env:AITOSQL_TEST_MYSQL_DSN="mysql://root:secret@127.0.0.1:3306/test"
$env:AITOSQL_TEST_CH_DSN="clickhouse://default@127.0.0.1:8123/default"
pytest -m golden_engine -q
```

数据目录（环境变量 `AITOSQL_DATA`，默认 ./data）：app.db / checkpoints.db /
backups/ / exports/ / models/。密钥链：`AITOSQL_FERNET_KEY` 或 OS keyring（fail-closed）。

备份内容/密钥剥离/可选加密/排除项、离线恢复步骤、query_event 无自动 TTL 的
生命周期说明，以及 `backup.keep`（最小 1）滚动语义：见
[docs/运维-备份与事件生命周期.md](docs/运维-备份与事件生命周期.md)。

## 构建期验证记录（2026-10-15，沙盒 python 3.13）

- `python -m compileall app scripts tests`：0 错误。
- `ruff check`（0.7.4，pyproject 配置）：0 错误（RUF001/002/003 中文标点歧义按
  工作区中文注释风格整体 ignore）。
- OpenAPI 冒烟：55 路径全齐（含 E5 快照、SSE 续播、门裁决 CAS、prompt-preview、
  graph layout 摆位）；lifespan 冒烟：迁移 0001~0006 幂等执行、`/api/health`
  二次调用均 `ok`（22 对象建全）。
- 探针实测并修复（构建期 ≤3 轮自检内闭环）：
  1. sqlite 3.53.1 对 `DEFAULT '{}'` + `(datetime('now)')` 组合解析歧义
     → 0005 迁移改 `DEFAULT CURRENT_TIMESTAMP`（语义等价，注释载明）；
  2. sqlglot 30.18 运行时形态：`KILL QUERY` 解析为 exp.Kill（探针环境 PARSE_FAIL）、
     clickhouse 方言 `EXPLAIN ANALYZE` 退化为 Command → R15 同步断言（同语义失败关闭）；
     `exp.AlterTable` 不存在 → exp.Alter；
  3. FTS5 `bm25()` 不可入聚合上下文 → 应用层聚合；bigram 混排合并 bug（「订单ID」
     单 token 化致召回失效）→ CJK/英文词双缓冲分词修复；
  4. query_event 16KB 截断原实现产出非法 JSON → 改为
     `{truncated, original_bytes, preview}` 合法 JSON 骨架。

## 修复回路记录（06-analysis 第一轮 → 本轮修复）

门禁结论 BLOCK（P0×4 / P1×18）。后端侧修复全部落地，契约冻结清单见
[contracts-sync.md](contracts-sync.md)（contracts.md 本体在 03-design/，超出写域，
由上游合入）：

- **P0-1 E5 快照**：冻结 `{session, items, last_seq_by_query}`（items 平铺 +
  续播游标顶层字典；item 带 preview/clarify_choice/recovered）——
  `store/queries.py` + 契约测试 `test_queries.py::test_e5_snapshot_*`。
- **P0-2 SSE 帧**：非落库事件（result/stream_resumed）**省略 id 行**，落库事件
  必写 `id:{qid}:{seq}`——`api/sse.py::_frame` + `test_repair_contracts.py::test_sse_frame_*`。
- **P0-3 路由引用**：冻结 `{provider_id}:{model}` 数字 id 引用；写入期 422 fail-fast、
  解析期 BizError(E4420) 不再冒泡 ValueError 误报 E2001——`api/settings.py` +
  `llm/registry.py` + `test_resolve_route_*`。
- **P1 闭环接缝**：stop 已终态幂等 200（P1-2）；设置包络冻结（P1-3）；摆位端点冻结
  嵌套形态（P1-4）；**补 `GET /tables/{id}/columns`**（P1-5a 新增端点）；建议队列冻结
  `{groups,total}` + confidence 非空（P1-6）；prompt-preview `datasource_id` 必填冻结
  （P1-7）；**memory toggle 请求体可省略、服务端取反**（P1-8）；备份包络冻结
  `{items,restore_hint}`/`{file}`（P1-10）。
- **P1 逻辑**：修正轮数耗尽落 failed + E2004 error 事件、`_finalize` 不再静默 done
  （P1-14）；validate 执行序 R15 先于 auto_limit，注入解析失败兜底归 B2（P1-15）。
- **P1 性能**：pack_preview 增量字节累计 O(n)（F-1）；auto 模式 token 估算按元数据指纹
  缓存（F-2）；consolidate/providers 不持 sqlite 连接跨 LLM 往返（F-3 拆相两段式）。
- **P1 需求缺口**：摄取/导入后建议生产（ingest/suggest.py：规则关系推断 + route.ai
  注释补全）+ CSV 模板 / AI 草稿端点（R-1）；会话压缩接线 `_maybe_compact` + E5 透出
  summary（R-2）；D03 续跑置 `recovered=1` 经 E5 透出（R-3）。
- **P2/P3 随批硬化**：异常分类映射（Q-3）、stop CAS 收窄（Q-4）、幂等创建竞态回查
  （Q-5）、摄取并发守卫 + 续摄文案降级（Q-6）、verify LLM 层 fail-closed（Q-7）、
  R13 别名感知字段核查（Q-8）、数据源聚合计数（Q-10）、metrics 保存回传完整行（Q-11）、
  `_tasks`/`_hub` 清理（Q-16/F-5）、S-1~S-6 全项（异常不出网 / 标识符引号白名单 /
  CSV 公式注入转义 / 数据文件 0600 / 备份秒级文件名 + **backup.dir 白名单收敛** /
  Bearer 定常时比较）、连接池信号量 + ping + 变更失效（F-4）。

**验证（2026-10-16，本机 python 3.12.7 + .testenv 依赖镜像）**：
`pytest tests -q` → **113 passed**（含新增 `tests/unit/test_api/test_repair_contracts.py`
12 条回归锚点）；`compileall app scripts tests` 0 错误。
ruff/mypy/import-linter 本机无对应模块，维持留 CI 首跑（见下）。
⏳ 前端配套项（另一写域）与真实后端 E2E 走查见 contracts-sync.md 各条 ⏳ 标记。

## 已知回写/TODO（详见交付摘要与 contracts-sync.md）

- **契约回写（修复回路）**：E5 包络 / SSE 帧 id 语义 / route.* 值格式 / 设置包络 /
  摆位端点 / 建议队列包络 / 备份包络 / prompt-preview 参数 + 3 个新增端点 +
  stop 幂等与 toggle 缺省语义 + E2004/E3002/E5000 错误码——全部冻结文本见
  [contracts-sync.md](contracts-sync.md)，待上游合入 contracts.md。
- C8 增补错误码：E4010/E4040/E4090/E4420/E5001（回写 contracts）。
- C5 QueryState 附注：内部协调键 hard_blocked/needs_correct/stop_requested 等（回写）。
- migrations 对象数 22 vs 上游"13/17 表"口径不一致（回写 plan/tasks 计数）。
- langgraph ≥1.x 的 interrupt/Command(resume) 语义已按 1.2.15 验证（R-1 spike 关闭，
  回写 plan V-5）；FastAPI _IncludedRouter 惰性形态（回写 design D-30 注记）。
- mypy / ruff / import-linter 本机已全绿（2026-10-31 修复轮：
  ruff 0 错误、mypy 0 错误/99 文件、5 项分层契约全 kept），CI 仅需保持基线。
- golden 已扩至 18 离线用例（mysql 6 / doris 6 / clickhouse 5 / 澄清 1，2026-10-31）；
  滚动门槛 P2≥30/P3≥40/T4.1=50 仍为目标；golden_engine 三方言容器用例 CI 分组
  （marker 已注册，DSN 环境变量驱动，缺环境明确 skip）。
- 部署安全基线：数据库账号最小权限由部署者负责（AST 校验 ≠ 数据库权限控制），
  写 SQL 仅对有意启用的可信账号开放；只读账号模式/写账号分离/资源组为待决策增强
  （见 docs/运维-备份与事件生命周期.md §1）。
