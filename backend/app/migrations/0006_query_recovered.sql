-- migrations/0006_query_recovered.sql —— 修复回路新增（rd-ca R-3 / F5-D03 闭环）
-- query.recovered：D03 启动清扫「自动续跑」标记；E5 快照项透出，前端据此亮
-- 「已自动恢复」绿横幅（无手动续跑按钮）。崩溃恢复失败（checkpoint 缺失）路径不置位——
-- 该路径有独立 E5001 error 事件标注。
ALTER TABLE query ADD COLUMN recovered INTEGER NOT NULL DEFAULT 0;
