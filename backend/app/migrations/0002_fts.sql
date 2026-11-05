-- migrations/0002_fts.sql —— SSOT v3.2 §3.2 原文
CREATE VIRTUAL TABLE schema_fts USING fts5(
  datasource_id UNINDEXED, table_name, col_name, comment,
  tokenize='unicode61'                    -- 中文由应用层 bigram 展开后写入（仅消费消毒后文本）
);
-- schema_vec 运行期按 setting schema.embed_dim（默认 512）建表，不写死 migration；
-- 换模型重建流程：改 setting → 重建 vec 表（期间 schema.vector 临时 off，降级纯 FTS）→ 完成自动恢复。FTS 表不动。
