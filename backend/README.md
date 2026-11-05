# AItosql 后端

按根目录《整体需求解决方案.md》契约实现。技术栈：Python 3.13 / FastAPI + uvicorn /
LangGraph + langgraph-checkpoint-sqlite / sqlglot 30.18.* / stdlib sqlite3 + Pydantic v2
（无 ORM）/ PyMySQL / clickhouse-connect / httpx / fastembed + sqlite-vec /
keyring + cryptography。

## 目录结构

```
app/
  api/          L1 路由层（路由 + schemas/ 契约模型；不含业务规则）
  graph/        L2 编排（state/context/nodes/edges/runner；langgraph 唯此目录）
  ingest/       L3 摄取（sanitize/capability/keys/offline/online）
  retrieval/    L3 供给（keyword FTS5 / vector fastembed / supply RRF+预算装配）
  llm/          L3 生成（secrets/providers/registry/prompts 组装器）
  sqlcheck/     L3 校验（clean/ddl/dialects/rules R01-R15/explain；sqlglot 唯此目录）
  exec/         L3 执行（runner 连接池+KILL / meta 预览+CSV+清扫）
  verify/       L3 结果校验（rules / llm 校验层）
  store/        L4 持久化（db/settings/models/providers/datasources/semantics/
                sessions/suggestions/queries/samples/memory/backup）
  migrations/   0001-0012（幂等执行器，失败拒启报迁移号）
configs/dialects/  方言能力卡（mysql/doris/clickhouse/postgresql/hive/starrocks）
scripts/dev.py     标准启动：单实例锁 + 首启打印一次 server.token
docs/              设计/运维文档
tests/             unit + golden 离线用例（mysql/doris/clickhouse + 澄清）；
                   golden_engine 真实引擎分组默认 skip
```

## 环境与命令

```bash
uv sync                        # 安装依赖（含 dev 组）
pytest tests -q                # 单元 + golden 离线用例
ruff check app scripts tests   # 0 错误为基线
python -m mypy app             # 0 错误为基线
python -m tests.golden.runner  # golden 报告
lint-imports                   # import-linter 分层契约
python scripts/dev.py          # 标准启动（单实例锁 + 首启打印一次 token）
```

**标准启动命令是 `python scripts/dev.py`**。直接 `uvicorn app.main:app` 仅供受控
单进程开发/测试（ASGI 工具导入、测试挂载等）；应用 lifespan 会自行抢数据目录
`.aitosql.lock` 单实例锁，多 worker 或多进程指向同一数据目录时**启动即明确失败**
（拒绝静默双份续跑/KILL 寻址错乱）。外部进程管理器自行 fork 的形态无法可靠
探测，部署纪律 = 同一数据目录仅允许一个实例（见 docs/运维-备份与事件生命周期.md §5）。

真实引擎集成测试（需容器/服务，缺环境明确 skip）：

```bash
$env:AITOSQL_TEST_ENGINE="1"
$env:AITOSQL_TEST_MYSQL_DSN="mysql://root:secret@127.0.0.1:3306/test"
$env:AITOSQL_TEST_CH_DSN="clickhouse://default@127.0.0.1:8123/default"
pytest -m golden_engine -q
```

数据目录（环境变量 `AITOSQL_DATA`，默认 ./data）：app.db / checkpoints.db /
backups/ / exports/ / models/。密钥链：`AITOSQL_FERNET_KEY` 或 OS keyring（fail-closed）。

备份内容/密钥剥离/可选加密/排除项、离线恢复步骤、query_event 生命周期说明，
以及 `backup.keep`（最小 1）滚动语义：见
[docs/运维-备份与事件生命周期.md](docs/运维-备份与事件生命周期.md)。

## 部署安全基线

数据库账号最小权限由部署者负责（AST 校验 ≠ 数据库权限控制），写 SQL 仅对有意
启用的可信账号开放；只读账号模式/写账号分离/资源组为可选增强方向
（见 docs/运维-备份与事件生命周期.md §1）。
