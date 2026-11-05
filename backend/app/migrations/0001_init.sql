-- migrations/0001_init.sql —— SSOT v3.2 §3.1 原文（冻结 DDL，唯一权威）
PRAGMA journal_mode=WAL;

CREATE TABLE datasource(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  dialect TEXT NOT NULL CHECK(dialect IN('mysql','doris','clickhouse')),
  mode TEXT NOT NULL CHECK(mode IN('online','offline')),
  host TEXT, port INTEGER, db_name TEXT, username TEXT,
  password_enc BLOB,                     -- Fernet；离线库为 NULL
  params_json TEXT DEFAULT '{}',
  version TEXT,
  capabilities_json TEXT DEFAULT '{}',
  last_ingest_at TEXT, created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE table_meta(
  id INTEGER PRIMARY KEY,
  datasource_id INTEGER NOT NULL REFERENCES datasource(id),
  name TEXT NOT NULL,
  comment_db TEXT, comment_custom TEXT,   -- 人工注释优先，重摄取不覆盖
  row_estimate INTEGER, field_count INTEGER,
  partition_type TEXT,                    -- month | day | NULL
  updated_at TEXT,
  UNIQUE(datasource_id, name)
);

CREATE TABLE column_meta(
  id INTEGER PRIMARY KEY,
  table_id INTEGER NOT NULL REFERENCES table_meta(id),
  name TEXT NOT NULL, type TEXT NOT NULL,
  comment_db TEXT, comment_custom TEXT,
  is_pk INTEGER DEFAULT 0,
  is_partition INTEGER DEFAULT 0,
  is_bucket INTEGER DEFAULT 0,
  field_standard_id INTEGER REFERENCES field_standard(id),
  updated_at TEXT,
  UNIQUE(table_id, name)
);
CREATE INDEX idx_column_table ON column_meta(table_id);

CREATE TABLE relation(
  id INTEGER PRIMARY KEY,
  datasource_id INTEGER NOT NULL REFERENCES datasource(id),
  from_table TEXT NOT NULL, from_col TEXT NOT NULL,
  to_table TEXT NOT NULL, to_col TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN('fk','inferred','manual')),
  confidence REAL DEFAULT 1.0,
  status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN('confirmed','pending')),
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE field_standard(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  data_type TEXT, unit TEXT, value_desc TEXT,
  dimension_standard_id INTEGER REFERENCES dimension_standard(id),
  description TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE dimension_standard(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  description TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE dimension_value(
  id INTEGER PRIMARY KEY, dimension_id INTEGER NOT NULL REFERENCES dimension_standard(id),
  value TEXT NOT NULL, label TEXT NOT NULL, sort INTEGER DEFAULT 0
);
CREATE TABLE metric_standard(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  expr TEXT NOT NULL, filter TEXT, base_tables TEXT,
  description TEXT, created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE ai_suggestion(
  id INTEGER PRIMARY KEY,
  datasource_id INTEGER NOT NULL REFERENCES datasource(id),
  kind TEXT NOT NULL CHECK(kind IN('comment','std_bind','relation','metric')),
  payload_json TEXT NOT NULL,             -- 结构见 §12.2
  confidence REAL, status TEXT DEFAULT 'pending' CHECK(status IN('pending','accepted','ignored')),
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE session(
  id TEXT PRIMARY KEY,                    -- s_ + ulid
  title TEXT NOT NULL, datasource_id INTEGER REFERENCES datasource(id),
  pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')), updated_at TEXT
);
CREATE INDEX idx_session_list ON session(archived, pinned DESC, updated_at DESC);

CREATE TABLE query(
  id TEXT PRIMARY KEY,                    -- q_ + ulid
  session_id TEXT REFERENCES session(id),
  datasource_id INTEGER, dialect TEXT,
  question TEXT NOT NULL,
  contract_json TEXT,
  sql_final TEXT,
  status TEXT NOT NULL DEFAULT 'running'
    CHECK(status IN('running','waiting_clarify','waiting_confirm','done','failed','stopped')),
  rounds INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0,
  scan_rows INTEGER, data_max_time TEXT, duration_ms INTEGER,
  result_unverified INTEGER DEFAULT 0,
  clarify_choice TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE result_preview(
  query_id TEXT PRIMARY KEY REFERENCES query(id),
  columns_json TEXT NOT NULL, rows_json TEXT NOT NULL,  -- 截断 50KB
  truncated INTEGER DEFAULT 0
);
CREATE TABLE clarify_pref(
  id INTEGER PRIMARY KEY,
  datasource_id INTEGER NOT NULL,
  term TEXT NOT NULL, chosen_rule TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(datasource_id, term)
);

CREATE TABLE sample(
  id INTEGER PRIMARY KEY,
  datasource_id INTEGER NOT NULL REFERENCES datasource(id),
  question TEXT NOT NULL, question_norm TEXT NOT NULL,
  sql TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'on' CHECK(status IN('on','stale','off')),
  source TEXT DEFAULT 'manual',
  semantic_rev TEXT,                      -- 语义版本指纹（§12.1）
  validated_at TEXT, stale_reason TEXT, hit_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_sample_norm ON sample(datasource_id, question_norm);

CREATE TABLE provider(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  type TEXT NOT NULL,
  protocol TEXT NOT NULL DEFAULT 'openai_chat',
  base_url TEXT, api_key_enc BLOB,
  models_json TEXT DEFAULT '[]',
  enabled INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE setting(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
