-- migrations/0012_dsh_alignment.sql —— DSH 对齐改造（P5/P6）
-- datasource_instruction：每数据源口径说明文档（借鉴 dsh-agent-instructions，
--   口径/术语/join 惯例的显式载体，注入信封「数据源口径」段，比 memory 假设碎片可控）。
-- query_inbox：运行中输入双队列（借鉴 dsh-agent-loop inbox）：
--   kind=steer   运行中引导（下一边界并入当轮）
--   kind=followup 排队下轮（当前查询终态后自动开新轮）
--   kind=inject  注入补充上下文（不改写问题，下一边界生效）
-- 状态机：pending → claimed（被图领取）/ discarded（持久取消）。

CREATE TABLE datasource_instruction (
    datasource_id INTEGER PRIMARY KEY REFERENCES datasource(id) ON DELETE CASCADE,
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE query_inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id TEXT NOT NULL REFERENCES query(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('steer', 'followup', 'inject')),
    content TEXT NOT NULL,
    datasource_id INTEGER,          -- followup 排队时记录目标数据源（发起时可已切换）
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'claimed', 'discarded')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    claimed_at TEXT
);
CREATE INDEX idx_inbox_query_pending ON query_inbox(query_id, status, id);
