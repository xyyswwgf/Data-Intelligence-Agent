import { useRef, useState } from "react";
import Icon from "../Icon";
import { uploadKnowledgeDocument } from "../../api";
import { extractErrorMessage } from "../../utils/format";
import { STORE_DATA_ACCEPT, isStoreDataFile } from "./storeDataFiles";

const ACCEPT = `.md,.markdown,.txt,.pdf,.docx,${STORE_DATA_ACCEPT}`;

/**
 * 批量上传文档：逐个上传，每个文件独立显示结果（成功 / 重复 / 失败原因）。
 * Excel（销售台账、收款明细等）转到门店数据导入，数字写入数据表而不是切成文本片段。
 */
export default function UploadDialog({ pages, defaultParentId, onClose, onDone, onOpen, onStoreData }) {
  const [files, setFiles] = useState([]);
  const [parentId, setParentId] = useState(defaultParentId ?? "");
  const [tags, setTags] = useState("");
  const [running, setRunning] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  const addFiles = (list) => {
    const all = Array.from(list);
    const excel = all.filter(isStoreDataFile);
    if (excel.length && !files.length && excel.length === all.length) {
      onStoreData(excel);
      return;
    }
    const incoming = all.map((file) => (isStoreDataFile(file)
      ? { file, status: "excel", message: "" }
      : { file, status: "pending", message: "" }));
    setFiles((prev) => [...prev, ...incoming]);
  };

  const start = async () => {
    setRunning(true);
    for (let i = 0; i < files.length; i++) {
      if (files[i].status !== "pending") continue;
      setFiles((prev) => prev.map((f, j) => (j === i ? { ...f, status: "uploading" } : f)));
      try {
        const res = await uploadKnowledgeDocument(files[i].file, { parentId: parentId || null, tags });
        setFiles((prev) => prev.map((f, j) => (j === i ? { ...f, status: "done", slug: res.page.slug, message: res.page.title } : f)));
      } catch (err) {
        const detail = err.response?.data?.detail;
        const duplicate = err.response?.status === 409;
        setFiles((prev) => prev.map((f, j) => (j === i ? {
          ...f,
          status: duplicate ? "duplicate" : "error",
          slug: duplicate ? detail?.page_slug : undefined,
          message: duplicate ? detail?.message : extractErrorMessage(err),
        } : f)));
      }
    }
    setRunning(false);
    onDone();
  };

  const pendingCount = files.filter((f) => f.status === "pending").length;
  const excelFiles = files.filter((f) => f.status === "excel").map((f) => f.file);

  return (
    <div className="conflict-overlay" onClick={() => !running && onClose()}>
      <div className="preview-dialog upload-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head plain">
          <h3><Icon name="upload" size={18} /> 上传文档到知识库</h3>
          <button type="button" className="icon-btn" onClick={onClose} disabled={running} aria-label="关闭"><Icon name="x" size={18} /></button>
        </div>
        <div
          className={`drop-zone ${dragOver ? "drag-over" : ""}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
        >
          <input ref={inputRef} type="file" multiple accept={ACCEPT} style={{ display: "none" }} onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
          <div className="drop-icon"><Icon name="upload" size={26} /></div>
          <div className="drop-text">拖拽文件到这里，或点击选择（可多选）</div>
          <div className="drop-hint">Markdown / TXT / PDF / Word(.docx)，统一转换为 Wiki 页面并建立检索索引；Excel 门店数据自动转到「导入门店数据」</div>
        </div>
        <div className="editor-meta">
          <label>
            <Icon name="folder" size={14} />
            <select value={parentId} onChange={(e) => setParentId(e.target.value)} disabled={running}>
              <option value="">放在根目录</option>
              {pages.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
            </select>
          </label>
          <label className="grow">
            <Icon name="tag" size={14} />
            <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="统一添加标签，如：制度, 财务" disabled={running} />
          </label>
        </div>
        {files.length > 0 && (
          <ul className="upload-list">
            {files.map((f, i) => (
              <li key={`${f.file.name}-${i}`} className={f.status}>
                <Icon name={f.status === "done" ? "check" : f.status === "error" ? "alert" : "file"} size={15} />
                <span className="upload-name">{f.file.name}</span>
                <span className="upload-status">
                  {f.status === "pending" && "等待上传"}
                  {f.status === "uploading" && "转换中…"}
                  {f.status === "done" && <button type="button" className="text-link" onClick={() => onOpen(f.slug)}>已生成页面「{f.message}」</button>}
                  {f.status === "duplicate" && <button type="button" className="text-link" onClick={() => f.slug && onOpen(f.slug)}>{f.message}</button>}
                  {f.status === "error" && f.message}
                  {f.status === "excel" && "门店数据，需用「导入门店数据」"}
                </span>
              </li>
            ))}
          </ul>
        )}
        {excelFiles.length > 0 && !running && (
          <div className="callout info">
            <Icon name="database" size={16} />
            <span>
              Excel 文件不切成文本片段，而是写入门店数据表供分析，指导书另外生成知识库页面。
              <button type="button" className="text-link" onClick={() => onStoreData(excelFiles)}>导入这 {excelFiles.length} 个 Excel</button>
            </span>
          </div>
        )}
        <div className="preview-actions">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={running}>关闭</button>
          <button type="button" className="btn-primary" onClick={start} disabled={running || pendingCount === 0}>
            {running ? "上传中…" : `开始上传${pendingCount ? `（${pendingCount}）` : ""}`}
          </button>
        </div>
      </div>
    </div>
  );
}
