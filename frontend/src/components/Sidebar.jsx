import { useState } from "react";
import Icon from "./Icon";

const ROLE_LABEL = { admin: "管理员", editor: "编辑", viewer: "只读成员" };

/**
 * 左侧导航：主导航、新对话、数据空间、最近分析、当前用户
 */
export default function Sidebar({
  open,
  onClose,
  view,
  onNavigate,
  onNewChat,
  onOpenData,
  tables,
  targetTable,
  onSelectTable,
  sessions,
  activeSessionId,
  onSelectSession,
  onDeleteSession,
  sourceInfo,
  user,
  onLogout,
  onChangePassword,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const imported = (tables || []).filter((t) => t.imported);
  const online = Boolean(sourceInfo);
  const canImport = user.role !== "viewer";

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

        <nav className="main-nav">
          <button type="button" className={view === "chat" ? "active" : ""} onClick={() => onNavigate("/")}>
            <Icon name="sparkles" size={16} /> 智能分析
          </button>
          <button type="button" className={view === "wiki" ? "active" : ""} onClick={() => onNavigate("/wiki")}>
            <Icon name="book" size={16} /> 知识库
          </button>
          {user.role === "admin" && (
            <button type="button" className={view === "admin" ? "active" : ""} onClick={() => onNavigate("/admin")}>
              <Icon name="shield" size={16} /> 管理
            </button>
          )}
        </nav>

        <button type="button" className="new-chat" onClick={onNewChat}>
          <Icon name="plus" size={16} /> 新对话
        </button>

        <nav className="side-section">
          <div className="side-label">数据空间</div>
          <button
            type="button"
            className={`side-item ${view === "chat" && !targetTable ? "active" : ""}`}
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
              className={`side-item ${view === "chat" && targetTable === t.table_name ? "active" : ""}`}
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
              <span className="side-text">导入 CSV</span>
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

        <div className="user-card-wrap">
          {menuOpen && (
            <div className="user-menu" onMouseLeave={() => setMenuOpen(false)}>
              <div className="user-menu-status">
                <span className={`status-dot ${online ? "on" : "off"}`} />
                {online ? `${sourceInfo.database.engine} · ${sourceInfo.database.name}` : "数据库未连接"}
              </div>
              <button type="button" onClick={() => { setMenuOpen(false); onNavigate("/memory"); }}>
                <Icon name="bulb" size={15} /> 我的记忆
              </button>
              <button type="button" onClick={() => { setMenuOpen(false); onChangePassword(); }}>
                <Icon name="key" size={15} /> 修改密码
              </button>
              <button type="button" onClick={() => { setMenuOpen(false); onLogout(); }}>
                <Icon name="logout" size={15} /> 退出登录
              </button>
            </div>
          )}
          <button type="button" className="status-card" onClick={() => setMenuOpen((v) => !v)}>
            <span className="avatar">{(user.display_name || user.username).slice(0, 1).toUpperCase()}</span>
            <div>
              <strong>{user.display_name}</strong>
              <small>
                <span className={`status-dot inline ${online ? "on" : "off"}`} /> {ROLE_LABEL[user.role]} · @{user.username}
              </small>
            </div>
          </button>
        </div>
      </aside>
    </>
  );
}
