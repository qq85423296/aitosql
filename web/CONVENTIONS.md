# CONVENTIONS.md —— 前端守则（SSOT §11.2 V1-V4 落地条文 + 分层纪律）

> 本文件是前端工程的唯一守则来源（T1.9b/c 验收条文）。等价于项目索引导航：组件库判定、API 约定、场景入口均在此声明（`openspec/.project-frontend/` 在写域外，本次不创建，见 plan.md §1 声明）。

## V1 · ResultTable 虚拟化
`ResultTable.tsx`：结果行 **>100** 启用 `@tanstack/react-virtual`，行高 **32px**、overscan **10**。阈值变更须同步本文件与验收。

## V2 · Stream 虚拟滚动
`Stream.tsx`：消息 **>200** 条启用虚拟滚动 + `measureElement`（ResizeObserver 动态测高）+ **底部锚定**（新消息/状态变化滚到底）。

## V3 · 令牌纪律
- `src/theme/tokens.css` 是**唯一 token 来源**（值与原型 `prototype.css :root` 逐字一致；深色 39 令牌 + 浅色 37 覆盖）。
- 闸口：`npm run verify:tokens`（V-2 脚本，不一致即非零退出）。
- Tailwind v4 `@theme inline` 桥接（工具类 `bg-bg-0`/`text-t1`/`border-border`/`font-mono`…运行时仍指向 `var(--*)`，主题切换即时生效）。
- stylelint `color-no-hex` 门禁：除 `tokens.css` 外任何文件禁裸 hex（`stylelint.config.mjs`）。
- 双主题：`<html data-theme>` 切换，`localStorage('aitosql.theme')` 记忆，**深色默认**。

## V4 · 状态分层
- **REST 数据一律归 TanStack Query**（`api/*.ts` 13 组端点模块；`queryKey` 以资源名命名）。
- **zustand 只存 SSE 装配态 + UI 态**：`queryStore`（MessageModel 装配/appliedSeq/横幅/门 optimistic）、`sessionStore`（抽屉/过滤/面板/Composer）、`settingsStore`（主题）。
- 终态（done/failed/stopped）invalidate 三键：`sessions` / `queries` / `samples`（useAssembly）。
- 门裁决 optimistic 回显后走 `POST /queries/:id/resume`（CAS；409 提示刷新）。

## 装配五步（D-21 / §11.2，评审项 32 终稿）
1. E5 快照 hydrate：`GET /sessions/:id/queries` 渲染历史（`useAssembly`）；
2. running/waiting_* 查询挂 SSE（`attachStream`）；
3. 双模重连归一：已知 seq → `Last-Event-ID` 增量；否则无头全量自 seq=1；
4. seq 幂等去重（`queryStore.applyEvent`，≤lastSeq 丢弃）；**乱序 fail-loud** → REST resync（重拉 E5）；
5. 横幅策略：`stream_resumed` 控制帧（无 seq 不入 appliedSeq）仅增量场景亮琥珀「已从断点续播」；崩溃恢复亮绿「已自动恢复」，**无手动续跑按钮**（D03）。

## API 约定（contracts C0-C10 唯一耦合面 = /api + SSE）
- `/api` 前缀 + Bearer `server.token`（`localStorage('aitosql.token')`，设置页可重生成）。
- envelope `{code, message, data}`：`code=0` 成功；`code` 为 `E####` 抛 `ApiError`（C8 七码表现见 `lib/errorText.ts`）。
- 业务 ID：`s_`/`q_` + ULID；`client_request_id` 前端生成（D-22，`lib/ulid.ts`）。
- SSE：自研 `fetch + ReadableStream` reader（`api/sse.ts`；EventSource 不支持自定义头/POST，弃用）；`:hb` 心跳过滤；单事件 ≤16KB 截断。
- mock 开关：`VITE_API_MOCK=1`（默认）→ `api/mock.ts`（原型 data.json 形状）；联调期置 0 并配 `server.proxy → 127.0.0.1:8321`。

## 组件库判定（等价索引声明）
- shadcn/ui 配置就位（`components.json`，new-york 风格，cssVariables）；当前 `components/ui/` 为**自实现无依赖基件**（Button/Drawer/Switch/Tabs/Badge/Toast），对齐原型类名与 aria 角色（§4.1）；后续可按 components.json 增量 `npx shadcn add` 替换而不改业务调用面。
- SqlBlock：只读态 = 原型同款正则高亮（四色 token：`--sql-k/f/s/n` + 行号）；编辑态 = CodeMirror 6（`@codemirror/lang-sql`）重跑。
- RelGraph：原生 SVG 自实现（缩放/拖拽/信息卡/三态边/摆位保存 `graph_layout`），不引图库（D-18）。

## 场景入口（等价 scenes）
| 场景 | 入口 |
|---|---|
| 主链路提问/复核 | `pages/QueryPage.tsx` + `components/query/**` |
| 数据源注册/治理 | `pages/DatasourcesPage.tsx` + `components/ds/**` |
| 样例库治理 | `pages/SamplesPage.tsx` + `components/samples/SampleDrawer.tsx` |
| 设置十组/出境联动 | `pages/SettingsPage.tsx` + `components/settings/**` |

## 文案纪律（验收逐字一致）
「已取消，语句未执行」「密钥未外发」「文件保留 24 小时」「模型 N: 模型 ID 不能为空」「不适用自动续跑」「未验证（已照常交付）」；E3010 五断言模板见 `lib/errorText.ts`（SSOT §12.7 原文）；复核包模板见 `lib/reviewPack.ts`（C9.1 逐字）。
