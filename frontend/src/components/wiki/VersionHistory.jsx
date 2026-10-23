import { useEffect, useMemo, useState } from "react";
import Icon from "../Icon";
import { getWikiVersion, getWikiVersions } from "../../api";
import { renderMarkdown } from "../../utils/markdown";
import { extractErrorMessage } from "../../utils/format";

export default function VersionHistory({ page, canRestore, onRestore, onClose }) {
  const [versions, setVersions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [snapshot, setSnapshot] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getWikiVersions(page.id)
      .then((data) => {
        setVersions(data.items);
        setSelected(data.items[0]?.version ?? null);
      })
      .catch((err) => setError(extractErrorMessage(err)));
  }, [page.id]);

  useEffect(() => {
    if (selected == null) return;
    getWikiVersion(page.id, selected).then(setSnapshot).catch((err) => setError(extractErrorMessage(err)));
  }, [page.id, selected]);

  const html = useMemo(() => (snapshot ? renderMarkdown(snapshot.content_md).html : ""), [snapshot]);

  return (
    <div className="conflict-overlay" onClick={onClose}>
      <div className="preview-dialog history-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head plain">
          <h3><Icon name="history" size={18} /> 版本历史 · {page.title}</h3>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭"><Icon name="x" size={18} /></button>
        </div>
        {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
        <div className="history-body">
          <ul className="version-list">
            {versions.map((v) => (
              <li key={v.version}>
                <button type="button" className={selected === v.version ? "active" : ""} onClick={() => setSelected(v.version)}>
                  <strong>v{v.version}{v.version === page.version && <em>当前</em>}</strong>
                  <span>{v.change_note || "（无说明）"}</span>
                  <small>{v.edited_by_name || "系统"} · {new Date(v.edited_at).toLocaleString("zh-CN")}</small>
                </button>
              </li>
            ))}
          </ul>
          <div className="version-preview">
            {snapshot && (
              <>
                <div className="version-preview-head">
                  <strong>{snapshot.title}</strong>
                  {canRestore && snapshot.version !== page.version && (
                    <button type="button" className="btn-primary btn-sm" onClick={() => onRestore(snapshot.version)}>
                      <Icon name="refresh" size={14} /> 恢复到此版本
                    </button>
                  )}
                </div>
                <div className="markdown-body" dangerouslySetInnerHTML={{ __html: html }} />
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
