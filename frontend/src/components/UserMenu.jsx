import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";
import { ROLE_LABEL, SETTINGS_SHORTCUT, formatCredits } from "../navigation";

/**
 * 左下角用户入口：点击头像卡片弹出菜单（方案与剩余额度、设置、使用量、方案、退出）。
 * 点击菜单外、按 Esc 或选择菜单项后关闭。
 */
export default function UserMenu({ user, online, account, onOpenSettings, onLogout }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (fn) => () => {
    setOpen(false);
    fn();
  };
  const name = user.display_name || user.username;
  const quota = account?.plan.monthly_credits || 0;
  const pct = quota > 0 ? Math.min(100, (account.plan_used / quota) * 100) : 100;

  return (
    <div className="user-card-wrap" ref={wrapRef}>
      {open && (
        <div className="user-menu" role="menu">
          <div className="user-menu-head">
            <strong>{name}</strong>
            <small>@{user.username} · {ROLE_LABEL[user.role]}</small>
          </div>
          {account && (
            <button type="button" role="menuitem" className="user-menu-quota" onClick={pick(() => onOpenSettings("usage"))}>
              <span className="user-menu-quota-row">
                <span className="plan-badge">{account.plan.name}</span>
                <small>剩余 {formatCredits(account.plan_remaining)} 点{account.balance ? ` · 余额 ${formatCredits(account.balance)}` : ""}</small>
              </span>
              <span className={`quota-bar ${pct >= 90 ? "warn" : ""}`}><span style={{ width: `${pct}%` }} /></span>
            </button>
          )}
          <button type="button" role="menuitem" onClick={pick(() => onOpenSettings("general"))}>
            <Icon name="settings" size={16} /> <span>设置</span> <kbd>{SETTINGS_SHORTCUT}</kbd>
          </button>
          <button type="button" role="menuitem" onClick={pick(() => onOpenSettings("usage"))}>
            <Icon name="chart" size={16} /> <span>使用量</span>
          </button>
          <button type="button" role="menuitem" onClick={pick(() => onOpenSettings("plan"))}>
            <Icon name="coins" size={16} /> <span>查看所有方案</span>
          </button>
          {user.role === "admin" && (
            <button type="button" role="menuitem" onClick={pick(() => onOpenSettings("members"))}>
              <Icon name="shield" size={16} /> <span>管理成员与计费</span>
            </button>
          )}
          <div className="user-menu-sep" />
          <button type="button" role="menuitem" onClick={pick(() => onOpenSettings("about"))}>
            <Icon name="info" size={16} /> <span>关于与帮助</span>
          </button>
          <button type="button" role="menuitem" onClick={pick(onLogout)}>
            <Icon name="logout" size={16} /> <span>退出登录</span>
          </button>
        </div>
      )}
      <button
        type="button"
        className={`status-card ${open ? "open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="avatar">{name.slice(0, 1).toUpperCase()}</span>
        <div className="status-card-text">
          <strong>{name}</strong>
          <small>
            <span className={`status-dot inline ${online ? "on" : "off"}`} /> {account ? account.plan.name : ROLE_LABEL[user.role]}
          </small>
        </div>
        <Icon name="chevron" size={14} className="status-card-chevron" />
      </button>
    </div>
  );
}
