import { useRef, useState } from "react";
import Icon from "../Icon";
import { importStoreData } from "../../api";
import { extractErrorMessage } from "../../utils/format";
import { STORE_DATA_ACCEPT, isStoreDataFile } from "./storeDataFiles";

const TABLE_LABELS = {
  fact_store_sales: "销售记录",
  fact_store_targets: "月度任务",
  fact_payments: "收款记录",
  fact_purchase_in: "入库明细",
  fact_inventory: "库存",
  fact_stock_age: "库龄",
  dim_product_policy: "产品政策",
  dim_product_price: "进店价格",
};

const describe = (records) =>
  Object.entries(records || {}).map(([table, n]) => `${TABLE_LABELS[table] || table} ${n.toLocaleString()} 行`).join("，");

/**
 * 门店数据导入：先识别（不写入）预览每个文件的类型和行数，确认后写入门店业务表并更新知识库页面。
 * 只替换文件覆盖到的门店-月份，以前月份的数据保留。
 */
export default function StoreDataDialog({ initialFiles = [], onClose, onDone, onOpen }) {
  const [files, setFiles] = useState(initialFiles.filter(isStoreDataFile));
  const [stage, setStage] = useState("select"); // select → preview → done
  const [report, setReport] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  const addFiles = (list) => {
    const incoming = Array.from(list);
    const rejected = incoming.filter((f) => !isStoreDataFile(f));
    setError(rejected.length ? `已忽略非 Excel 文件：${rejected.map((f) => f.name).join("、")}` : null);
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [...prev, ...incoming.filter((f) => isStoreDataFile(f) && !names.has(f.name))];
    });
    setStage("select");
    setReport(null);
  };

  const removeFile = (name) => {
    setFiles((prev) => prev.filter((f) => f.name !== name));
    setStage("select");
    setReport(null);
  };

  const run = async (dryRun) => {
    setRunning(true);
    setError(null);
    try {
      const res = await importStoreData(files, { dryRun });
      setReport(res);
      setStage(dryRun ? "preview" : "done");
      if (!dryRun) onDone();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setRunning(false);
    }
  };

  const byName = Object.fromEntries((report?.files || []).map((f) => [f.file, f]));
  const totals = stage === "done" ? report?.inserted : report?.would_insert;
  const recognised = (report?.files || []).some((f) => Object.keys(f.records || {}).length);

  return (
    <div className="conflict-overlay" onClick={() => !running && onClose()}>
      <div className="preview-dialog upload-dialog store-data-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head plain">
          <h3><Icon name="database" size={18} /> 导入门店数据</h3>
          <button type="button" className="icon-btn" onClick={onClose} disabled={running} aria-label="关闭"><Icon name="x" size={18} /></button>
        </div>

        {stage !== "done" && (
          <div
            className={`drop-zone ${dragOver ? "drag-over" : ""}`}
            onClick={() => !running && inputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); if (!running) addFiles(e.dataTransfer.files); }}
          >
            <input ref={inputRef} type="file" multiple accept={STORE_DATA_ACCEPT} style={{ display: "none" }} onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
            <div className="drop-icon"><Icon name="upload" size={26} /></div>
            <div className="drop-text">拖拽 Excel 文件到这里，或点击选择（可多选）</div>
            <div className="drop-hint">
              销售台账、微信 / 支付宝收款明细、ERP 进存表、库龄表、门店指导书。按表格内容自动识别，
              数字写入数据表供分析，指导书和月度任务生成知识库页面
            </div>
          </div>
        )}

        {files.length > 0 && (
          <ul className="upload-list">
            {files.map((f) => {
              const item = byName[f.name];
              const ok = item && Object.keys(item.records || {}).length > 0;
              return (
                <li key={f.name} className={item ? (ok ? "done" : "error") : "pending"}>
                  <Icon name={item ? (ok ? "check" : "alert") : "file"} size={15} />
                  <span className="upload-name">{f.name}</span>
                  <span className="upload-status">
                    {!item && "待识别"}
                    {item && ok && <><strong>{item.kind}</strong> · {describe(item.records)}</>}
                    {item && !ok && (item.kind === "unknown" ? "无法识别，将跳过" : item.kind)}
                  </span>
                  {stage !== "done" && !running && (
                    <button type="button" className="icon-btn small" onClick={() => removeFile(f.name)} aria-label="移除"><Icon name="x" size={14} /></button>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {report?.notes?.length > 0 && (
          <div className="callout warn">
            <Icon name="alert" size={16} />
            <ul className="store-notes">{report.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          </div>
        )}

        {stage === "preview" && recognised && (
          <div className="callout info">
            <Icon name="bulb" size={16} />
            <span>
              将写入：{describe(totals)}。只替换这些文件覆盖到的门店和月份，其他月份的数据保留；
              库存、库龄按最新一次导入整体更新。
            </span>
          </div>
        )}

        {stage === "done" && (
          <div className="callout info">
            <Icon name="check" size={16} />
            <span>
              已导入：{describe(totals)}。知识库更新 {report.knowledge?.pages ?? 0} 个页面
              {report.knowledge?.queued ? `，${report.knowledge.queued} 个页面正在后台建立索引` : ""}。
              <button type="button" className="text-link" onClick={() => onOpen("store-data-guide")}>查看门店与数据说明</button>
            </span>
          </div>
        )}

        {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}

        <div className="preview-actions">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={running}>{stage === "done" ? "完成" : "取消"}</button>
          {stage === "select" && (
            <button type="button" className="btn-primary" onClick={() => run(true)} disabled={running || files.length === 0}>
              {running ? "识别中…" : `识别文件${files.length ? `（${files.length}）` : ""}`}
            </button>
          )}
          {stage === "preview" && (
            <button type="button" className="btn-primary" onClick={() => run(false)} disabled={running || !recognised}>
              {running ? "导入中…" : "确认导入"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
