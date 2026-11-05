-- Durable write fence. A started attempt is never automatically replayed.
CREATE TABLE execution_attempt(
  query_id TEXT NOT NULL REFERENCES query(id) ON DELETE CASCADE,
  sql_hash TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('prepared','started','succeeded','outcome_unknown')),
  approved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(query_id, sql_hash)
);
