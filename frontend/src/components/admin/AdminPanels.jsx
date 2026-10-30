/** 用户与权限、审计日志、系统状态（在「设置 → 管理」中显示） */
import { useCallback, useEffect, useState } from "react";
import Icon from "../Icon";
import {
  createUser,
  getAuditLogs,
  getSystemStatus,
  listUsers,
  reindexKnowledgeBase,
  updateUser,
} from "../../api";
import { extractErrorMessage } from "../../utils/format";
import { ROLE_LABEL } from "../../navigation";

const ACTION_LABEL = {
  login: "登录",
  login_failed: "登录失败",
  logout: "退出",
  change_password: "修改密码",
  user_create: "创建用户",
  user_update: "修改用户",
  wiki_create: "新建页面",
  wiki_update: "编辑页面",
  wiki_delete: "删除页面",
  wiki_restore: "恢复版本",
  document_upload: "上传文档",
  document_upload_failed: "上传失败",
  kb_ask: "知识问答",
  kb_reindex: "重建索引",
  csv_import: "导入 CSV",
  table_delete: "删除数据表",
  store_data_import: "导入门店数据",
  plan_create: "新建方案",
  plan_update: "修改方案",
  plan_assign: "开通方案",
  balance_adjust: "充值 / 调整余额",
  data_export: "导出个人数据",
  chat_clear: "删除全部对话",
  memory_clear: "清空记忆",
  pack_switch: "切换行业包",
  eval_run: "运行准确率评测",
};

const fmt = (v) => (v ? new Date(v).toLocaleString("zh-CN") : "—");

