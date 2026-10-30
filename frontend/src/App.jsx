import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "./components/Sidebar";
import DataDrawer from "./components/DataDrawer";
import Icon from "./components/Icon";
import ChatView from "./components/ChatView";
import LoginPage from "./components/LoginPage";
import SettingsView from "./components/settings/SettingsView";
import DiscoverView from "./components/discover/DiscoverView";
import { DISCOVER_SECTIONS, LEGACY_ADMIN_SECTIONS, MODE_OPTIONS, SETTINGS_SECTIONS } from "./navigation";
import WikiView from "./components/wiki/WikiView";
import { detectChartConfig } from "./utils/chartDetector";
import { extractErrorMessage } from "./utils/format";
import {
  createChatSession,
  deleteChatSession,
  getChatSession,
  getMe,
  getModelOptions,
  getMyAccount,
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

// ── 极简 hash 路由 ──
//   #/ 新对话，#/chat/<id> 会话，#/wiki[/<slug>] 知识库，#/discover[/<分类>] 发现，#/settings[/<分区>] 设置
//   旧的 #/admin/<分区> 转到对应的设置分区
function parseHash() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const [section, ...rest] = hash.split("/");
  if (section === "wiki") return { view: "wiki", slug: rest.length ? decodeURIComponent(rest.join("/")) : null };
  if (section === "discover") return { view: "discover", section: rest[0] || DISCOVER_SECTIONS[0].id };
  if (section === "settings") return { view: "settings", section: rest[0] || SETTINGS_SECTIONS[0].id };
  if (section === "admin") return { view: "settings", section: LEGACY_ADMIN_SECTIONS[rest[0]] || "members" };
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

// 只用于记住个人偏好；隐私模式下读写失败不影响使用
function readPref(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function writePref(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, String(value));
  } catch {
    // 忽略
  }
}

