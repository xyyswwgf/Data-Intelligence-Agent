import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "../Icon";
import WikiTree from "./WikiTree";
import WikiHome from "./WikiHome";
import WikiPage from "./WikiPage";
import WikiEditor from "./WikiEditor";
import VersionHistory from "./VersionHistory";
import UploadDialog from "./UploadDialog";
import StoreDataDialog from "./StoreDataDialog";
import {
  createWikiPage,
  deleteWikiPage,
  getWikiPage,
  getWikiTree,
  restoreWikiVersion,
  searchWiki,
  updateWikiPage,
} from "../../api";
import { extractErrorMessage } from "../../utils/format";

/**
 * 知识库 Wiki 工作区
 *   侧边栏（navSlot）：搜索 + 新建 / 上传 / 导入门店数据 + 首页 + 页面目录
 *   主区：索引首页 / 页面阅读 / 编辑 / 新建 / 搜索结果
 */
export default function WikiView({ slug, user, navSlot, onNavigate, onCloseNav, onAsk }) {
  const canEdit = user.role === "editor" || user.role === "admin";
  const canDelete = user.role === "admin";

  const [tree, setTree] = useState([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [page, setPage] = useState(null);
  const [pageError, setPageError] = useState(null);
  const [editingSlug, setEditingSlug] = useState(null);
  const [draft, setDraft] = useState(null); // 新建页面的初始值
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [storeDataFiles, setStoreDataFiles] = useState(null); // 非 null 时打开门店数据导入
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    getWikiTree().then((data) => setTree(data.items)).catch(() => {});
  }, [refreshKey]);

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    getWikiPage(slug)
      .then((data) => {
        if (!cancelled) {
          setPage(data);
          setPageError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setPage(null);
          setPageError(err.response?.status === 404 ? "notfound" : extractErrorMessage(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, refreshKey]);

  const existingSlugs = useMemo(() => new Set(tree.map((p) => p.slug)), [tree]);
  const currentPage = page && page.slug === slug ? page : null;
  const editing = currentPage && editingSlug === slug;

  const open = (target) => {
    setResults(null);
    setDraft(null);
    setSaveError(null);
    onNavigate(target);
  };

  const runSearch = async (e) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setDraft(null);
    onCloseNav();
    try {
      setResults({ q, ...(await searchWiki(q)) });
    } catch (err) {
      setResults({ q, error: extractErrorMessage(err), pages: [] });
    }
  };

  const startCreate = (initial = {}) => {
    onCloseNav();
    setResults(null);
    setSaveError(null);
    setDraft({ title: "", content_md: "", tags: [], parent_id: null, ...initial });
  };

  const handleSave = async (values) => {
    setSaving(true);
    setSaveError(null);
    try {
      if (draft) {
        const created = await createWikiPage(values);
        setDraft(null);
        refresh();
        onNavigate(created.slug);
      } else {
        const parentChanged = values.parent_id !== currentPage.parent_id;
        await updateWikiPage(currentPage.id, {
          base_version: currentPage.version,
          title: values.title,
          content_md: values.content_md,
          tags: values.tags,
          change_note: values.change_note,
          parent_id: values.parent_id,
          move: parentChanged,
        });
        setEditingSlug(null);
        refresh();
      }
    } catch (err) {
      setSaveError(
        err.response?.status === 409
          ? `${extractErrorMessage(err)}。你的修改仍保留在编辑器中，可复制后刷新页面再合并。`
          : extractErrorMessage(err),
      );
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`删除页面「${currentPage.title}」？子页面会移动到上一级，检索索引会立即移除。`)) return;
    try {
      await deleteWikiPage(currentPage.id);
      refresh();
      onNavigate(null);
    } catch (err) {
      setPageError(extractErrorMessage(err));
    }
  };

  const handleRestore = async (version) => {
    if (!window.confirm(`把页面恢复到 v${version}？会生成一个新版本，历史不会丢失。`)) return;
    await restoreWikiVersion(currentPage.id, version);
    setHistoryOpen(false);
    refresh();
  };

  let main;
  if (draft) {
    main = (
      <WikiEditor
        key="new"
        initial={draft}
        pages={tree}
        existingSlugs={existingSlugs}
        saving={saving}
        error={saveError}
        onSave={handleSave}
        onCancel={() => setDraft(null)}
      />
    );
  } else if (results) {
    main = (
      <div className="wiki-search-results">
        <div className="search-head">
          <h2>“{results.q}” 的搜索结果</h2>
          <span className="tag">{results.mode === "hybrid" ? "向量 + 关键词" : "关键词"}检索 · {results.pages.length} 个页面</span>
          <button type="button" className="ghost-btn" onClick={() => onAsk(results.q)}>
            <Icon name="sparkles" size={14} /> 用知识问答回答
          </button>
        </div>
        {results.error && <div className="callout danger"><Icon name="alert" size={16} /><span>{results.error}</span></div>}
        {results.pages.length === 0 && !results.error && <p className="side-empty">没有找到相关页面，换个关键词试试。</p>}
        {results.pages.map((p) => (
          <button type="button" key={p.slug} className="search-hit" onClick={() => open(p.slug)}>
            <strong><Icon name="file" size={14} /> {p.title}</strong>
            {p.sections.map((s, i) => (
              <span key={i}>
                {s.heading_path && <em>{s.heading_path.split(" > ").slice(-1)[0]}：</em>}
                {s.snippet}
              </span>
            ))}
          </button>
        ))}
      </div>
    );
  } else if (!slug) {
    main = <WikiHome onOpen={open} refreshKey={refreshKey} />;
  } else if (pageError === "notfound") {
    main = (
      <div className="empty-state">
        <div className="empty-icon"><Icon name="file" size={28} /></div>
        <p>页面「{slug}」还不存在</p>
        {canEdit && (
          <button type="button" className="btn-primary" onClick={() => startCreate({ title: slug, content_md: `# ${slug}\n\n` })}>
            <Icon name="plus" size={14} /> 创建这个页面
          </button>
        )}
      </div>
    );
  } else if (pageError) {
    main = <div className="callout danger"><Icon name="alert" size={16} /><span>{pageError}</span></div>;
  } else if (!currentPage) {
    main = <div className="wiki-loading"><span className="spinner" /></div>;
  } else if (editing) {
    main = (
      <WikiEditor
        key={`${currentPage.id}-${currentPage.version}`}
        initial={currentPage}
        pages={tree}
        existingSlugs={existingSlugs}
        saving={saving}
        error={saveError}
        onSave={handleSave}
        onCancel={() => { setEditingSlug(null); setSaveError(null); }}
      />
    );
  } else {
    main = (
      <WikiPage
        page={currentPage}
        existingSlugs={existingSlugs}
        canEdit={canEdit}
        canDelete={canDelete}
        onOpen={open}
        onEdit={() => { setSaveError(null); setEditingSlug(slug); }}
        onHistory={() => setHistoryOpen(true)}
        onDelete={handleDelete}
        onCreateChild={() => startCreate({ parent_id: currentPage.id })}
        onCreateMissing={(target) => startCreate({ title: target, content_md: `# ${target}\n\n`, parent_id: currentPage.parent_id })}
      />
    );
  }

  const nav = (
    <div className="wiki-sidenav">
      <form className="wiki-search" onSubmit={runSearch} role="search">
        <Icon name="search" size={15} />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索知识库…" aria-label="搜索知识库" />
      </form>
      {canEdit && (
        <>
          <button type="button" className="new-chat" onClick={() => startCreate()}>
            <Icon name="plus" size={16} /> 新建页面
          </button>
          <div className="wiki-nav-actions">
            <button type="button" className="ghost-btn" onClick={() => { onCloseNav(); setUploadOpen(true); }}><Icon name="upload" size={14} /> 上传文档</button>
            <button type="button" className="ghost-btn" onClick={() => { onCloseNav(); setStoreDataFiles([]); }}><Icon name="database" size={14} /> 导入门店数据</button>
          </div>
        </>
      )}
      <nav className="side-section">
        <button type="button" className={`side-item ${!slug && !draft && !results ? "active" : ""}`} onClick={() => open(null)}>
          <Icon name="home" size={16} /><span className="side-text">知识库首页</span>
        </button>
      </nav>
      <nav className="side-section grow">
        <div className="side-label">页面目录</div>
        <div className="side-scroll">
          <WikiTree items={tree} activeSlug={slug} onOpen={open} />
        </div>
      </nav>
    </div>
  );

  return (
    <div className="wiki-shell">
      {navSlot && createPortal(nav, navSlot)}
      <section className="wiki-main">{main}</section>

      {historyOpen && currentPage && (
        <VersionHistory page={currentPage} canRestore={canEdit} onRestore={handleRestore} onClose={() => setHistoryOpen(false)} />
      )}
      {uploadOpen && (
        <UploadDialog
          pages={tree}
          defaultParentId={currentPage?.id}
          onClose={() => setUploadOpen(false)}
          onDone={refresh}
          onOpen={(target) => { setUploadOpen(false); open(target); }}
          onStoreData={(files) => { setUploadOpen(false); setStoreDataFiles(files); }}
        />
      )}
      {storeDataFiles && (
        <StoreDataDialog
          initialFiles={storeDataFiles}
          onClose={() => setStoreDataFiles(null)}
          onDone={refresh}
          onOpen={(target) => { setStoreDataFiles(null); open(target); }}
        />
      )}
    </div>
  );
}
