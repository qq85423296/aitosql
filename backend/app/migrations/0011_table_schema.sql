-- 0011: table_meta 增模式名（schema_name）。
-- 来源：CSV 字典模板「表模式名」落库（此前校验必填但不落库）；在线摄取 = 库名。
-- 用途：问数提示词表标题限定 schema.table，生成 SQL 使用正确模式名前缀。
ALTER TABLE table_meta ADD COLUMN schema_name TEXT;
