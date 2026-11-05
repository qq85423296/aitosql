-- Generation feedback loop: executed-query table usage for retrieval reranking.
-- One row per (datasource, table); success_count is bumped only after the query
-- reaches a verified-success state (rows returned / write succeeded).
CREATE TABLE retrieval_feedback(
  datasource_id INTEGER NOT NULL,
  table_name TEXT NOT NULL,
  success_count INTEGER NOT NULL DEFAULT 0,
  last_success_at TEXT,
  PRIMARY KEY(datasource_id, table_name)
);
