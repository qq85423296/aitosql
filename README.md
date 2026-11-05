# AItosql

> 个人版 Text2SQL 助手：**智能优先 · 本地部署 · 单用户**。
> 支持 MySQL / Doris / ClickHouse，中文界面，AI 建议由人裁决后才生效。

## 特性

- **智能优先查询**：自然语言 → SQL，歧义给假设不打断；高风险影响（impact=high）才走澄清门
- **三层安全防线**：AST 规则告警（R01–R14，不阻断）→ 写操作确认门 → 环境级只读；多语句/文件副作用等由 R15 硬阻断
- **数据源管理**：在线/离线摄取、五 Tab 详情页（表/关系/口径/样例/图谱）、DDL 导入、连接测试
- **AI 补全统一工作区**：一键为整库/单表生成注释、关系、标准绑定建议；多泳道进度可视；建议先入评审队列，**人工采纳后才写入元数据**
- **崩溃恢复**：查询任务断点续跑，写操作续跑重过确认门
- **本地优先**：默认 local_only，云路由显式开启；数据不出境有清单可查

## 架构

```
backend/   FastAPI + LangGraph（SQLite 持久化，uv 管理依赖，uv.lock 锁版）
web/       React 19 + TypeScript + Vite 6 + TanStack Query
prototype/ 交互原型（视觉与交互验收基准）
docs/      设计文档与开发任务记录
openspec/  变更管理（OpenSpec）
```

## 快速开始

**前置**：Python ≥ 3.13（推荐用 [uv](https://docs.astral.sh/uv/) 自动管理）、Node ≥ 20

```bash
# 1. 后端（默认 127.0.0.1:9500，首次启动自动生成登录 token）
cd backend
uv run --no-dev uvicorn app.main:app --host 127.0.0.1 --port 9500

# 2. 前端（默认 9501，代理 /api → 9500）
cd web
npm ci
npm run dev
```

打开 http://localhost:9501 ，登录 token 存于 `backend/data/app.db` 的 `setting` 表（`server.token` 键）。

**无后端演示模式**：URL 加 `?mock=1` 可纯前端体验全部界面与 AI 补全流程（内置演示数据，生产构建自动剔除 mock 代码）。

**生产构建**：`cd web && npm run build`，产物 `web/dist/` 可由 FastAPI 内嵌静态托管。

## 文档

| 文件 | 内容 |
|---|---|
| [整体需求解决方案.md](整体需求解决方案.md) | 需求与决策的唯一权威依据（SSOT） |
| [数据源模块设计.md](数据源模块设计.md) | 数据源子模块设计：实体/摄取/AI 补全/指标口径 |
| [prototype/](prototype/) | 四页可交互原型，前端走查基准 |

## 许可

[MIT](LICENSE)
