"""AI 补全领域模块（L3，方案 v1.1）。

分层纪律（D1/D2/D6）：仅可 import app.store；LLM chat_fn、采样 query_fn 均由
api 层注入（见 api/ai_runs.py），严禁跨域 import ingest/exec/llm/retrieval。
"""
