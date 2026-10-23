import { useCallback, useEffect, useState } from "react";
import Sidebar from "./components/Sidebar";
import DataDrawer from "./components/DataDrawer";
import Icon from "./components/Icon";
import ChatView from "./components/ChatView";
import LoginPage from "./components/LoginPage";
import AdminView from "./components/AdminView";
import WikiView from "./components/wiki/WikiView";
import { detectChartConfig } from "./utils/chartDetector";
import { extractErrorMessage } from "./utils/format";
import {
  queryNL,
  analyzeQuestion,
  analyzeTableQuestion,
  askKnowledgeBase,
  changePassword,
  getAnalysisHistory,
  getAnalysisDetail,
  getMe,
  getToken,
  logout,
  apiClient,
} from "./api";
import "./App.css";

let turnSeq = 0;
const nextTurnId = () => `turn-${Date.now()}-${turnSeq++}`;

// ── 极简 hash 路由：#/ 对话，#/wiki、#/wiki/<slug> 知识库，#/admin 系统管理 ──
function parseHash() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const [section, ...rest] = hash.split("/");
  if (section === "wiki") return { view: "wiki", slug: rest.length ? decodeURIComponent(rest.join("/")) : null };
  if (section === "admin") return { view: "admin" };
  return { view: "chat" };
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

function App() {
  const [user, setUser] = useState(null);
  const [authChecked, setAuthChecked] = useState(() => !getToken());
  const [route, navigate] = useHashRoute();

  const [mode, setMode] = useState("agent");
  const [turns, setTurns] = useState([]);
  const [historyItems, setHistoryItems] = useState([]);
  const [targetTable, setTargetTable] = useState(null);
  const [sourceInfo, setSourceInfo] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const loading = turns.some((t) => t.status === "pending");

  // 已有 token 时恢复登录态；任意接口返回 401 时回到登录页
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

  const fetchHistory = useCallback(() => {
    getAnalysisHistory().then((res) => setHistoryItems(res.items || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (!user) return;
    fetchInfo();
    fetchHistory();
  }, [user, fetchInfo, fetchHistory]);

  const updateTurn = (id, patch) => setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const ask = async (question, options = {}) => {
    if (loading) return;
    const activeMode = options.mode || mode;
    const table = options.table !== undefined ? options.table : targetTable;
    const id = nextTurnId();
    if (route.view !== "chat") navigate("/");
    setTurns((prev) => [...prev, { id, question, mode: activeMode, table: activeMode === "agent" ? table : null, status: "pending" }]);

    try {
      if (activeMode === "agent") {
        const data = table ? await analyzeTableQuestion(table, question) : await analyzeQuestion(question);
        updateTurn(id, { status: "done", result: data });
        fetchHistory();
      } else if (activeMode === "kb") {
        updateTurn(id, { status: "done", result: await askKnowledgeBase(question) });
      } else {
        const data = await queryNL(question);
        updateTurn(id, { status: "done", result: { ...data, chartConfig: detectChartConfig(data.columns, data.data) } });
      }
    } catch (err) {
      updateTurn(id, { status: "error", error: extractErrorMessage(err) });
    }
  };

  const handleNewChat = () => {
    if (loading) return;
    setTurns([]);
    setSidebarOpen(false);
    navigate("/");
  };

  const handleSelectTable = (tableName) => {
    setTargetTable(tableName);
    setMode("agent");
    setSidebarOpen(false);
    navigate("/");
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

  const handleSelectHistory = async (runId) => {
    if (loading) return;
    setSidebarOpen(false);
    navigate("/");
    try {
      const data = await getAnalysisDetail(runId);
      setTargetTable(data.table_name || null);
      setMode("agent");
      setTurns((prev) => [
        ...prev,
        { id: nextTurnId(), question: data.question, mode: "agent", table: data.table_name || null, status: "done", result: data },
      ]);
    } catch (err) {
      setTurns((prev) => [
        ...prev,
        { id: nextTurnId(), question: "查看历史分析", mode: "agent", table: null, status: "error", error: extractErrorMessage(err) },
      ]);
    }
  };

  const handleLogout = async () => {
    await logout().catch(() => {});
    setUser(null);
    setTurns([]);
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

  const activeRunId = [...turns].reverse().find((t) => t.result?.run_id)?.result.run_id;
  const view = route.view === "admin" && user.role !== "admin" ? "chat" : route.view;

  const title =
    view === "wiki" ? (
      <><Icon name="book" size={15} /> 企业知识库</>
    ) : view === "admin" ? (
      <><Icon name="shield" size={15} /> 系统管理</>
    ) : targetTable && mode === "agent" ? (
      <><Icon name="table" size={15} /> {targetTable}</>
    ) : mode === "kb" ? (
      <><Icon name="book" size={15} /> 知识问答</>
    ) : mode === "query" ? (
      <><Icon name="code" size={15} /> 自然语言查询</>
    ) : (
      <><Icon name="sparkles" size={15} /> 经营分析 Agent</>
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
        history={historyItems}
        activeRunId={activeRunId}
        onSelectHistory={handleSelectHistory}
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
