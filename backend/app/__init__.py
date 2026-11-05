"""AItosql 个人版 Text2SQL 后端包。

分层守则（SSOT §2.3 D1-D6，import-linter CI 强制）：
    app/api  →  app/graph  →  七领域模块（互禁导入）  →  app/store
三方 import 收口：langgraph→graph、sqlglot→sqlcheck、fastembed→retrieval/vector。
SQL 文本拼装仅限 store/ 与 retrieval/ 两处。
"""

__version__ = "0.1.0"
