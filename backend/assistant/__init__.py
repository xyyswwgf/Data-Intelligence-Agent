"""
智能助手：推理 Agent + 记忆 + 上下文管理

  memory.py   长期记忆（用户偏好/事实/目标/指令），向量召回 + 去重合并
  sessions.py 对话会话与消息持久化，滚动摘要压缩
  context.py  按 token 预算组装上下文
  tools.py    Agent 可调用的工具（知识库、SQL、经营分析、CSV 分析、记忆）
  agent.py    ReAct 推理循环（计划 → 调用工具 → 观察 → 反思 → 回答），事件流输出
"""
