"""多 Agent 层：每个 Agent 对外是图中的一个节点，内部可包含多步工具调用。

- decomposition：问题拆解（指代消解 / 子问题拆解 / 分类 / 查询改写）
- retrieval：检索召回 + 充分性判断（内部多轮补查）
- verification：引用溯源 + 逐条引用与召回片段的蕴含校验
"""