function App() {
  const [user, setUser] = useState(null);
  const [authChecked, setAuthChecked] = useState(() => !getToken());
  const [loginNotice, setLoginNotice] = useState(null);
  const [route, navigate] = useHashRoute();

  const [defaultMode, setDefaultModeState] = useState(() => {
    const saved = readPref("datamind.mode", "assistant");
    return MODE_OPTIONS.some((m) => m.id === saved) ? saved : "assistant";
  });
  const [mode, setMode] = useState(defaultMode);
  const [account, setAccount] = useState(null); // 当前方案、额度与余额（用户菜单和设置共用）
  const lastAppPath = useRef("/"); // 从设置「返回应用」时回到的位置
  const [slot, setSlot] = useState(null); // 侧边栏里留给知识库 / 系统管理导航的位置
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
    const saved = readPref("datamind.model", null);
    return saved ? Number(saved) : null;
  });
  const setModelId = (id) => {
    setModelIdState(id);
    writePref("datamind.model", id);
  };
  const setDefaultMode = (value) => {
    setDefaultModeState(value);
    setMode(value);
    writePref("datamind.mode", value);
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

  // ⌘, / Ctrl+, 打开设置
  useEffect(() => {
    if (!user) return undefined;
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        window.location.hash = "/settings/general";
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [user]);

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
  }, [user, fetchInfo, fetchSessions, route.view]);

  // 可选模型和技能受「发现」中的启用状态影响，切换模块和修改启用状态后重新获取
  const fetchOptions = useCallback(() => {
    getModelOptions().then((d) => setModels(d.items || [])).catch(() => setModels([]));
    getSkillOptions().then((d) => setSkills(d.items || [])).catch(() => setSkills([]));
  }, []);

  const fetchAccount = useCallback(() => {
    getMyAccount().then(setAccount).catch(() => setAccount(null));
  }, []);

  useEffect(() => {
    if (user) fetchOptions();
  }, [user, fetchOptions, route.view]);

  // 额度随每次提问变化：任何页面切换都刷新一次（请求很轻）
  useEffect(() => {
    if (user) fetchAccount();
  }, [user, fetchAccount, route]);

  // 记住最后所在的应用页面，设置页的「返回应用」回到这里
  useEffect(() => {
    if (route.view !== "settings") lastAppPath.current = window.location.hash.replace(/^#/, "") || "/";
  }, [route]);

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
            updateTurn(id, { status: "done", content: ev.content, citations: ev.citations, usage: ev.usage, draft: null });
          } else if (ev.error) {
            updateTurn(id, { status: "error", error: ev.error });
          } else if (activeMode === "query") {
            updateTurn(id, { status: "done", result: { ...ev.result, chartConfig: detectChartConfig(ev.result.columns, ev.result.data) } });
          } else {
            updateTurn(id, { status: "done", result: ev.result });
          }
        } else if (ev.type === "delta") {
          // 正在生成的文字：按步骤累积成草稿，不进入推理轨迹
          updateTurn(id, (t) => {
            const prev = t.draft && t.draft.step === ev.step ? t.draft : { step: ev.step, answer: "", reasoning: "" };
            return { draft: { ...prev, [ev.channel]: prev[ev.channel] + ev.text } };
          });
        } else {
          // 这一步有了定论（转为思考记录 / 调用工具），草稿作废
          updateTurn(id, (t) => ({ events: [...(t.events || []), ev], draft: null }));
        }
      });
      // 流结束但没有收到 done（例如网络中断）
      updateTurn(id, (t) => (t.status === "pending" ? { status: "error", error: "连接中断，请重试。" } : {}));
    } catch (err) {
      updateTurn(id, { status: "error", error: err.message || extractErrorMessage(err) });
    } finally {
      streamingSessionRef.current = null;
      fetchSessions();
      fetchAccount();
    }
  };

  const handleNewChat = () => {
    if (loading) return;
    setSidebarOpen(false);
    setMode(defaultMode);
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

  const handlePasswordChanged = () => {
    setLoginNotice("密码已修改，请使用新密码重新登录。");
    setUser(null);
    setTurns([]);
    setLoadedSessionId(null);
    navigate("/");
  };

  const handleLogin = (u) => {
    setLoginNotice(null);
    setUser(u);
  };

  if (!authChecked) {
    return <div className="login-shell"><span className="orb orb-lg spinning" /></div>;
  }
  if (!user) {
    return <LoginPage onLogin={handleLogin} notice={loginNotice} />;
  }

  const view = route.view;
  const settingsSection = SETTINGS_SECTIONS.find((x) => x.id === route.section && (!x.admin || user.role === "admin"))
    || SETTINGS_SECTIONS[0];
  const discoverSection = DISCOVER_SECTIONS.find((x) => x.id === route.section) || DISCOVER_SECTIONS[0];
  const currentTitle = sessionList.find((s) => s.id === route.sessionId)?.title;

  const title =
    view === "wiki" ? (
      <><Icon name="book" size={15} /> 知识库</>
    ) : view === "discover" ? (
      <><Icon name="compass" size={15} /> 发现 {discoverSection.id !== "all" && <><span className="crumb-sep">/</span> {discoverSection.label}</>}</>
    ) : view === "settings" ? (
      <><Icon name="settings" size={15} /> 设置 <span className="crumb-sep">/</span> {settingsSection.label}</>
    ) : currentTitle ? (
      <><Icon name="history" size={15} /> {currentTitle}</>
    ) : (
      <><Icon name="sparkles" size={15} /> 新对话</>
    );

  const go = (path) => {
    setSidebarOpen(false);
    navigate(path);
  };

  return (
    <div className="shell">
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        module={view}
        onNavigate={go}
        onSlot={setSlot}
        chat={{
          onNewChat: handleNewChat,
          onOpenData: () => { setSidebarOpen(false); setDrawerOpen(true); },
          tables: sourceInfo?.tables,
          targetTable,
          onSelectTable: handleSelectTable,
          sessions: sessionList,
          activeSessionId: view === "chat" ? route.sessionId : null,
          onSelectSession: handleSelectSession,
          onDeleteSession: handleDeleteSession,
        }}
        user={user}
        online={Boolean(sourceInfo)}
        account={account}
        onOpenSettings={(id) => go(`/settings/${id}`)}
        onBack={() => go(lastAppPath.current)}
        onLogout={handleLogout}
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
            pack={sourceInfo?.pack}
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
            pack={sourceInfo?.pack}
            navSlot={slot}
            onNavigate={(slug) => go(slug ? `/wiki/${encodeURIComponent(slug)}` : "/wiki")}
            onCloseNav={() => setSidebarOpen(false)}
            onAsk={(q) => { setMode("kb"); ask(q, { mode: "kb" }); }}
          />
        )}
        {view === "discover" && (
          <DiscoverView section={discoverSection.id} navSlot={slot} user={user}
            onNavigate={(id) => go(id === "all" ? "/discover" : `/discover/${id}`)} onChanged={fetchOptions} />
        )}
        {view === "settings" && (
          <SettingsView
            section={settingsSection.id}
            navSlot={slot}
            onNavigate={(id) => go(`/settings/${id}`)}
            user={user}
            account={account}
            sourceInfo={sourceInfo}
            models={models}
            modelId={modelId}
            onModelChange={setModelId}
            defaultMode={defaultMode}
            onDefaultModeChange={setDefaultMode}
            onPasswordChanged={handlePasswordChanged}
            onChatsDeleted={() => { setTurns([]); setLoadedSessionId(null); fetchSessions(); }}
            onPackChanged={() => { fetchInfo(); setMode(defaultMode); }}
          />
        )}
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
