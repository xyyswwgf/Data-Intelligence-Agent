/**
 * =========================================================================
 * API 服务层 — 与后端 NL2SQL API 通信
 * =========================================================================
 *
 * 封装的接口:
 *   queryNL / analyzeQuestion / analyzeTableQuestion → 查询与 Agent 分析
 *   getAnalysisHistory / getAnalysisDetail / analysisExportUrl → 分析历史与导出
 *   previewCsv / importCsv / getTables / deleteTable → CSV 导入与表管理
 *   healthCheck()     → 后端健康检查
 *
 * 配置:
 *   后端默认地址 http://localhost:8000
 *   超时时间 60 秒（LLM 调用可能较慢）
 * =========================================================================
 */

import axios from "axios";

// ---------------------------------------------------------------------------
// Axios 实例
// ---------------------------------------------------------------------------

// 开发环境使用 Vite 代理（/api → localhost:8000）
// 生产环境需根据部署配置调整
export const apiClient = axios.create({
  baseURL: "/",
  timeout: 60000, // LLM 调用可能耗时较长，设 60s 超时
  headers: {
    "Content-Type": "application/json",
  },
});

// ---------------------------------------------------------------------------
// 公开接口
// ---------------------------------------------------------------------------

/**
 * 发送自然语言查询，获取 SQL + 数据结果
 *
 * @param {string} question - 用户的自然语言问题
 * @returns {Promise<{
 *   sql: string,
 *   columns: string[],
 *   data: any[][],
 *   row_count: number,
 *   truncated: boolean,
 *   attempts: number
 * }>}
 *
 * @throws {Error} 网络错误、超时、或后端返回的 4xx/5xx 错误
 */
export async function queryNL(question) {
  const response = await apiClient.post("/api/query", { question });
  return response.data;
}

/**
 * 发送经营分析问题，获取 Agent 工作流结果。
 *
 * @param {string} question
 * @param {string} analysisMode
 */
export async function analyzeQuestion(question, analysisMode = "auto") {
  const response = await apiClient.post("/api/analyze", {
    question,
    analysis_mode: analysisMode,
  }, {
    timeout: 120000,
  });
  return response.data;
}

export async function analyzeTableQuestion(tableName, question, analysisMode = "auto") {
  const response = await apiClient.post("/api/analyze/table", {
    table_name: tableName,
    question,
    analysis_mode: analysisMode,
  }, {
    timeout: 120000,
  });
  return response.data;
}

export async function getAnalysisHistory() {
  const response = await apiClient.get("/api/analysis/history");
  return response.data;
}

export async function getAnalysisDetail(runId) {
  const response = await apiClient.get(`/api/analysis/${encodeURIComponent(runId)}`);
  return response.data;
}

export function analysisExportUrl(runId, format) {
  return `/api/analysis/${encodeURIComponent(runId)}/export?format=${format}`;
}

/**
 * 健康检查 — 验证后端服务是否正常运行
 *
 * @returns {Promise<{status: string, service: string, version: string}>}
 */
export async function healthCheck() {
  const response = await apiClient.get("/api/health");
  return response.data;
}

// ---------------------------------------------------------------------------
// CSV 导入接口
// ---------------------------------------------------------------------------

/**
 * 预览 CSV 文件（不创建表），返回解析结果
 *
 * @param {File} file - CSV 文件对象
 * @returns {Promise<{
 *   encoding: string,
 *   delimiter: string,
 *   total_rows: number,
 *   total_cols: number,
 *   original_headers: string[],
 *   clean_headers: string[],
 *   columns: Array<{name, original_name, detected_type, sample_values}>,
 *   preview_rows: any[][]
 * }>}
 */
export async function previewCsv(file, options = {}) {
  const formData = new FormData();
  formData.append("file", file);
  if (options.headerRow) formData.append("header_row", String(options.headerRow));
  if (options.dataStartRow) formData.append("data_start_row", String(options.dataStartRow));
  if (options.combineHeaders) formData.append("combine_headers", String(options.combineHeaders));
  if (options.skipTrailing !== undefined) formData.append("skip_trailing", String(options.skipTrailing));
  const response = await apiClient.post("/api/import/csv/preview", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 30000,
  });
  return response.data;
}

/**
 * 上传 CSV 文件并导入到数据库
 *
 * @param {File} file - CSV 文件对象
 * @param {Object} options - 可选配置
 * @param {boolean} [options.overwrite=false] - 是否覆盖同名表
 * @param {string} [options.tableName] - 自定义表名
 * @param {boolean} [options.hasHeader=true] - 第一行是否为表头
 * @param {string[]} [options.columnNames] - 自定义列名数组
 * @param {Object} [options.columnTypes] - 自定义列类型 {"col":"INT",...}
 * @param {string} [options.primaryKeyColumn] - 主键列名
 * @returns {Promise<{
 *   table_name: string,
 *   original_filename: string,
 *   columns: Array<{name: string, type: string, nullable: boolean}>,
 *   row_count: number,
 *   message: string
 * }>}
 */
export async function importCsv(file, options = {}) {
  const formData = new FormData();
  formData.append("file", file);
  if (options.overwrite) formData.append("overwrite", "true");
  if (options.tableName) formData.append("table_name", options.tableName);
  if (options.hasHeader !== undefined) {
    formData.append("has_header", String(options.hasHeader));
  }
  if (options.columnNames) {
    formData.append("column_names", JSON.stringify(options.columnNames));
  }
  if (options.columnTypes) {
    formData.append("column_types_override", JSON.stringify(options.columnTypes));
  }
  if (options.primaryKeyColumn) {
    formData.append("primary_key_column", options.primaryKeyColumn);
  }
  if (options.headerRow) formData.append("header_row", String(options.headerRow));
  if (options.dataStartRow) formData.append("data_start_row", String(options.dataStartRow));
  if (options.combineHeaders) formData.append("combine_headers", String(options.combineHeaders));
  if (options.skipTrailing) formData.append("skip_trailing", String(options.skipTrailing));
  const response = await apiClient.post("/api/import/csv", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000,
  });
  return response.data;
}

/**
 * 获取所有表信息（包含导入状态）
 *
 * @param {Object} [options]
 * @param {boolean} [options.importedOnly=false] - 仅显示导入的表
 * @returns {Promise<{
 *   tables: Array<{
 *     table_name: string,
 *     row_count: number,
 *     imported: boolean,
 *     imported_at: string|null
 *   }>
 * }>}
 */
export async function getTables(options = {}) {
  const params = {};
  if (options.importedOnly) params.imported_only = "true";
  const response = await apiClient.get("/api/tables", { params });
  return response.data;
}

/**
 * 删除导入的表
 *
 * @param {string} tableName - 要删除的表名
 * @returns {Promise<{deleted: string, message: string}>}
 */
export async function deleteTable(tableName) {
  const response = await apiClient.delete(`/api/tables/${encodeURIComponent(tableName)}`);
  return response.data;
}
