-- migrations/0005_graph_layout.sql —— 本次设计新增（design D-18，关闭 TODO-N1）
-- ER 图谱手工摆位记忆；读取缺失时前端按表名字典序确定性平铺；随 app.db 入备份。
-- 注：updated_at 用 CURRENT_TIMESTAMP（sqlite 3.53 对 '{}' 默认 + (datetime('now)')) 组合
--     存在解析歧义，探针实测规避；语义等价 UTC now）。
CREATE TABLE graph_layout(
  datasource_id INTEGER PRIMARY KEY REFERENCES datasource(id),
  node_positions_json TEXT NOT NULL DEFAULT '{}',  -- 形如 {table_name: {x, y}}
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
