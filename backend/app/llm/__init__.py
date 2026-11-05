"""L3 模型与提示词域（design D-34）。

出境行为的唯一发生地（C11）：一切离开本机的文本只经 providers.py 适配器发出；
脱敏输入由 sqlcheck/clean.py 显式传入（跨层显式参数，D2）。
"""
