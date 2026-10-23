import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar";
import DataDrawer from "./components/DataDrawer";
import Icon from "./components/Icon";
import ChatView from "./components/ChatView";
import LoginPage from "./components/LoginPage";
import AdminView from "./components/AdminView";
import MemoryView from "./components/MemoryView";
import WikiView from "./components/wiki/WikiView";
import { detectChartConfig } from "./utils/chartDetector";
import { extractErrorMessage } from "./utils/format";
import {
  changePassword,
  createChatSession,
  deleteChatSession,
  getChatSession,
  getMe,
  getModelOptions,
  getSkillOptions,
  getToken,
  listChatSessions,
  logout,
  streamChatMessage,
  apiClient,
} from "./api";
import "./App.css";

let turnSeq = 0;
const nextTurnId = () => `turn-${Date.now()}-${turnSeq++}`;

// ── 极简 hash 路由：#/ 新对话，#/chat/<id> 会话，#/wiki[/<slug>] 知识库，#/memory 我的记忆，#/admin 系统管理 ──
function parseHash() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const [section, ...rest] = hash.split("/");
  if (section === "wiki") return { view: "wiki", slug: rest.length ? decodeURIComponent(rest.join("/")) : null };
  if (section === "admin") return { view: "admin" };
  if (section === "memory") return { view: "memory" };
  if (section === "chat" && rest[0]) return { view: "chat", sessionId: rest[0] };
  return { view: "chat", sessionId: null };
}

