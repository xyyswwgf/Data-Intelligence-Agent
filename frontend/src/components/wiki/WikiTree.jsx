import { useMemo, useState } from "react";
import Icon from "../Icon";

function buildTree(items) {
  const byParent = new Map();
  items.forEach((item) => {
    const key = item.parent_id ?? "root";
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(item);
  });
  const attach = (parentKey) => (byParent.get(parentKey) || []).map((node) => ({ ...node, children: attach(node.id) }));
  return attach("root");
}

function ancestorsOf(items, slug) {
  const byId = new Map(items.map((i) => [i.id, i]));
  const current = items.find((i) => i.slug === slug);
  const ids = new Set();
  let cursor = current && byId.get(current.parent_id);
  while (cursor && !ids.has(cursor.id)) {
    ids.add(cursor.id);
    cursor = byId.get(cursor.parent_id);
  }
  return ids;
}

function Node({ node, depth, activeSlug, expanded, toggle, onOpen }) {
  const hasChildren = node.children.length > 0;
  const open = expanded.has(node.id);
  return (
    <li>
      <div className={`tree-row ${node.slug === activeSlug ? "active" : ""}`} style={{ paddingLeft: 8 + depth * 14 }}>
        <button
          type="button"
          className={`tree-toggle ${hasChildren ? "" : "invisible"} ${open ? "open" : ""}`}
          onClick={() => toggle(node.id)}
          aria-label={open ? "折叠" : "展开"}
          tabIndex={hasChildren ? 0 : -1}
        >
          <Icon name="chevron" size={12} />
        </button>
        <button type="button" className="tree-label" onClick={() => onOpen(node.slug)} title={node.title}>
          <Icon name={hasChildren ? "folder" : "file"} size={14} />
          <span>{node.title}</span>
          {["pending", "indexing"].includes(node.index_status) && <span className="tree-dot" title="索引中" />}
        </button>
      </div>
      {hasChildren && open && (
        <ul>
          {node.children.map((child) => (
            <Node key={child.id} node={child} depth={depth + 1} activeSlug={activeSlug} expanded={expanded} toggle={toggle} onOpen={onOpen} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function WikiTree({ items, activeSlug, onOpen }) {
  const tree = useMemo(() => buildTree(items), [items]);
  const [manual, setManual] = useState({});
  // 当前页面的祖先默认展开，用户手动折叠/展开的状态优先
  const expanded = useMemo(() => {
    const set = ancestorsOf(items, activeSlug);
    items.forEach((i) => {
      if (manual[i.id] === true) set.add(i.id);
      if (manual[i.id] === false) set.delete(i.id);
    });
    return set;
  }, [items, activeSlug, manual]);
  const toggle = (id) => setManual((prev) => ({ ...prev, [id]: !expanded.has(id) }));

  if (!items.length) return <p className="side-empty">还没有页面</p>;
  return (
    <ul className="wiki-tree">
      {tree.map((node) => (
        <Node key={node.id} node={node} depth={0} activeSlug={activeSlug} expanded={expanded} toggle={toggle} onOpen={onOpen} />
      ))}
    </ul>
  );
}
