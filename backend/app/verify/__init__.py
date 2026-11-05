"""L3 结果校验域（design D-37 / BR-8）。

规则层 5 检查（SSOT §9.2，全过才 pass）+ 全局共享 1 次回退额度（D09/D-12）+
可选 LLM 层（feature.verify_llm 默认关，local_only 置灰，E6 红标出境类目）。
边界：不修正 SQL（回退理由进 error_history 交 correct 节点）。
"""
