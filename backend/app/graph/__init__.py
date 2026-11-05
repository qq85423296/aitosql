"""L2 编排层（design D-31）：langgraph 唯一 import 点（D3）。

8 节点规格 = SSOT §5.2 逐行实现；条件边 = §5.3；双库写入顺序铁律（D-01）：
终态事件与 status 在 app.db 单事务先提交（节点内 persist_status），checkpoint 后提交
（langgraph superstep 提交天然晚于节点内 app.db 写入）。
"""
