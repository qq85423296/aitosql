-- migrations/0003_events.sql —— SSOT v3.2 §3.3 原文（事件溯源，评审项 6）
CREATE TABLE query_event(
  query_id TEXT NOT NULL REFERENCES query(id) ON DELETE CASCADE,  -- 级联自闭环，不设 retention（D12 关闭）
  seq INTEGER NOT NULL,                    -- 单调递增，续播接缝
  type TEXT NOT NULL,                      -- §10.3 事件类型（控制帧 stream_resumed 不入表）
  payload TEXT NOT NULL,                   -- 错误类落库前过脱敏（§12.5 第三出口）；result 批量行不入此表
  PRIMARY KEY(query_id, seq)
);

-- 备份归属注（评审项 9）：query_event 在 app.db 内随备份（密级标注「SQL 字面量与事件溯源明文」）；
-- checkpoints.db 不入备份（最大明文聚合体 + 恢复语义绑本地进程）。
