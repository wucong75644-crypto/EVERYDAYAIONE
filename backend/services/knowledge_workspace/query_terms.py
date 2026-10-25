"""Shared Chinese lexical recall for program and model-driven retrieval."""
import re

STOP=set('请 查询 查阅 知识库 当前 资料 文件 文档 对应 怎么 如何 什么 多少 是否 能否 可以 不能 需要 要求 期限 规则 流程 处理 告诉 我 我们 客户 用户 问题 这个 那个 还有 以及 给出 来源 链接 请问 要先 先 能 按 此 中 的 和 与 是 了'.split())

def keywords(question):
    import jieba
    return list(dict.fromkeys(t.lower() for t in jieba.cut_for_search(question)
                             if len(t.strip())>=2 and t not in STOP and re.search(r'[\w\u4e00-\u9fff]',t)))

