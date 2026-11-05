"""L3 检索供给域（design D-33 / BR-2）。

FTS5 bigram 关键词路 + sqlite-vec 向量路 → RRF k=60 融合 → 预算装配；
滞后带 55-65% 防模式抖动；need_tables 二次供给仅 1 次；检索失败降级 full 截断。
边界：SQL 拼装允许位之二（D4）；不触 LLM。
"""
