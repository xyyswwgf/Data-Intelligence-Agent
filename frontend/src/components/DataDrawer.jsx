import { useState } from "react";
import Icon from "./Icon";
import CsvUploader from "./CsvUploader";

function SourceInfo({ info }) {
  if (!info) return <p className="side-empty">后端未连接，无法读取数据源信息。</p>;
  const { database, tables, pipeline } = info;
  return (
    <div className="source-info">
      <div className="panel">
        <h4 className="panel-title">
          <Icon name="database" size={15} /> 数据库
        </h4>
        <p className="source-line">
          <strong>{database.engine}</strong>
          <span>
            {database.host} / {database.name} · {database.charset}
          </span>
        </p>
      </div>
      <div className="panel">
        <h4 className="panel-title">
          <Icon name="table" size={15} /> 数据表 <span className="count">{tables.length}</span>
        </h4>
        <ul className="source-tables">
          {tables.map((t) => (
            <li key={t.table_name}>
              <code>{t.table_name}</code>
              <span>
                {t.row_count.toLocaleString("zh-CN")} 行{t.imported && <em>导入</em>}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="panel">
        <h4 className="panel-title">
          <Icon name="sparkles" size={15} /> 处理链路
        </h4>
        <ol className="pipeline">
          {pipeline.map((s) => (
            <li key={s.step}>
              <strong>{s.actor}</strong>
              <span>{s.action}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

/** 右侧抽屉：导入 CSV / 数据源信息 */
export default function DataDrawer({ open, onClose, info, onImportComplete, onQueryTable, onAnalyzeTable }) {
  const [tab, setTab] = useState("import");
  return (
    <>
      <div className={`drawer-scrim ${open ? "show" : ""}`} onClick={onClose} />
      <aside className={`drawer ${open ? "open" : ""}`} aria-hidden={!open}>
        <header className="drawer-head">
          <div className="segmented">
            <button type="button" className={tab === "import" ? "active" : ""} onClick={() => setTab("import")}>
              <Icon name="upload" size={14} /> 导入数据
            </button>
            <button type="button" className={tab === "source" ? "active" : ""} onClick={() => setTab("source")}>
              <Icon name="database" size={14} /> 数据源
            </button>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
            <Icon name="x" size={18} />
          </button>
        </header>
        <div className="drawer-body">
          {tab === "import" ? (
            <CsvUploader onImportComplete={onImportComplete} onQueryTable={onQueryTable} onAnalyzeTable={onAnalyzeTable} />
          ) : (
            <SourceInfo info={info} />
          )}
        </div>
      </aside>
    </>
  );
}
