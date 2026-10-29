import Icon from "./Icon";
import UserMenu from "./UserMenu";

const MODULES = [
  { id: "chat", label: "智能分析", icon: "sparkles", path: "/" },
  { id: "wiki", label: "知识库", icon: "book", path: "/wiki" },
  { id: "discover", label: "发现", icon: "compass", path: "/discover" },
];

/** 智能分析模块的导航：新对话、数据空间、对话历史 */
function ChatNav({ onNewChat, onOpenData, tables, targetTable, onSelectTable, sessions, activeSessionId,
  onSelectSession, onDeleteSession, canImport, active }) {
  const imported = (tables || []).filter((t) => t.imported);
  return (
    <>
      <button type="button" className="new-chat" onClick={onNewChat}>
        <Icon name="plus" size={16} /> 新对话
      </button>

      <nav className="side-section">
        <div className="side-label">数据空间</div>
        <button
          type="button"
          className={`side-item ${active && !targetTable ? "active" : ""}`}
          onClick={() => onSelectTable(null)}
        >
          <Icon name="database" size={16} />
          <span className="side-text">经营数据仓库</span>
          <span className="side-badge">内置</span>
        </button>
        {imported.map((t) => (
          <button
            type="button"
            key={t.table_name}
            className={`side-item ${active && targetTable === t.table_name ? "active" : ""}`}
            onClick={() => onSelectTable(t.table_name)}
            title={t.table_name}
          >
            <Icon name="table" size={16} />
            <span className="side-text">{t.table_name.replace(/^csv_/, "")}</span>
            <span className="side-badge">{t.row_count}</span>
          </button>
        ))}
        {canImport && (
          <button type="button" className="side-item muted" onClick={onOpenData}>
            <Icon name="upload" size={16} />
            <span className="side-text">导入 CSV / Excel 表</span>
          </button>
        )}
      </nav>

      <nav className="side-section grow">
        <div className="side-label">对话</div>
        {sessions.length === 0 && <p className="side-empty">还没有对话</p>}
        <div className="side-scroll">
          {sessions.map((item) => (
            <div key={item.id} className={`side-item history session ${item.id === activeSessionId ? "active" : ""}`}>
              <button type="button" className="session-open" onClick={() => onSelectSession(item.id)} title={item.title}>
                <Icon name="history" size={15} />
                <span className="side-text">{item.title}</span>
              </button>
              <button
                type="button"
                className="session-delete"
                onClick={() => onDeleteSession(item.id)}
                aria-label="删除对话"
                title="删除对话"
              >
                <Icon name="trash" size={13} />
              </button>
            </div>
          ))}
        </div>
      </nav>
    </>
  );
}

/**
 * 左侧导航
 *   顶部：品牌 + 模块切换（智能分析 / 知识库 / 发现）；设置页换成「返回应用」+ 设置分区
 *   中部：当前模块自己的导航。知识库、发现、设置的导航由各自页面通过 slot 渲染（React portal），状态留在页面内
 *   底部：用户菜单（方案与额度、设置、使用量、退出）
 */
export default function Sidebar({ open, onClose, module, onNavigate, onSlot, chat, user, online, account, onOpenSettings,
  onBack, onLogout }) {
  return (
    <>
      <div className={`sidebar-scrim ${open ? "show" : ""}`} onClick={onClose} />
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand">
          <span className="orb orb-sm" />
          <div>
            <strong>DataMind</strong>
            <small>数据智能 Agent</small>
          </div>
        </div>

        {module === "settings" ? (
          <div className="module-back">
            <button type="button" className="side-item" onClick={onBack}>
              <Icon name="back" size={16} /> <span className="side-text">返回应用</span>
            </button>
            <div className="module-title"><Icon name="settings" size={16} /> 设置</div>
          </div>
        ) : (
          <div className="module-switch" role="tablist" aria-label="切换模块">
            {MODULES.map((m) => (
              <button
                type="button"
                key={m.id}
                role="tab"
                aria-selected={module === m.id}
                className={module === m.id ? "active" : ""}
                onClick={() => onNavigate(m.path)}
              >
                <Icon name={m.icon} size={15} /> {m.label}
              </button>
            ))}
          </div>
        )}

        <div className="module-nav">
          {module === "chat" ? <ChatNav {...chat} canImport={user.role !== "viewer"} active /> : <div className="module-slot" ref={onSlot} />}
        </div>

        <UserMenu user={user} online={online} account={account} onOpenSettings={onOpenSettings} onLogout={onLogout} />
      </aside>
    </>
  );
}
