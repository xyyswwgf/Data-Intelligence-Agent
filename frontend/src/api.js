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
// 登录态：token 存 localStorage，请求自动携带；401 时通知应用回到登录页
// ---------------------------------------------------------------------------

const TOKEN_KEY = "datamind.token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // 隐私模式下 localStorage 不可用：本次会话内仍可用内存中的请求头
  }
}

apiClient.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error.config?.url || "";
    if (error.response?.status === 401 && !url.includes("/api/auth/login")) {
      setToken(null);
      window.dispatchEvent(new Event("auth:expired"));
    }
    return Promise.reject(error);
  },
);

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

/** 下载需要鉴权的文件：用 axios 带 token 取 blob，再触发浏览器保存 */
async function downloadBlob(url, fallbackName) {
  const response = await apiClient.get(url, { responseType: "blob", timeout: 120000 });
  const disposition = response.headers["content-disposition"] || "";
  const match = disposition.match(/filename\*=UTF-8''([^;]+)/i) || disposition.match(/filename="?([^";]+)"?/i);
  const filename = match ? decodeURIComponent(match[1]) : fallbackName;
  const href = URL.createObjectURL(response.data);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

export function downloadAnalysisReport(runId, format) {
  return downloadBlob(`/api/analysis/${encodeURIComponent(runId)}/export?format=${format}`, `analysis.${format === "html" ? "html" : "md"}`);
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

// ---------------------------------------------------------------------------
// 认证 / 用户
// ---------------------------------------------------------------------------

export async function login(username, password) {
  const response = await apiClient.post("/api/auth/login", { username, password });
  setToken(response.data.token);
  return response.data;
}

export async function logout() {
  try {
    await apiClient.post("/api/auth/logout");
  } finally {
    setToken(null);
  }
}

export async function getMe() {
  return (await apiClient.get("/api/auth/me")).data;
}

export async function changePassword(oldPassword, newPassword) {
  return (await apiClient.post("/api/auth/change-password", { old_password: oldPassword, new_password: newPassword })).data;
}

export async function listUsers() {
  return (await apiClient.get("/api/admin/users")).data;
}

export async function createUser(payload) {
  return (await apiClient.post("/api/admin/users", payload)).data;
}

export async function updateUser(id, payload) {
  return (await apiClient.patch(`/api/admin/users/${id}`, payload)).data;
}

export async function getAuditLogs(params) {
  return (await apiClient.get("/api/admin/audit", { params })).data;
}

export async function getSystemStatus() {
  return (await apiClient.get("/api/admin/system", { timeout: 90000 })).data;
}

export async function reindexKnowledgeBase(allPages = false) {
  return (await apiClient.post("/api/admin/kb/reindex", null, { params: { all_pages: allPages } })).data;
}

// ---------------------------------------------------------------------------
// 知识库 / Wiki
// ---------------------------------------------------------------------------

export async function getWikiTree() {
  return (await apiClient.get("/api/wiki/tree")).data;
}

export async function getWikiIndex() {
  return (await apiClient.get("/api/wiki/index")).data;
}

export async function getWikiTag(tag) {
  return (await apiClient.get(`/api/wiki/tags/${encodeURIComponent(tag)}`)).data;
}

export async function searchWiki(q) {
  return (await apiClient.get("/api/wiki/search", { params: { q } })).data;
}

export async function getWikiPage(slug) {
  return (await apiClient.get(`/api/wiki/pages/${encodeURIComponent(slug)}`)).data;
}

export async function createWikiPage(payload) {
  return (await apiClient.post("/api/wiki/pages", payload)).data;
}

export async function updateWikiPage(id, payload) {
  return (await apiClient.put(`/api/wiki/pages/${id}`, payload)).data;
}

export async function deleteWikiPage(id) {
  return (await apiClient.delete(`/api/wiki/pages/${id}`)).data;
}

export async function getWikiVersions(id) {
  return (await apiClient.get(`/api/wiki/pages/${id}/versions`)).data;
}

export async function getWikiVersion(id, version) {
  return (await apiClient.get(`/api/wiki/pages/${id}/versions/${version}`)).data;
}

export async function restoreWikiVersion(id, version) {
  return (await apiClient.post(`/api/wiki/pages/${id}/versions/${version}/restore`)).data;
}

export async function uploadKnowledgeDocument(file, { parentId, tags } = {}) {
  const formData = new FormData();
  formData.append("file", file);
  if (parentId) formData.append("parent_id", String(parentId));
  if (tags) formData.append("tags", tags);
  return (
    await apiClient.post("/api/kb/documents", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300000,
    })
  ).data;
}

export function downloadKnowledgeDocument(documentId, filename) {
  return downloadBlob(`/api/kb/documents/${documentId}/download`, filename || "document");
}

export async function askKnowledgeBase(question) {
  return (await apiClient.post("/api/kb/ask", { question }, { timeout: 120000 })).data;
}
