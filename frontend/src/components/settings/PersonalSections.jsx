import { useState } from "react";
import Icon from "../Icon";
import { changePassword, deleteAllChats, exportMyData } from "../../api";
import { MODE_OPTIONS, ROLE_LABEL, SETTINGS_SHORTCUT } from "../../navigation";
import { extractErrorMessage } from "../../utils/format";

export function SettingsRow({ title, hint, children }) {
  return (
    <div className="settings-row">
      <div className="settings-row-text">
        <strong>{title}</strong>
        {hint && <small>{hint}</small>}
      </div>
      <div className="settings-row-control">{children}</div>
    </div>
  );
}

export function GeneralSection({ models, modelId, onModelChange, defaultMode, onDefaultModeChange }) {
  const profiles = models.filter((m) => m.id != null);
  return (
    <div className="settings-card">
      <SettingsRow title="默认模型" hint="提问时默认使用的大模型；对话时也可以在输入框下方临时切换。可选模型在「发现 → 模型」中管理">
        <select value={profiles.some((m) => m.id === modelId) ? modelId : ""}
          onChange={(e) => onModelChange(e.target.value ? Number(e.target.value) : null)}>
          <option value="">跟随系统默认</option>
          {profiles.map((m) => (
            <option key={m.id} value={m.id}>{m.name}（{m.kind === "local" ? "本地" : "云端"}{m.is_default ? " · 系统默认" : ""}）</option>
          ))}
        </select>
      </SettingsRow>
      <SettingsRow title="默认对话模式" hint={MODE_OPTIONS.find((m) => m.id === defaultMode)?.hint}>
        <select value={defaultMode} onChange={(e) => onDefaultModeChange(e.target.value)}>
          {MODE_OPTIONS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
      </SettingsRow>
      <SettingsRow title="界面语言" hint="目前提供简体中文">
        <select value="zh-CN" disabled><option value="zh-CN">简体中文</option></select>
      </SettingsRow>
      <p className="settings-note">以上设置保存在当前浏览器中。</p>
    </div>
  );
}

export function AccountSection({ user, account, onPasswordChanged }) {
  const [form, setForm] = useState({ old: "", next: "", confirm: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (form.next !== form.confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    setBusy(true);
    try {
      await changePassword(form.old, form.next);
      onPasswordChanged();
    } catch (err) {
      setError(extractErrorMessage(err));
      setBusy(false);
    }
  };

  const name = user.display_name || user.username;
  return (
    <>
      <div className="settings-card">
        <div className="settings-profile">
          <span className="avatar avatar-lg">{name.slice(0, 1).toUpperCase()}</span>
          <div>
            <strong>{name}</strong>
            <small>@{user.username} · {ROLE_LABEL[user.role]}{account ? ` · ${account.plan.name}` : ""}</small>
          </div>
        </div>
        <p className="settings-note">显示名、角色由管理员在「设置 → 成员与权限」中设置。</p>
      </div>

      <form className="settings-card settings-form" onSubmit={submit}>
        <h4>修改密码</h4>
        <label>
          当前密码
          <input type="password" value={form.old} onChange={(e) => setForm({ ...form, old: e.target.value })} autoComplete="current-password" required />
        </label>
        <label>
          新密码
          <input type="password" value={form.next} onChange={(e) => setForm({ ...form, next: e.target.value })} autoComplete="new-password" minLength={8} required />
        </label>
        <label>
          确认新密码
          <input type="password" value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} autoComplete="new-password" minLength={8} required />
        </label>
        <p className="settings-note">至少 8 位，包含字母和数字。修改后需要重新登录，其他设备上的登录也会失效。</p>
        {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
        <div>
          <button type="submit" className="btn-primary" disabled={busy}>{busy ? "保存中…" : "修改密码"}</button>
        </div>
      </form>
    </>
  );
}

export function PrivacySection({ onOpenMemory, onChatsDeleted }) {
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(null);

  const run = async (key, fn) => {
    setBusy(key);
    setMessage(null);
    try {
      setMessage(await fn());
    } catch (err) {
      setMessage(extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const removeChats = () => {
    if (!window.confirm("删除你的全部对话？对话内容会被永久删除，长期记忆和使用量记录不受影响。")) return;
    run("delete", async () => {
      const res = await deleteAllChats();
      onChatsDeleted();
      return `已删除 ${res.deleted} 个对话。`;
    });
  };

  return (
    <>
      <div className="settings-card">
        <h4>你的数据如何保存</h4>
        <ul className="settings-tips">
          <li>对话、长期记忆只有你自己能查看；管理员看不到对话内容。</li>
          <li>为了计费和安全，系统会记录每次提问的模型、token 用量，以及登录、导入等关键操作（审计日志）。</li>
          <li>使用本地大模型时，提问和数据不会离开企业服务器；使用云端模型时会发送给对应的模型服务商。</li>
        </ul>
      </div>
      <div className="settings-card">
        <SettingsRow title="长期记忆" hint="开启后助手会记住你的偏好和背景">
          <button type="button" className="btn-secondary btn-sm" onClick={onOpenMemory}>管理记忆</button>
        </SettingsRow>
        <SettingsRow title="导出我的数据" hint="对话、记忆、使用量与账单，JSON 格式">
          <button type="button" className="btn-secondary btn-sm" disabled={busy === "export"}
            onClick={() => run("export", async () => { await exportMyData(); return "已开始下载导出文件。"; })}>
            <Icon name="download" size={14} /> {busy === "export" ? "导出中…" : "导出"}
          </button>
        </SettingsRow>
        <SettingsRow title="删除全部对话" hint="永久删除，无法恢复">
          <button type="button" className="btn-danger btn-sm" disabled={busy === "delete"} onClick={removeChats}>
            <Icon name="trash" size={14} /> 删除
          </button>
        </SettingsRow>
        {message && <div className="callout info"><Icon name="check" size={16} /><span>{message}</span></div>}
      </div>
    </>
  );
}

const SHORTCUTS = [
  [SETTINGS_SHORTCUT, "打开设置"],
  ["Enter", "发送消息"],
  ["Shift + Enter", "输入框内换行"],
  ["Esc", "关闭弹窗和菜单"],
];

export function AboutSection({ sourceInfo }) {
  const db = sourceInfo?.database;
  return (
    <>
      <div className="settings-card">
        <div className="settings-profile">
          <span className="orb orb-sm" />
          <div>
            <strong>DataMind 数据智能 Agent</strong>
            <small>门店经营分析 · 企业知识库 · 模型与技能市场</small>
          </div>
        </div>
        <SettingsRow title="数据连接" hint={db ? `${db.engine} · ${db.name}` : "无法连接后端或数据库"}>
          <span className={`status-pill ${db ? "ready" : "failed"}`}>{db ? "正常" : "异常"}</span>
        </SettingsRow>
      </div>
      <div className="settings-card">
        <h4>快捷键</h4>
        <dl className="shortcut-list">
          {SHORTCUTS.map(([key, label]) => (
            <div key={key}><dt>{label}</dt><dd><kbd>{key}</kbd></dd></div>
          ))}
        </dl>
      </div>
      <div className="settings-card">
        <h4>使用提示</h4>
        <ul className="settings-tips">
          <li>「智能分析」用来提问和分析数据，「知识库」查阅和维护门店指导书、制度和口径，「发现」浏览和启用模型、技能、工具。</li>
          <li>每月的门店报表在「知识库 → 导入门店数据」中导入，导入前可以先预览识别结果。</li>
          <li>每次提问按模型单价折算成点数，先扣方案额度，用完再扣余额；用量在「设置 → 使用量」查看。</li>
        </ul>
      </div>
    </>
  );
}
