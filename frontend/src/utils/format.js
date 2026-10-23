export function formatPercent(value, signed = false) {
  if (value === null || value === undefined || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  const sign = signed && num > 0 ? "+" : "";
  return `${sign}${(num * 100).toFixed(2)}%`;
}

export function formatMetric(value, name) {
  if (value === null || value === undefined || value === "") return "—";
  if (name?.includes("率")) return formatPercent(value);
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return num.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

export function formatCell(value) {
  if (value === null || value === undefined) return "—";
  return typeof value === "number" ? formatMetric(value) : String(value);
}

export function extractErrorMessage(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) return detail[0].msg || "请求参数校验失败";
  if (detail?.message) return detail.message;
  if (err?.code === "ECONNABORTED") return "请求超时，请稍后重试";
  if (!err?.response) return "无法连接后端服务，请确认后端已启动（端口 8000）";
  return err?.message || "请求失败，请稍后重试";
}
