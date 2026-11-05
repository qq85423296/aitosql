-- 0007 AI 补全任务模型（ai_run）+ 建议关联 run_id（方案 v1.1 §3/§3.1）
-- ai_run：一次补全任务一行；ai_suggestion.run_id 关联产出批次（增量 ALTER，向后兼容）。
CREATE TABLE ai_run(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  datasource_id INTEGER NOT NULL REFERENCES datasource(id) ON DELETE CASCADE,
  scope_type TEXT NOT NULL CHECK(scope_type IN('datasource','table','relations')),
  scope_table_id INTEGER NULL,            -- scope=table 时的表 id
  kinds_json TEXT NOT NULL,               -- ["comment","relation","std_bind"]
  options_json TEXT NOT NULL DEFAULT '{}',-- {rescan_existing, use_sample_values}
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK(status IN('queued','running','done','failed','cancelled')),
  progress_done INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER NOT NULL DEFAULT 0,
  made_json TEXT NOT NULL DEFAULT '{}',   -- {"comment":12,"relation":3}
  failed_json TEXT NOT NULL DEFAULT '[]', -- [{table,kind,error}]
  error TEXT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  finished_at TEXT NULL
);
CREATE INDEX idx_ai_run_ds ON ai_run(datasource_id, status);

ALTER TABLE ai_suggestion ADD COLUMN run_id INTEGER NULL;  -- 产出批次关联
