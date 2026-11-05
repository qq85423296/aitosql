"""L3 执行域（design D-36 / BR-7）。

唯一引擎触点（PyMySQL / clickhouse-connect，惰性 import）；全部同步调用经
anyio.to_thread 隔离出事件循环（D-02）；工作/管理连接分离（评审项 18）。
"""
