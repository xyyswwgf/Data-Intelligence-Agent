import { useState } from "react";
import Icon from "./Icon";
import { login } from "../api";
import { extractErrorMessage } from "../utils/format";

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true);
    setError(null);
    try {
      const data = await login(username.trim(), password);
      onLogin(data.user);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-shell">
      <div className="login-glow" aria-hidden="true" />
      <form className="login-card" onSubmit={submit}>
        <span className="orb orb-lg" />
        <h1>
          欢迎使用 <span className="gradient-text">DataMind</span>
        </h1>
        <p className="login-sub">数据智能 Agent · 企业知识库</p>
        <label>
          <span>用户名</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus />
        </label>
        <label>
          <span>密码</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && (
          <div className="callout danger">
            <Icon name="alert" size={16} />
            <span>{error}</span>
          </div>
        )}
        <button type="submit" className="btn-primary login-btn" disabled={loading || !username.trim() || !password}>
          {loading ? "登录中…" : "登录"}
        </button>
        <p className="login-foot">
          <Icon name="shield" size={13} /> 私有化部署 · 数据不出企业内网
        </p>
      </form>
    </div>
  );
}
