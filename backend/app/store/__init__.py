"""L4 持久化层（design D-38/D-39）。

边界：SQL 文本拼装允许位之一（分层 D4）；不 import 任何 L3 领域模块（D6）。
双库铁律（D-01）：app.db 终态权威（入备份）/ checkpoints.db 进度权威（不入备份）。
"""
