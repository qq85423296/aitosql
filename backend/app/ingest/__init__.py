"""L3 摄取域（design D-32）。

三方言在线摄取 + 离线三通道（DDL/CSV/AI 草稿）；消毒唯一入口（sanitize.py，
先于 bigram 与 embedding，§8.4）；写库经 store 层；不读 QueryState。
"""