function useHashRoute() {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const onChange = () => setRoute(parseHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const navigate = useCallback((path) => {
    window.location.hash = path;
  }, []);
  return [route, navigate];
}

/** 把持久化的会话消息还原成界面上的「轮次」，刷新页面后推理过程也能完整回看 */
function messagesToTurns(messages) {
  const turns = [];
  for (const msg of messages) {
    if (msg.role === "user") {
      turns.push({ id: `m-${msg.id}`, question: msg.content, mode: msg.mode, table: msg.payload?.table || null, status: "pending", events: [] });
      continue;
    }
    const turn = turns[turns.length - 1];
    if (!turn) continue;
    const payload = msg.payload || {};
    Object.assign(turn, { status: payload.error ? "error" : "done", error: payload.error });
    turn.model = payload.model;
    if (msg.mode === "assistant") {
      Object.assign(turn, { content: msg.content, citations: payload.citations || [], usage: payload.usage, events: msg.trace || [] });
    } else if (msg.mode === "query" && payload.result) {
      turn.result = { ...payload.result, chartConfig: detectChartConfig(payload.result.columns, payload.result.data) };
    } else {
      turn.result = payload.result;
    }
  }
  // 最后一条用户消息没有回复（例如上次请求中断）
  return turns.map((t) => (t.status === "pending" ? { ...t, status: "error", error: "这条消息没有完成，请重新提问。" } : t));
}

function App() {
  const [user, setUser] = useState(null);
  const [authChecked, setAuthChecked] = useState(() => !getToken());
  const [route, navigate] = useHashRoute();

  const [mode, setMode] = useState("assistant");
  const [turns, setTurns] = useState([]);
  const [loadedSessionId, setLoadedSessionId] = useState(null);
  const [sessionList, setSessionList] = useState([]);
  const [targetTable, setTargetTable] = useState(null);
  const [sourceInfo, setSourceInfo] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [models, setModels] = useState([]);
  const [skills, setSkills] = useState([]);
  const [skill, setSkill] = useState(null);
  const [modelId, setModelIdState] = useState(() => {
    try {
      const saved = localStorage.getItem("datamind.model");
      return saved ? Number(saved) : null;
    } catch {
      return null;
    }
  });
  const setModelId = (id) => {
    setModelIdState(id);
    try {
      if (id == null) localStorage.removeItem("datamind.model");
      else localStorage.setItem("datamind.model", String(id));
    } catch {
      // 隐私模式下无法记住选择，不影响使用
    }
  };
  // 正在流式写入的会话：路由切到它时不要用服务端数据覆盖本地的实时轮次
  const streamingSessionRef = useRef(null);

  const loading = turns.some((t) => t.status === "pending");

  useEffect(() => {
    if (getToken()) {
      getMe()
        .then(setUser)
        .catch(() => setUser(null))
        .finally(() => setAuthChecked(true));
    }
    const onExpired = () => setUser(null);
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, []);

  const fetchInfo = useCallback(() => {
    apiClient.get("/api/info").then((res) => setSourceInfo(res.data)).catch(() => setSourceInfo(null));
  }, []);

  const fetchSessions = useCallback(() => {
    listChatSessions().then((res) => setSessionList(res.items || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (!user) return;
    fetchInfo();
    fetchSessions();
    getModelOptions().then((d) => setModels(d.items || [])).catch(() => setModels([]));
    getSkillOptions().then((d) => setSkills(d.items || [])).catch(() => setSkills([]));
  }, [user, fetchInfo, fetchSessions, route.view]);

  // 路由中的会话 ID 变化时加载该会话
  const routeSessionId = route.view === "chat" ? route.sessionId : undefined;
  useEffect(() => {
    if (!user || routeSessionId === undefined || routeSessionId === loadedSessionId) return;
    if (routeSessionId === null) {
      Promise.resolve().then(() => {
        setTurns([]);
        setLoadedSessionId(null);
      });
      return;
    }
    if (streamingSessionRef.current === routeSessionId) {
      Promise.resolve().then(() => setLoadedSessionId(routeSessionId));
      return;
    }
    let cancelled = false;
    getChatSession(routeSessionId)
      .then((data) => {
        if (cancelled) return;
        setTurns(messagesToTurns(data.messages));
        setLoadedSessionId(routeSessionId);
        const lastTable = [...data.messages].reverse().find((m) => m.role === "user")?.payload?.table;
        setTargetTable(lastTable || null);
      })
      .catch(() => {
        if (!cancelled) navigate("/");
      });
    return () => {
      cancelled = true;
    };
  }, [user, routeSessionId, loadedSessionId, navigate]);

  const updateTurn = (id, patch) =>
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));

  const ask = async (question, options = {}) => {
    if (loading) return;
    const activeMode = options.mode || mode;
    const table = options.table !== undefined ? options.table : targetTable;
    const tableForMode = activeMode === "agent" || activeMode === "assistant" ? table : null;
    const id = nextTurnId();

    let sessionId = route.view === "chat" ? route.sessionId : null;
    setTurns((prev) => [...(sessionId ? prev : []), { id, question, mode: activeMode, table: tableForMode, status: "pending", events: [] }]);
    try {
      if (!sessionId) {
        sessionId = (await createChatSession()).id;
        streamingSessionRef.current = sessionId;
        navigate(`/chat/${sessionId}`);
      } else {
        streamingSessionRef.current = sessionId;
        if (route.view !== "chat") navigate(`/chat/${sessionId}`);
      }
      const selectedModel = models.find((m) => m.id === modelId) ? modelId : null;
      const body = { content: question, mode: activeMode, table_name: tableForMode, model_profile_id: selectedModel };
      if (activeMode === "assistant" && skill) body.skill = skill;
      await streamChatMessage(sessionId, body, (ev) => {
        if (ev.type === "session") {
          fetchSessions();
        } else if (ev.type === "model") {
          updateTurn(id, { model: ev });
        } else if (ev.type === "done") {
          if (activeMode === "assistant") {
            updateTurn(id, { status: "done", content: ev.content, citations: ev.citations, usage: ev.usage });
          } else if (ev.error) {
            updateTurn(id, { status: "error", error: ev.error });
          } else if (activeMode === "query") {
            updateTurn(id, { status: "done", result: { ...ev.result, chartConfig: detectChartConfig(ev.result.columns, ev.result.data) } });
          } else {
            updateTurn(id, { status: "done", result: ev.result });
          }
        } else {
          updateTurn(id, (t) => ({ events: [...(t.events || []), ev] }));
        }
      });
      // 流结束但没有收到 done（例如网络中断）
      updateTurn(id, (t) => (t.status === "pending" ? { status: "error", error: "连接中断，请重试。" } : {}));
    } catch (err) {
      updateTurn(id, { status: "error", error: err.message || extractErrorMessage(err) });
    } finally {
      streamingSessionRef.current = null;
      fetchSessions();
    }
  };

  const handleNewChat = () => {
    if (loading) return;
    setSidebarOpen(false);
    navigate("/");
  };

  const handleSelectSession = (sessionId) => {
    if (loading) return;
    setSidebarOpen(false);
    navigate(`/chat/${sessionId}`);
  };

  const handleDeleteSession = async (sessionId) => {
    if (!window.confirm("删除这个对话？对话内容会被永久删除，已提炼的长期记忆不受影响。")) return;
    await deleteChatSession(sessionId).catch(() => {});
    fetchSessions();
    if (route.sessionId === sessionId) navigate("/");
  };

  const handleSelectTable = (tableName) => {
    setTargetTable(tableName);
    if (mode !== "assistant") setMode("agent");
    setSidebarOpen(false);
    if (route.view !== "chat") navigate(route.sessionId ? `/chat/${route.sessionId}` : "/");
  };

  const handleQueryTable = (tableName) => {
    setDrawerOpen(false);
    setTargetTable(null);
    setMode("query");
    ask(`查看 ${tableName} 表的前 100 行数据`, { mode: "query", table: null });
  };

  const handleAnalyzeTable = (tableName) => {
    setDrawerOpen(false);
    handleSelectTable(tableName);
  };

  const handleLogout = async () => {
    await logout().catch(() => {});
    setUser(null);
    setTurns([]);
    setLoadedSessionId(null);
    navigate("/");
  };

  const handleChangePassword = async () => {
    const oldPassword = window.prompt("请输入当前密码：");
    if (!oldPassword) return;
    const newPassword = window.prompt("请输入新密码（至少 8 位，包含字母和数字）：");
    if (!newPassword) return;
    try {
      await changePassword(oldPassword, newPassword);
      window.alert("密码已修改，请使用新密码重新登录。");
      setUser(null);
    } catch (err) {
      window.alert(extractErrorMessage(err));
    }
  };

  if (!authChecked) {
    return <div className="login-shell"><span className="orb orb-lg spinning" /></div>;
  }
  if (!user) {
    return <LoginPage onLogin={setUser} />;
  }

  const view = route.view === "admin" && user.role !== "admin" ? "chat" : route.view;
  const currentTitle = sessionList.find((s) => s.id === route.sessionId)?.title;

  const title =
    view === "wiki" ? (
      <><Icon name="book" size={15} /> 企业知识库</>
    ) : view === "admin" ? (
      <><Icon name="shield" size={15} /> 系统管理</>
    ) : view === "memory" ? (
      <><Icon name="bulb" size={15} /> 我的记忆</>
    ) : currentTitle ? (
      <><Icon name="history" size={15} /> {currentTitle}</>
    ) : (
      <><Icon name="sparkles" size={15} /> 新对话</>
    );

  return (
    <div className="shell">
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        view={view}
        onNavigate={(path) => { setSidebarOpen(false); navigate(path); }}
        onNewChat={handleNewChat}
        onOpenData={() => setDrawerOpen(true)}
        tables={sourceInfo?.tables}
        targetTable={targetTable}
        onSelectTable={handleSelectTable}
        sessions={sessionList}
        activeSessionId={view === "chat" ? route.sessionId : null}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        sourceInfo={sourceInfo}
        user={user}
        onLogout={handleLogout}
        onChangePassword={handleChangePassword}
      />

      <main className={`main ${view !== "chat" ? "main-wide" : ""}`}>
        <header className="topbar">
          <button type="button" className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="打开导航">
            <Icon name="menu" size={20} />
          </button>
          <div className="topbar-title">{title}</div>
          {view === "chat" && user.role !== "viewer" && (
            <button type="button" className="ghost-btn" onClick={() => setDrawerOpen(true)}>
              <Icon name="database" size={15} /> 数据源
            </button>
          )}
        </header>

        {view === "chat" && (
          <ChatView
            turns={turns}
            loading={loading}
            mode={mode}
            onModeChange={setMode}
            targetTable={targetTable}
            onClearTable={() => setTargetTable(null)}
            onOpenData={() => setDrawerOpen(true)}
            onAsk={ask}
            onOpenPage={(slug) => navigate(`/wiki/${encodeURIComponent(slug)}`)}
            models={models}
            modelId={modelId}
            onModelChange={setModelId}
            skills={skills}
            skill={skill}
            onSkillChange={setSkill}
          />
        )}
        {view === "wiki" && (
          <WikiView
            slug={route.slug}
            user={user}
            onNavigate={(slug) => navigate(slug ? `/wiki/${encodeURIComponent(slug)}` : "/wiki")}
            onAsk={(q) => { setMode("kb"); ask(q, { mode: "kb" }); }}
          />
        )}
        {view === "memory" && <MemoryView />}
        {view === "admin" && <AdminView user={user} />}
      </main>

      {user.role !== "viewer" && (
        <DataDrawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          info={sourceInfo}
          onImportComplete={fetchInfo}
          onQueryTable={handleQueryTable}
          onAnalyzeTable={handleAnalyzeTable}
        />
      )}
    </div>
  );
}

export default App;
