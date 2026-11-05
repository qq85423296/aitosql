"""L3 SQL 校验域（design D-35 / BR-5）。

sqlglot 唯一 import 点（D3；含 ingest 离线 DDL 解析的收口模块 ddl.py——
构建期分层裁决：offline.py 不 import sqlglot，解析产物经 api 层显式传递）。
validate 执行序铁律（D-03）：Unicode 清洗 → auto_limit AST 注入 → R15 优先 →
R01-R14 → 标识符归一 → EXPLAIN 干跑（AST 去 LIMIT 副本，干跑串永不进 execute）。
"""