export function UsersPanel({ currentUser }) {
  const [users, setUsers] = useState([]);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "viewer" });
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    listUsers().then((d) => setUsers(d.items)).catch((err) => setError(extractErrorMessage(err)));
  }, [version]);

  const reload = () => setVersion((v) => v + 1);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createUser({ ...form, display_name: form.display_name || null });
      setForm({ username: "", display_name: "", password: "", role: "viewer" });
      reload();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const patch = async (user, payload, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setError(null);
    try {
      await updateUser(user.id, payload);
      reload();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  };

  const resetPassword = (user) => {
    const password = window.prompt(`为 ${user.username} 设置新密码（至少 8 位，包含字母和数字）：`);
    if (password) patch(user, { password });
  };

  return (
    <div className="admin-section">
      <form className="panel user-form" onSubmit={submit}>
        <h4 className="panel-title"><Icon name="plus" size={15} /> 新建用户</h4>
        <div className="form-row">
          <input placeholder="用户名" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
          <input placeholder="显示名（可选）" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <input type="password" placeholder="初始密码" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required minLength={8} autoComplete="new-password" />
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <button type="submit" className="btn-primary" disabled={busy}>创建</button>
        </div>
        <p className="form-hint">只读成员：浏览、搜索、问答、分析；编辑：可上传文档、编辑 Wiki、导入 CSV；管理员：用户管理、删除、审计。</p>
      </form>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="panel">
        <table className="mini-table">
          <thead><tr><th>用户</th><th>角色</th><th>状态</th><th>最近登录</th><th>创建时间</th><th /></tr></thead>
          <tbody>
            {users.map((u) => {
              const self = u.id === currentUser.id;
              return (
                <tr key={u.id}>
                  <td><strong>{u.display_name}</strong> <small className="muted">@{u.username}</small>{self && <span className="tag">我</span>}</td>
                  <td>
                    <select value={u.role} disabled={self} onChange={(e) => patch(u, { role: e.target.value })}>
                      {Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                  </td>
                  <td>
                    <span className={`status-pill ${u.is_active ? (u.locked ? "processing" : "ready") : "failed"}`}>
                      {u.is_active ? (u.locked ? "已锁定" : "正常") : "已停用"}
                    </span>
                  </td>
                  <td>{fmt(u.last_login_at)}</td>
                  <td>{fmt(u.created_at)}</td>
                  <td className="row-actions">
                    <button type="button" className="text-link" onClick={() => resetPassword(u)}>重置密码</button>
                    {!self && (
                      <button
                        type="button"
                        className="text-link"
                        onClick={() => patch(u, { is_active: !u.is_active }, u.is_active ? `停用 ${u.username}？其登录会话会立即失效。` : null)}
                      >
                        {u.is_active ? "停用" : "启用"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function AuditPanel() {
  const [data, setData] = useState({ total: 0, items: [] });
  const [filters, setFilters] = useState({ action: "", username: "" });
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState(null);
  const limit = 30;

  const load = useCallback(() => {
    getAuditLogs({ limit, offset, action: filters.action || undefined, username: filters.username || undefined })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((err) => setError(extractErrorMessage(err)));
  }, [offset, filters]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="admin-section">
      <div className="panel filter-bar">
        <select value={filters.action} onChange={(e) => { setOffset(0); setFilters({ ...filters, action: e.target.value }); }}>
          <option value="">全部操作</option>
          {Object.entries(ACTION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input placeholder="按用户名筛选" value={filters.username} onChange={(e) => { setOffset(0); setFilters({ ...filters, username: e.target.value }); }} />
        <span className="muted">共 {data.total} 条</span>
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="panel">
        <table className="mini-table">
          <thead><tr><th>时间</th><th>用户</th><th>操作</th><th>对象</th><th>详情</th><th>IP</th></tr></thead>
          <tbody>
            {data.items.map((log) => (
              <tr key={log.id}>
                <td>{fmt(log.created_at)}</td>
                <td>{log.username || "—"}</td>
                <td><span className={`status-pill ${log.action.includes("fail") || log.action.includes("delete") ? "failed" : "ready"}`}>{ACTION_LABEL[log.action] || log.action}</span></td>
                <td>{log.target_type ? `${log.target_type}${log.target_id ? ` #${log.target_id}` : ""}` : "—"}</td>
                <td className="detail-cell">{Object.keys(log.detail || {}).length ? JSON.stringify(log.detail) : "—"}</td>
                <td>{log.ip || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="pager">
          <button type="button" className="btn-secondary btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</button>
          <span>{data.total ? `${offset + 1}–${Math.min(offset + limit, data.total)}` : "0"} / {data.total}</span>
          <button type="button" className="btn-secondary btn-sm" disabled={offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>下一页</button>
        </div>
      </div>
    </div>
  );
}

export function SystemPanel() {
  const [status, setStatus] = useState(null);
  const [message, setMessage] = useState(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    getSystemStatus().then(setStatus).catch((err) => setMessage(extractErrorMessage(err)));
  }, [version]);

  const reindex = async (all) => {
    if (all && !window.confirm("全量重建会重新向量化所有页面，适用于更换 Embedding 模型后。继续？")) return;
    try {
      const res = await reindexKnowledgeBase(all);
      setMessage(`已提交 ${res.queued} 个页面重建索引，后台执行中。`);
      setVersion((v) => v + 1);
    } catch (err) {
      setMessage(extractErrorMessage(err));
    }
  };

  const emb = status?.embedding;
  return (
    <div className="admin-section">
      {message && <div className="callout info"><Icon name="refresh" size={16} /><span>{message}</span></div>}
      <div className="panel-grid">
        <div className="panel">
          <h4 className="panel-title"><Icon name="activity" size={15} /> 向量模型（Embedding）</h4>
          {!status ? <span className="spinner" /> : (
            <dl className="kv">
              <dt>状态</dt>
              <dd><span className={`status-pill ${emb.ok ? "ready" : "failed"}`}>{emb.enabled ? (emb.ok ? `可用 · ${emb.latency_ms} ms` : "不可用") : "未启用"}</span></dd>
              <dt>模型</dt><dd>{emb.model}</dd>
              <dt>接口</dt><dd><code>{emb.base_url}</code></dd>
              {emb.error && (<><dt>错误</dt><dd className="muted">{emb.error}</dd></>)}
            </dl>
          )}
          <p className="form-hint">不可用时知识库自动降级为关键词检索；恢复后点击「补齐索引」生成向量。</p>
        </div>
        <div className="panel">
          <h4 className="panel-title"><Icon name="sparkles" size={15} /> 大模型与索引</h4>
          {!status ? <span className="spinner" /> : (
            <dl className="kv">
              <dt>大模型</dt>
              <dd><span className={`status-pill ${status.llm_configured ? "ready" : "processing"}`}>{status.llm_configured ? "已配置" : "未配置（问答使用抽取式答案）"}</span></dd>
              <dt>待处理页面</dt><dd>{status.pending_index}</dd>
            </dl>
          )}
          <div className="btn-row">
            <button type="button" className="btn-primary btn-sm" onClick={() => reindex(false)}><Icon name="refresh" size={14} /> 补齐索引</button>
            <button type="button" className="btn-secondary btn-sm" onClick={() => reindex(true)}>全量重建</button>
            <button type="button" className="btn-secondary btn-sm" onClick={() => setVersion((v) => v + 1)}>刷新状态</button>
          </div>
        </div>
      </div>
    </div>
  );
}
