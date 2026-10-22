import Icon from "./Icon";

/**
 * 左侧导航：新对话、数据表、最近分析、连接状态
 */
export default function Sidebar({
  open,
  onClose,
  onNewChat,
  onOpenData,
  tables,
  targetTable,
  onSelectTable,
  history,
  activeRunId,
  onSelectHistory,
  sourceInfo,
}) {
  const imported = (tables || []).filter((t) => t.imported);
  const online = Boolean(sourceInfo);

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

        <button type="button" className="new-chat" onClick={onNewChat}>
          <Icon name="plus" size={16} /> 新对话
        </button>

        <nav className="side-section">
          <div className="side-label">数据空间</div>
          <button
            type="button"
            className={`side-item ${!targetTable ? "active" : ""}`}
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
              className={`side-item ${targetTable === t.table_name ? "active" : ""}`}
              onClick={() => onSelectTable(t.table_name)}
              title={t.table_name}
            >
              <Icon name="table" size={16} />
              <span className="side-text">{t.table_name.replace(/^csv_/, "")}</span>
              <span className="side-badge">{t.row_count}</span>
            </button>
          ))}
          <button type="button" className="side-item muted" onClick={onOpenData}>
            <Icon name="upload" size={16} />
            <span className="side-text">导入 CSV</span>
          </button>
        </nav>

        <nav className="side-section grow">
          <div className="side-label">最近分析</div>
          {history.length === 0 && <p className="side-empty">还没有分析记录</p>}
          {history.map((item) => (
            <button
              type="button"
              key={item.run_id}
              className={`side-item history ${item.run_id === activeRunId ? "active" : ""}`}
              onClick={() => onSelectHistory(item.run_id)}
              title={item.summary}
            >
              <Icon name="history" size={15} />
              <span className="side-text">{item.question}</span>
            </button>
          ))}
        </nav>

        <button type="button" className="status-card" onClick={onOpenData}>
          <span className={`status-dot ${online ? "on" : "off"}`} />
          <div>
            <strong>{online ? sourceInfo.database.engine : "后端未连接"}</strong>
            <small>{online ? `${sourceInfo.database.name} · ${sourceInfo.tables.length} 张表` : "请启动后端服务"}</small>
          </div>
        </button>
      </aside>
    </>
  );
}
