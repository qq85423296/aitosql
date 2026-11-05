-- Monotonic revisions avoid timestamp collisions and keep derived indexes disposable.
CREATE TABLE schema_revision(
 datasource_id INTEGER PRIMARY KEY REFERENCES datasource(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL DEFAULT 0,
 fts_revision INTEGER NOT NULL DEFAULT -1,
 vector_revision INTEGER NOT NULL DEFAULT -1,
 vector_model TEXT,
 vector_dim INTEGER
);
INSERT INTO schema_revision(datasource_id) SELECT id FROM datasource;
CREATE TRIGGER rag_datasource_insert AFTER INSERT ON datasource BEGIN
 INSERT INTO schema_revision(datasource_id) VALUES(NEW.id);
END;
CREATE TRIGGER rag_table_insert AFTER INSERT ON table_meta BEGIN
 UPDATE schema_revision SET revision=revision+1 WHERE datasource_id=NEW.datasource_id;
END;
CREATE TRIGGER rag_table_update AFTER UPDATE ON table_meta BEGIN
 UPDATE schema_revision SET revision=revision+1 WHERE datasource_id IN(OLD.datasource_id,NEW.datasource_id);
END;
CREATE TRIGGER rag_table_delete AFTER DELETE ON table_meta BEGIN
 UPDATE schema_revision SET revision=revision+1 WHERE datasource_id=OLD.datasource_id;
END;
CREATE TRIGGER rag_column_insert AFTER INSERT ON column_meta BEGIN
 UPDATE schema_revision SET revision=revision+1 WHERE datasource_id=(SELECT datasource_id FROM table_meta WHERE id=NEW.table_id);
END;
CREATE TRIGGER rag_column_update AFTER UPDATE ON column_meta BEGIN
 UPDATE schema_revision SET revision=revision+1 WHERE datasource_id IN(SELECT datasource_id FROM table_meta WHERE id IN(OLD.table_id,NEW.table_id));
END;
CREATE TRIGGER rag_column_delete AFTER DELETE ON column_meta BEGIN
 UPDATE schema_revision SET revision=revision+1 WHERE datasource_id=(SELECT datasource_id FROM table_meta WHERE id=OLD.table_id);
END;
CREATE TRIGGER rag_relation_insert AFTER INSERT ON relation BEGIN
 UPDATE schema_revision SET revision=revision+1 WHERE datasource_id=NEW.datasource_id;
END;
CREATE TRIGGER rag_relation_update AFTER UPDATE ON relation BEGIN
 UPDATE schema_revision SET revision=revision+1 WHERE datasource_id IN(OLD.datasource_id,NEW.datasource_id);
END;
CREATE TRIGGER rag_relation_delete AFTER DELETE ON relation BEGIN
 UPDATE schema_revision SET revision=revision+1 WHERE datasource_id=OLD.datasource_id;
END;
CREATE TRIGGER rag_standard_insert AFTER INSERT ON field_standard BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_standard_update AFTER UPDATE ON field_standard BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_standard_delete AFTER DELETE ON field_standard BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_dimension_insert AFTER INSERT ON dimension_value BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_dimension_update AFTER UPDATE ON dimension_value BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_dimension_delete AFTER DELETE ON dimension_value BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_dimension_standard_insert AFTER INSERT ON dimension_standard BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_dimension_standard_update AFTER UPDATE ON dimension_standard BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_dimension_standard_delete AFTER DELETE ON dimension_standard BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_metric_insert AFTER INSERT ON metric_standard BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_metric_update AFTER UPDATE ON metric_standard BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
CREATE TRIGGER rag_metric_delete AFTER DELETE ON metric_standard BEGIN
 UPDATE schema_revision SET revision=revision+1;
END;
