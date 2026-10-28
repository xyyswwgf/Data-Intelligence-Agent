import { useState, useEffect, useRef, useCallback } from "react";
import { importCsv, previewCsv, getTables, deleteTable } from "../api";
import Icon from "./Icon";

/**
 * CSV 文件导入组件（含预览调整功能）
 *
 * 流程: 选择文件 → 预览解析结果 → 调整列名/类型/主键 → 确认导入
 */

const TYPE_OPTIONS = ["INT", "BIGINT", "DECIMAL(18,2)", "DATE", "TIMESTAMP", "VARCHAR(100)", "VARCHAR(500)", "TEXT"];

function formatTime(dateStr) {
  if (!dateStr) return "";
  try { return new Date(dateStr).toLocaleString("zh-CN", { month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit" }); }
  catch { return dateStr; }
}
function formatNumber(n) { return typeof n === "number" ? n.toLocaleString("zh-CN") : n; }

// ═══════════════════════════════════════════════════════════════
// CSV 预览调整弹窗
// ═══════════════════════════════════════════════════════════════

function CsvPreviewModal({ preview, file, onConfirm, onCancel, onRefresh, importing }) {
  // 父组件在每次重新预览时更换 key，组件重新挂载即可用新的 preview 初始化状态
  const [columnConfig, setColumnConfig] = useState(() =>
    preview.columns.map((c) => ({ name: c.name, type: c.detected_type }))
  );
  const [primaryKey, setPrimaryKey] = useState(preview.file_profile?.recommended_primary_key || "");
  const [tableAlias, setTableAlias] = useState(preview.file_profile?.suggested_table_name || "");
  const [headerRow, setHeaderRow] = useState(preview.header_row || 1);
  const [dataStartRow, setDataStartRow] = useState(preview.data_start_row || 2);
  const [combineHeaders, setCombineHeaders] = useState(preview.combine_header_rows || 1);
  const [skipTrailing, setSkipTrailing] = useState(preview.skip_trailing_rows || 0);

  const handleNameChange = (idx, newName) => {
    setColumnConfig((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], name: newName || next[idx].name };
      return next;
    });
  };

  const handleTypeChange = (idx, newType) => {
    setColumnConfig((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], type: newType };
      return next;
    });
  };

  const handleConfirm = () => {
    const namesChanged = columnConfig.some((c, i) => c.name !== preview.columns[i].name);
    const typesChanged = columnConfig.some((c, i) => c.type !== preview.columns[i].detected_type);
    const colNames = columnConfig.map((c) => c.name);
    const typeOverrides = {};
    columnConfig.forEach((c, i) => {
      if (c.type !== preview.columns[i].detected_type) typeOverrides[c.name] = c.type;
    });

    onConfirm({
      columnNames: namesChanged ? colNames : null,
      columnTypes: typesChanged ? typeOverrides : null,
      primaryKeyColumn: primaryKey || null,
      tableName: tableAlias.trim() || null,
      headerRow: headerRow,
      dataStartRow: dataStartRow,
      combineHeaders: combineHeaders,
      skipTrailing: skipTrailing,
      sheetName: preview.selected_sheet || null,
    });
  };

  const handleRefresh = () => {
    if (onRefresh) onRefresh(headerRow, dataStartRow, combineHeaders, skipTrailing, preview.selected_sheet);
  };

  // 切换工作表：表头、数据起始行、末尾行全部重新自动识别
  const handleSheetChange = (sheet) => {
    if (onRefresh) onRefresh(0, 0, 1, undefined, sheet);
  };

  return (
    <div className="conflict-overlay" onClick={onCancel}>
      <div className="preview-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="preview-header">
          <h3>📋 CSV 数据预览与调整</h3>
          <div className="preview-meta">
            <span>📄 {file.name}</span>
            <span>📝 {preview.total_rows} 行 × {preview.total_cols} 列</span>
            <span>🔤 {preview.encoding.toUpperCase()}</span>
            {preview.sheets?.length > 1 && (
              <label className="sheet-picker">
                工作表
                <select value={preview.selected_sheet} onChange={(e) => handleSheetChange(e.target.value)} disabled={importing}>
                  {preview.sheets.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
            )}
          </div>
        </div>

        {preview.file_profile && (
          <div className="file-profile">
            <div className="profile-card">
              <span>文件类型</span>
              <strong>{preview.file_profile.file_type.toUpperCase()}</strong>
            </div>
            <div className="profile-card">
              <span>推荐主键</span>
              <strong>{preview.file_profile.recommended_primary_key || "自动 _id"}</strong>
            </div>
            <div className="profile-card">
              <span>建议表名</span>
              <strong>{preview.file_profile.suggested_table_name}</strong>
            </div>
            <div className="profile-card">
              <span>空值比例</span>
              <strong>{(preview.file_profile.quality.empty_cell_ratio * 100).toFixed(2)}%</strong>
            </div>
            {preview.file_profile.primary_key_candidates?.length > 0 && (
              <div className="profile-wide">
                <strong>主键候选：</strong>
                {preview.file_profile.primary_key_candidates.map((item) => (
                  <span key={item.column}>
                    {item.column} ({Math.round(item.score * 100)}%)
                  </span>
                ))}
              </div>
            )}
            {preview.file_profile.semantic_columns?.length > 0 && (
              <div className="profile-wide">
                <strong>语义列：</strong>
                {preview.file_profile.semantic_columns.slice(0, 8).map((item) => (
                  <span key={item.column}>{item.column}: {item.semantic_type}</span>
                ))}
              </div>
            )}
            {preview.file_profile.warnings?.map((warning) => (
              <div className="profile-warning" key={warning}>{warning}</div>
            ))}
          </div>
        )}

        {/* ── 列配置 ── */}
        <div className="preview-col-config">
          <h4>⚙️ 列配置（点击修改列名或类型）</h4>
          <div className="col-config-grid">
            {columnConfig.map((col, idx) => (
              <div key={idx} className="col-config-item">
                <div className="col-config-header">
                  <input
                    className="col-name-input"
                    value={col.name}
                    onChange={(e) => handleNameChange(idx, e.target.value)}
                    title="修改列名"
                  />
                  <select
                    className="col-type-select"
                    value={col.type}
                    onChange={(e) => handleTypeChange(idx, e.target.value)}
                    title="修改数据类型"
                  >
                    {!TYPE_OPTIONS.includes(preview.columns[idx].detected_type) && (
                      <option value={preview.columns[idx].detected_type}>
                        {preview.columns[idx].detected_type}（自动识别）
                      </option>
                    )}
                    {TYPE_OPTIONS.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
                <div className="col-samples">
                  {preview.columns[idx].sample_values.map((v, vi) => (
                    <span key={vi} className="sample-val">{v || "(空)"}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ── 行号设置 ── */}
        <div className="preview-settings">
          <div className="setting-group">
            <label>📌 列名所在行：</label>
            <select value={headerRow} onChange={(e) => setHeaderRow(Number(e.target.value))}>
              {Array.from({ length: Math.min(8, preview.total_valid_lines || 8) }, (_, i) => (
                <option key={i + 1} value={i + 1}>第 {i + 1} 行</option>
              ))}
            </select>
          </div>
          <div className="setting-group">
            <label>🔗 合并表头行：</label>
            <select value={combineHeaders} onChange={(e) => setCombineHeaders(Number(e.target.value))}>
              {[1, 2, 3, 4].map((n) => (
                <option key={n} value={n}>{n} 行</option>
              ))}
            </select>
            <span className="setting-hint">Excel合并单元格场景</span>
          </div>
          <div className="setting-group">
            <label>📋 数据起始行：</label>
            <select value={dataStartRow} onChange={(e) => setDataStartRow(Number(e.target.value))}>
              {Array.from({ length: Math.min(15, preview.total_valid_lines || 15) }, (_, i) => (
                <option key={i + 1} value={i + 1}>第 {i + 1} 行</option>
              ))}
            </select>
          </div>
          <div className="setting-group">
            <label>✂️ 跳过末尾：</label>
            <select value={skipTrailing} onChange={(e) => setSkipTrailing(Number(e.target.value))}>
              {Array.from({ length: 11 }, (_, i) => (
                <option key={i} value={i}>{i === 0 ? '不跳过' : `${i} 行`}</option>
              ))}
            </select>
            {preview.auto_skip_trailing > 0 && (
              <span className="setting-hint">（已自动检测到 {preview.auto_skip_trailing} 行非数据）</span>
            )}
          </div>
          <button className="btn-secondary btn-sm" onClick={handleRefresh} disabled={importing}>
            🔄 刷新预览
          </button>
        </div>

        {/* ── 主键 & 表名设置 ── */}
        <div className="preview-settings">
          <div className="setting-group">
            <label>🔑 主键列：</label>
            <select value={primaryKey} onChange={(e) => setPrimaryKey(e.target.value)}>
              <option value="">自动生成 _id</option>
              {columnConfig.map((c, idx) => (
                <option key={idx} value={c.name}>{c.name}</option>
              ))}
            </select>
          </div>
          <div className="setting-group">
            <label>📊 表名：</label>
            <input
              className="table-name-input"
              placeholder="自动从文件名推导"
              value={tableAlias}
              onChange={(e) => setTableAlias(e.target.value)}
            />
            <span className="setting-hint">留空则自动生成（csv_前缀）</span>
          </div>
        </div>

        {/* ── 数据预览表格 ── */}
        <div className="preview-table-wrap">
          <h4>👁️ 数据预览（前 {Math.min(20, preview.preview_rows.length)} 行）</h4>
          <div className="table-scroll">
            <table className="preview-table">
              <thead>
                <tr>
                  <th className="row-num">#</th>
                  {columnConfig.map((c, i) => (
                    <th key={i}>{c.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.preview_rows.map((row, ri) => (
                  <tr key={ri}>
                    <td className="row-num">{ri + 1}</td>
                    {row.map((cell, ci) => (
                      <td key={ci}>{cell !== null && cell !== undefined ? String(cell) : "—"}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* ── 操作按钮 ── */}
        <div className="preview-actions">
          <button className="btn-primary" onClick={handleConfirm} disabled={importing}>
            {importing ? "⏳ 导入中..." : "✅ 确认导入"}
          </button>
          <button className="btn-secondary" onClick={onCancel} disabled={importing}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// 冲突对话框
// ═══════════════════════════════════════════════════════════════

function ConflictDialog({ tableName, onOverwrite, onRename, onCancel }) {
  const [customName, setCustomName] = useState("");
  return (
    <div className="conflict-overlay" onClick={onCancel}>
      <div className="conflict-dialog" onClick={(e) => e.stopPropagation()}>
        <h4>⚠️ 表名冲突</h4>
        <p>表 <code>{tableName}</code> 已存在。请选择处理方式：</p>
        <div className="conflict-rename">
          <label>自定义表名（可选）：</label>
          <input type="text" className="rename-input" placeholder="输入新表名（不含 csv_ 前缀）"
            value={customName} onChange={(e) => setCustomName(e.target.value)} />
        </div>
        <div className="conflict-actions">
          <button className="btn-primary" onClick={onOverwrite}>🔄 覆盖原表</button>
          <button className="btn-secondary" onClick={() => onRename(customName || undefined)}>
            ✏️ {customName ? `使用 "${customName}"` : "自动重命名"}
          </button>
          <button className="btn-cancel" onClick={onCancel}>取消</button>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// 导入历史
// ═══════════════════════════════════════════════════════════════

function ImportHistory({ tables, onDelete, onQueryTable, onAnalyzeTable, loading }) {
  if (!tables || tables.length === 0) return null;
  return (
    <div className="import-history">
      <h4>已导入的数据表 <span className="count">{tables.length}</span></h4>
      {tables.map((t) => (
        <div key={t.table_name} className="import-item">
          <div className="import-left">
            <span className="import-icon"><Icon name="table" size={16} /></span>
            <div className="import-info">
              <span className="import-name">{t.table_name}</span>
              <span className="import-meta">
                {formatNumber(t.row_count)} 行{t.imported_at && ` · ${formatTime(t.imported_at)}`}
              </span>
            </div>
          </div>
          <div className="import-actions">
            <button className="btn-query" onClick={() => onQueryTable(t.table_name)}>查询</button>
            <button className="btn-analyze" onClick={() => onAnalyzeTable(t.table_name)}>
              <Icon name="sparkles" size={13} /> 分析
            </button>
            <button className="delete-btn" onClick={() => onDelete(t.table_name)} disabled={loading} title="删除表" aria-label="删除表">
              <Icon name="x" size={14} />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// 主组件
// ═══════════════════════════════════════════════════════════════

export default function CsvUploader({ onImportComplete, onQueryTable, onAnalyzeTable }) {
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [conflict, setConflict] = useState(null);
  const [pendingFile, setPendingFile] = useState(null);
  const [pendingConfig, setPendingConfig] = useState(null);
  const [importedTables, setImportedTables] = useState([]);
  const [preview, setPreview] = useState(null);
  const [previewSeq, setPreviewSeq] = useState(0);
  const [previewFile, setPreviewFile] = useState(null);
  const fileInputRef = useRef(null);

  const loadTables = useCallback(() => {
    getTables({ importedOnly: true })
      .then((data) => setImportedTables(data.tables || []))
      .catch(() => { /* 后端未就绪时静默 */ });
  }, []);
  useEffect(() => { loadTables(); }, [loadTables]);

  // ── 文件选择 → 先预览 ──
  const handleFilePicked = async (file, hr, dsr, ch, st, sheet) => {
    setError(null);
    setResult(null);
    setConflict(null);
    setPreviewFile(file);
    setUploading(true);
    try {
      const opts = {};
      if (hr) opts.headerRow = hr;
      if (dsr) opts.dataStartRow = dsr;
      if (ch) opts.combineHeaders = ch;
      if (st !== undefined) opts.skipTrailing = st;
      if (sheet) opts.sheetName = sheet;
      const data = await previewCsv(file, opts);
      setPreview(data);
      setPreviewSeq((n) => n + 1);
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === "string" ? detail : (detail?.message || "预览失败"));
      setPreviewFile(null);
    } finally {
      setUploading(false);
    }
  };

  // ── 预览中调整 → 重新预览 ──
  const handleRefreshPreview = async (hr, dsr, ch, st, sheet) => {
    if (previewFile) await handleFilePicked(previewFile, hr, dsr, ch, st, sheet);
  };

  // ── 预览确认 → 正式导入 ──
  const handlePreviewConfirm = async (config) => {
    setPendingConfig(config);
    setPreview(null);
    await doImport(previewFile, config);
  };

  const doImport = async (file, config = {}) => {
    setUploading(true);
    setError(null);
    setResult(null);
    setConflict(null);
    try {
      const opts = {
        overwrite: config.overwrite || false,
        tableName: config.tableName || null,
        columnNames: config.columnNames || null,
        columnTypes: config.columnTypes || null,
        primaryKeyColumn: config.primaryKeyColumn || null,
        headerRow: config.headerRow || null,
        dataStartRow: config.dataStartRow || null,
        combineHeaders: config.combineHeaders || null,
        skipTrailing: config.skipTrailing || 0,
        sheetName: config.sheetName || null,
      };
      const data = await importCsv(file, opts);
      setResult(data);
      setPendingFile(null);
      setPendingConfig(null);
      setPreviewFile(null);
      if (onImportComplete) onImportComplete();
      loadTables();
    } catch (err) {
      const detail = err.response?.data?.detail;
      if (err.response?.status === 409 && detail?.error === "table_exists") {
        setConflict(detail);
        setPendingFile(file);
        setPendingConfig(config);
      } else {
        setError(typeof detail === "string" ? detail : (detail?.message || "导入失败"));
      }
    } finally {
      setUploading(false);
    }
  };

  const handleOverwrite = () => {
    setConflict(null);
    if (pendingFile) doImport(pendingFile, { ...(pendingConfig || {}), overwrite: true });
  };
  const handleRename = (customName) => {
    const baseName = conflict?.table_name || "csv_imported";
    // 未填写新名称时自动追加时间戳后缀，避免以同名再次提交导致反复冲突
    const stamp = new Date().toISOString().replace(/\D/g, "").slice(4, 14);
    const tableName = customName?.trim() || `${baseName}_${stamp}`;
    setConflict(null);
    if (pendingFile) doImport(pendingFile, { ...(pendingConfig || {}), tableName, overwrite: false });
  };
  const handleCancelConflict = () => { setConflict(null); setPendingFile(null); setPendingConfig(null); };
  const handlePreviewCancel = () => { setPreview(null); setPreviewFile(null); };

  const handleDelete = async (tableName) => {
    if (!confirm(`确定要删除表 "${tableName}" 吗？此操作不可撤销。`)) return;
    setDeleting(true); setError(null);
    try { await deleteTable(tableName); loadTables(); if (onImportComplete) onImportComplete(); }
    catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "删除失败");
    }
    finally { setDeleting(false); }
  };

  // ── 拖拽事件 ──
  const handleDragEnter = (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true); };
  const handleDragOver = (e) => { e.preventDefault(); e.stopPropagation(); };
  const handleDragLeave = (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(false); };
  const handleDrop = (e) => {
    e.preventDefault(); e.stopPropagation(); setDragOver(false);
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      if (/\.(csv|xlsx|xlsm)$/i.test(files[0].name)) handleFilePicked(files[0]);
      else setError("仅支持 .csv 和 .xlsx 格式的文件（旧版 .xls 请先另存为 .xlsx）");
    }
  };
  const handleFileSelect = (e) => {
    const file = e.target.files[0];
    if (file) handleFilePicked(file);
  };

  const handleQueryTable = (tableName) => {
    if (onQueryTable) onQueryTable(tableName);
  };

  const handleAnalyzeTable = (tableName) => {
    if (onAnalyzeTable) onAnalyzeTable(tableName);
  };

  return (
    <div className="csv-uploader">
      <div className="csv-content">
        {/* 上传区域 */}
        <div className={`drop-zone ${dragOver ? "drag-over" : ""}`}
          onDragEnter={handleDragEnter} onDragOver={handleDragOver}
          onDragLeave={handleDragLeave} onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}>
          <input ref={fileInputRef} type="file" accept=".csv,text/csv,.xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            style={{ display: "none" }} onChange={handleFileSelect} disabled={uploading} />
          {!uploading && !result && (
            <>
              <div className="drop-icon"><Icon name="upload" size={26} /></div>
              <div className="drop-text">拖拽 CSV 或 Excel 文件到这里，或点击选择文件</div>
              <div className="drop-hint">支持 .csv（UTF-8 / GBK）和 .xlsx（可选工作表），最大 10MB，自动识别表头和列类型</div>
            </>
          )}
          {uploading && !preview && (
            <div className="import-progress">
              <div className="spinner" />
              <p>正在解析 CSV 文件...</p>
            </div>
          )}
          {result && !uploading && (
            <div className="import-success">
              <div className="success-icon"><Icon name="check" size={24} strokeWidth={2.4} /></div>
              <h4>导入成功！</h4>
              <div className="success-stats">
                <div className="stat-item"><div className="stat-value">{result.row_count}</div><div className="stat-label">数据行数</div></div>
                <div className="stat-item"><div className="stat-value">{result.columns.length}</div><div className="stat-label">列数</div></div>
              </div>
              <div className="success-table-name">表名：<code>{result.table_name}</code></div>
              <div className="success-columns">
                {result.columns.map((col) => (
                  <span key={col.name} className="column-tag">{col.name} <small>({col.type})</small></span>
                ))}
              </div>
              <div className="success-actions">
                <button className="btn-primary" onClick={() => handleAnalyzeTable(result.table_name)}>Agent 分析这张表</button>
                <button className="btn-secondary" onClick={() => handleQueryTable(result.table_name)}>查询数据</button>
                <button className="btn-secondary" onClick={() => setResult(null)}>继续上传</button>
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="error-banner">
            <Icon name="alert" size={16} /><span>{error}</span>
            <button className="btn-dismiss" onClick={() => setError(null)} aria-label="关闭"><Icon name="x" size={14} /></button>
          </div>
        )}

        <ImportHistory
          tables={importedTables}
          onDelete={handleDelete}
          onQueryTable={handleQueryTable}
          onAnalyzeTable={handleAnalyzeTable}
          loading={deleting}
        />
      </div>

      {/* 预览弹窗 */}
      {preview && (
        <CsvPreviewModal
          key={previewSeq}
          preview={preview}
          file={previewFile}
          onConfirm={handlePreviewConfirm}
          onCancel={handlePreviewCancel}
          onRefresh={handleRefreshPreview}
          importing={uploading}
        />
      )}

      {/* 冲突弹窗 */}
      {conflict && (
        <ConflictDialog
          tableName={conflict.table_name}
          onOverwrite={handleOverwrite}
          onRename={handleRename}
          onCancel={handleCancelConflict}
        />
      )}
    </div>
  );
}
