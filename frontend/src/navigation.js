// 界面导航与偏好的共享常量：侧边栏、用户菜单、设置弹窗、系统管理共用

export const ROLE_LABEL = { admin: "管理员", editor: "编辑", viewer: "只读成员" };

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const SETTINGS_SHORTCUT = isMac ? "⌘," : "Ctrl+,";

// 对话模式（与输入框下方的模式切换一致）
export const MODE_OPTIONS = [
  { id: "assistant", label: "智能助手", hint: "自主规划：查知识库、找数据、写 SQL、做归因" },
  { id: "agent", label: "经营分析", hint: "门店经营指标、目标达成、店员与库存" },
  { id: "kb", label: "知识问答", hint: "只根据知识库回答，并给出引用" },
  { id: "query", label: "SQL 查询", hint: "把问题直接转成 SQL 查询数据" },
];

// 设置分区（整页设置，地址为 #/settings/<id>）。admin: true 的分区只有管理员可见
export const SETTINGS_GROUPS = [
  { id: "personal", label: "个人" },
  { id: "billing", label: "用量与账单" },
  { id: "admin", label: "管理", admin: true },
  { id: "help", label: "帮助" },
];

export const SETTINGS_SECTIONS = [
  { id: "general", group: "personal", label: "一般", icon: "settings", hint: "默认模型与对话模式" },
  { id: "account", group: "personal", label: "账户", icon: "user", hint: "个人资料与登录密码" },
  { id: "privacy", group: "personal", label: "隐私", icon: "shield", hint: "你的数据如何保存，导出或删除" },
  { id: "memory", group: "personal", label: "记忆", icon: "bulb", hint: "助手记住的偏好与背景，只有你自己可见" },
  { id: "usage", group: "billing", label: "使用量", icon: "chart", hint: "本期额度、每日用量与按模型统计" },
  { id: "plan", group: "billing", label: "方案与账单", icon: "coins", hint: "当前方案、余额、可选方案与账单记录" },
  { id: "members", group: "admin", label: "成员与权限", icon: "users", hint: "账号、角色与启用状态", admin: true },
  { id: "billing-admin", group: "admin", label: "方案与计费", icon: "coins", hint: "方案定价、为成员开通方案和充值、全员用量", admin: true },
  { id: "models", group: "admin", label: "模型接入", icon: "sparkles", hint: "接入云端或本地大模型，设置默认模型和单价", admin: true },
  { id: "skills", group: "admin", label: "技能管理", icon: "bulb", hint: "上架到「发现」的技能：分析流程与写作规范", admin: true },
  { id: "tools", group: "admin", label: "工具管理", icon: "code", hint: "上架到「发现」的工具与外部接口", admin: true },
  { id: "audit", group: "admin", label: "审计日志", icon: "history", hint: "登录、编辑、导入、计费等关键操作记录", admin: true },
  { id: "system", group: "admin", label: "系统状态", icon: "activity", hint: "向量模型、大模型与知识库索引", admin: true },
  { id: "about", group: "help", label: "关于与帮助", icon: "info", hint: "版本、数据连接、快捷键" },
];

export const visibleSections = (user) => SETTINGS_SECTIONS.filter((s) => !s.admin || user.role === "admin");

// 旧的系统管理地址 → 设置分区
export const LEGACY_ADMIN_SECTIONS = { models: "models", tools: "tools", skills: "skills", users: "members", audit: "audit", system: "system" };

// 发现：模型 / 技能 / 工具目录
export const DISCOVER_SECTIONS = [
  { id: "all", label: "全部", icon: "sparkles" },
  { id: "models", label: "模型", icon: "database", kind: "model" },
  { id: "skills", label: "技能", icon: "bulb", kind: "skill" },
  { id: "tools", label: "工具", icon: "code", kind: "tool" },
];

export const formatCredits = (value) =>
  Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: Math.abs(value) < 10 ? 2 : 0 });

export const formatYuan = (cents) => `¥${(Number(cents || 0) / 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
