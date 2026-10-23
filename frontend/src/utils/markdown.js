import { Marked } from "marked";
import DOMPurify from "dompurify";

/** 与后端 wiki.slugify 保持一致：[[标题]] 与 [[slug]] 都能链接到同一页面 */
export function slugify(title) {
  const slug = String(title)
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_一-鿿-]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-")
    .slice(0, 180);
  return slug || "page";
}

function headingId(text, used) {
  let base = slugify(text.replace(/<[^>]+>/g, "")) || "section";
  let id = base;
  let n = 2;
  while (used.has(id)) id = `${base}-${n++}`;
  used.add(id);
  return id;
}

/**
 * 渲染 Wiki Markdown：
 * - [[目标]] / [[目标|文字]] → 站内链接（不存在的页面标红）
 * - 标题带锚点 id，供目录跳转
 * - 输出经 DOMPurify 清洗，防止文档中的脚本注入
 */
export function renderMarkdown(markdown, { existingSlugs } = {}) {
  const used = new Set();
  const toc = [];
  const marked = new Marked({ gfm: true, breaks: false });
  marked.use({
    extensions: [
      {
        name: "wikilink",
        level: "inline",
        start: (src) => src.indexOf("[["),
        tokenizer(src) {
          const match = /^\[\[([^[\]|#]+?)(?:\|([^[\]]*))?\]\]/.exec(src);
          if (match) {
            return { type: "wikilink", raw: match[0], target: match[1].trim(), label: (match[2] || match[1]).trim() };
          }
          return undefined;
        },
        renderer(token) {
          const slug = slugify(token.target);
          const missing = existingSlugs && !existingSlugs.has(slug);
          const label = token.label.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
          return `<a href="#/wiki/${encodeURIComponent(slug)}" data-wiki="${slug}" class="wikilink${missing ? " missing" : ""}">${label}</a>`;
        },
      },
    ],
    renderer: {
      heading({ tokens, depth }) {
        const html = this.parser.parseInline(tokens);
        const id = headingId(html, used);
        if (depth >= 2 && depth <= 4) toc.push({ id, depth, text: html.replace(/<[^>]+>/g, "") });
        return `<h${depth} id="${id}">${html}</h${depth}>`;
      },
      link({ href, title, tokens }) {
        const text = this.parser.parseInline(tokens);
        const external = /^https?:\/\//i.test(href || "");
        const titleAttr = title ? ` title="${title}"` : "";
        return `<a href="${href}"${titleAttr}${external ? ' target="_blank" rel="noopener noreferrer"' : ""}>${text}</a>`;
      },
    },
  });
  const raw = marked.parse(markdown || "");
  const html = DOMPurify.sanitize(raw, { ADD_ATTR: ["target", "data-wiki"] });
  return { html, toc };
}
