-- migrations/0004_memory.sql —— SSOT v3.2 §3.4 原文（记忆模块，v3.1）
-- Layer 1 流水账：追加只写、从不修改，原子事实
CREATE TABLE memory_log(
  id INTEGER PRIMARY KEY,
  datasource_id INTEGER,                 -- NULL=全局
  kind TEXT NOT NULL CHECK(kind IN('caliber','correction','quirk','preference')),
  fact TEXT NOT NULL,                    -- 如 "收入口径=含税（用户澄清门裁决）"
  source_query_id TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
-- Layer 2 总账：LLM 合并后的精华条目，每次生成注入
CREATE TABLE memory(
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'global' CHECK(scope IN('global','datasource')),
  datasource_id INTEGER,
  topic TEXT NOT NULL,                   -- 主题分组（口径偏好/表怪癖/查询习惯…）
  content TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,             -- 停用则不注入（人工管理入口）
  token_est INTEGER DEFAULT 0,
  updated_at TEXT
);
-- 合并水位线：只处理增量，省 token
CREATE TABLE memory_watermark(
  id INTEGER PRIMARY KEY CHECK(id=1),
  last_log_id INTEGER NOT NULL DEFAULT 0,
  last_consolidate_at TEXT
);
-- 会话滚动摘要（长会话压缩）
ALTER TABLE session ADD COLUMN summary TEXT;          -- 滚动摘要（有损链）
ALTER TABLE session ADD COLUMN summary_upto TEXT;     -- 摘要覆盖到的最后一个 query_id
