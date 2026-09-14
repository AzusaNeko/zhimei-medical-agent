// 设计文档构建器：把 Workflow 目录下的 .md 转成统一风格的 HTML 文档页
//
// 用法：node build-docs.cjs
// 输出：arch-pseudocode.html / arch-supervisor.html / config-rules.html /
//       data-model.html / ops-console.html
//
// 说明：零依赖。只支持本项目文档用到的 Markdown 子集：
//   标题(#~####)、段落、有序/无序列表(含一层嵌套与续行)、表格、围栏代码块、
//   引用块(内部可含列表/表格)、分隔线、行内 code / **粗体** / 链接。
//   —— 改动 .md 后重新执行本脚本即可，不要手改生成的 HTML。

const fs = require('fs');
const path = require('path');

const DIR = __dirname;

const PAGES = [
  { file: 'langgraph-v1.html',    label: '架构总览' },
  { file: 'arch-pseudocode.html', label: '伪代码讲解', md: 'langgraph-pseudocode.md' },
  { file: 'arch-supervisor.html', label: '总控与意图', md: 'supervisor-intake.md' },
  { file: 'config-rules.html',    label: '规则与参数', md: 'mvp-config-rules.md' },
  { file: 'data-model.html',      label: '数据模型',   md: 'data-model.md' },
  { file: 'ops-console.html',     label: '运营后台',   md: 'ops-console.md' },
];

/* ══════════════ 行内处理 ══════════════ */
function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function inline(text) {
  let t = esc(text);
  const codes = [];
  t = t.replace(/`([^`]+)`/g, (m, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>');
  t = t.replace(/\u0000(\d+)\u0000/g, (m, i) => '<code>' + codes[Number(i)] + '</code>');
  return t;
}

function slugOf(raw) {
  return raw
    .replace(/`/g, '').replace(/\*\*/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .trim()
    .replace(/\s/g, '-');          // 每个空白 → 一个连字符（与 GitHub 一致，不合并连续空白）
}

/* ══════════════ 列表 ══════════════ */
function renderListNode(node) {
  const tag = node.ordered ? 'ol' : 'ul';
  const items = node.items.map((it) =>
    '<li>' + it.text.map(inline).join('<br>') + it.children.map(renderListNode).join('') + '</li>'
  ).join('');
  return '<' + tag + '>' + items + '</' + tag + '>';
}

function renderListBlock(lines, start) {
  const root = { ordered: null, items: [] };
  const stack = [{ indent: -1, lastItemIndent: -1, node: root }];
  let i = start;
  while (i < lines.length) {
    const l = lines[i];
    if (l.trim() === '') {
      if (i + 1 < lines.length && /^(\s*)([-*]|\d+\.)\s+/.test(lines[i + 1])) { i++; continue; }
      break;
    }
    const m = l.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
    if (m) {
      const indent = m[1].length;
      const ordered = /\d+\./.test(m[2]);
      while (stack.length > 1 && indent < stack[stack.length - 1].indent) stack.pop();
      let top = stack[stack.length - 1];
      if (top.node.items.length && indent > top.lastItemIndent) {
        const child = { ordered, items: [] };
        top.node.items[top.node.items.length - 1].children.push(child);
        stack.push({ indent, lastItemIndent: -1, node: child });
        top = stack[stack.length - 1];
      } else if (top.node.ordered === null) {
        top.node.ordered = ordered;
      }
      top.node.items.push({ text: [m[3]], children: [] });
      top.lastItemIndent = indent;
      i++;
      continue;
    }
    if (/^\s+\S/.test(l) && stack[stack.length - 1].node.items.length) {
      const cur = stack[stack.length - 1].node.items.slice(-1)[0];
      cur.text.push(l.trim());
      i++;
      continue;
    }
    break;
  }
  return { html: renderListNode(root), next: i };
}

/* ══════════════ 表格 ══════════════ */
function isTableSep(l) { return /^\|[\s:\-|]+\|$/.test(l.trim()); }
function splitRow(l) {
  let s = l.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((c) => c.trim());
}

/* ══════════════ 主渲染 ══════════════ */
function render(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out = [];
  const toc = [];
  const seen = {};
  let title = null;
  let i = 0;

  const isBlockStart = (l) => {
    const t = l.trim();
    return t === '' || /^#{1,4}\s+/.test(l) || /^```/.test(l) ||
      t.startsWith('>') || /^(\s*)([-*]|\d+\.)\s+/.test(l) || /^(-{3,}|\*{3,})$/.test(t);
  };

  while (i < lines.length) {
    const line = lines[i];
    const t = line.trim();
    if (t === '') { i++; continue; }

    // 围栏代码块
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      const lang = fence[1] || '';
      const buf = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push('<pre><code' + (lang ? ' class="lang-' + lang + '"' : '') + '>' +
        esc(buf.join('\n')) + '</code></pre>');
      continue;
    }

    // 标题
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      const raw = h[2].trim();
      let id = slugOf(raw);
      if (seen[id] != null) { seen[id]++; id = id + '-' + seen[id]; } else { seen[id] = 0; }
      if (level === 1 && title === null) title = raw;
      if (level === 2 || level === 3) toc.push({ level, id, text: raw });
      out.push('<h' + level + ' id="' + id + '">' + inline(raw) + '</h' + level + '>');
      i++;
      continue;
    }

    // 分隔线
    if (/^(-{3,}|\*{3,})$/.test(t)) { out.push('<hr>'); i++; continue; }

    // 表格
    if (t.startsWith('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = splitRow(t);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(splitRow(lines[i])); i++; }
      out.push('<div class="tw"><table><thead><tr>' +
        head.map((c) => '<th>' + inline(c) + '</th>').join('') +
        '</tr></thead><tbody>' +
        rows.map((r) => '<tr>' + r.map((c) => '<td>' + inline(c) + '</td>').join('') + '</tr>').join('') +
        '</tbody></table></div>');
      continue;
    }

    // 兜底：以 | 开头但不是表格
    if (t.startsWith('|')) { out.push('<p>' + inline(t) + '</p>'); i++; continue; }

    // 引用块（内部递归渲染，允许包含列表与表格）
    if (t.startsWith('>')) {
      const buf = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].replace(/^\s*>\s?/, ''));
        i++;
      }
      out.push('<blockquote>' + render(buf.join('\n')).html + '</blockquote>');
      continue;
    }

    // 列表
    if (/^(\s*)([-*]|\d+\.)\s+/.test(line)) {
      const r = renderListBlock(lines, i);
      out.push(r.html);
      i = r.next;
      continue;
    }

    // 段落
    const buf = [];
    while (i < lines.length && !isBlockStart(lines[i]) && !lines[i].trim().startsWith('|')) {
      buf.push(lines[i].trim());
      i++;
    }
    if (buf.length) out.push('<p>' + buf.map(inline).join('<br>') + '</p>');
    else i++;
  }

  return { html: out.join('\n'), toc, title };
}

/* ══════════════ 页面模板 ══════════════ */
const CSS = `
:root{--ink:#19344a;--muted:#586d7b;--line:#d7e1e7;--accent:#226b62;--bg:#f3f6f8;--code:#f7f9fb}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:84px}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 "Microsoft YaHei","PingFang SC",sans-serif}
a{color:#1f6f8b}
.topbar{position:sticky;top:0;z-index:20;display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;justify-content:space-between;padding:12px 28px;background:#fff;border-bottom:1px solid var(--line)}
.brand{display:flex;flex-direction:column;line-height:1.3}
.brand .eyebrow{font:11px/1.4 Consolas,monospace;color:var(--accent);letter-spacing:1.5px}
.brand b{font-size:15px}
.topbar nav{display:flex;flex-wrap:wrap;gap:6px}
.topbar nav a{border:1px solid var(--line);border-radius:6px;padding:5px 11px;font-size:13px;text-decoration:none;color:var(--ink);background:#fff}
.topbar nav a:hover{background:#e8f2f0;border-color:#75a69a}
.topbar nav a.active{background:var(--accent);border-color:var(--accent);color:#fff}
.layout{display:grid;grid-template-columns:272px minmax(0,1fr);gap:26px;max-width:1560px;margin:0 auto;padding:26px 28px 60px}
.toc{position:sticky;top:74px;align-self:start;max-height:calc(100vh - 96px);overflow:auto;background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 14px}
.toc b{display:block;font-size:12px;color:var(--muted);letter-spacing:1px;margin-bottom:8px}
.toc ul{list-style:none;margin:0;padding:0}
.toc li{margin:3px 0;font-size:13px;line-height:1.5}
.toc li.l3{padding-left:14px;font-size:12.5px}
.toc a{color:var(--muted);text-decoration:none;display:block;padding:2px 0}
.toc a:hover{color:var(--accent)}
.doc{background:#fff;border:1px solid var(--line);border-radius:12px;padding:30px 38px 44px;min-width:0}
.doc h1{font-size:29px;letter-spacing:-.5px;margin:0 0 10px;line-height:1.35}
.doc h2{font-size:22px;margin:36px 0 10px;padding-top:14px;border-top:1px solid var(--line)}
.doc h2:first-of-type{border-top:0;padding-top:0}
.doc h3{font-size:17px;margin:26px 0 8px}
.doc h4{font-size:15px;margin:20px 0 6px;color:#2c4a5e}
.doc p{margin:9px 0}
.doc ul,.doc ol{margin:9px 0;padding-left:24px}
.doc li{margin:4px 0}
.doc code{font-family:Consolas,"Courier New",monospace;font-size:12.5px;background:#eef2f5;padding:1px 5px;border-radius:4px;color:#2c4f6b}
.doc pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:16px 18px;overflow:auto;margin:12px 0}
.doc pre code{background:none;padding:0;font-size:12.5px;line-height:1.7;color:#243b4a;white-space:pre}
.doc blockquote{margin:12px 0;padding:12px 16px;background:#e8f2f0;border-left:3px solid var(--accent);border-radius:0 8px 8px 0;color:#315a58}
.doc blockquote p{margin:5px 0}
.doc blockquote ol,.doc blockquote ul{padding-left:20px}
.doc hr{border:0;border-top:1px solid var(--line);margin:26px 0}
.tw{overflow:auto;border:1px solid var(--line);border-radius:10px;margin:14px 0}
.doc table{width:100%;border-collapse:collapse;font-size:13px}
.doc th,.doc td{border:1px solid var(--line);padding:8px 11px;text-align:left;vertical-align:top}
.doc th{background:#f3f6f8;font-size:12px;color:var(--muted);font-weight:600;white-space:nowrap}
.doc td code{font-size:12px;white-space:normal;word-break:break-word}
footer{max-width:1560px;margin:0 auto;padding:0 28px 44px;color:var(--muted);font-size:12px}
@media(max-width:1080px){.layout{grid-template-columns:1fr}.toc{position:static;max-height:none}.doc{padding:22px 20px 32px}}
@media print{.topbar,.toc{display:none}.layout{display:block;padding:0}.doc{border:0;padding:0}}
`;

function navHtml(current) {
  return PAGES.map((p) =>
    '<a href="' + p.file + '"' + (p.file === current ? ' class="active"' : '') + '>' + p.label + '</a>'
  ).join('');
}

function tocHtml(toc) {
  if (!toc.length) return '<b>本页目录</b><ul><li>（无）</li></ul>';
  return '<b>本页目录</b><ul>' + toc.map((x) =>
    '<li class="l' + x.level + '"><a href="#' + x.id + '">' + inline(x.text) + '</a></li>'
  ).join('') + '</ul>';
}

function buildPage(file, title, body, toc) {
  return '<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n' +
    '<title>' + title + ' · 智美医美顾问设计文档</title>\n<style>' + CSS + '</style>\n</head>\n<body>\n' +
    '<header class="topbar"><div class="brand"><span class="eyebrow">ZHIMEI / DESIGN DOCS</span>' +
    '<b>智美医美顾问 · 设计文档</b></div><nav>' + navHtml(file) + '</nav></header>\n' +
    '<div class="layout"><aside class="toc">' + tocHtml(toc) + '</aside>' +
    '<main class="doc">' + body + '</main></div>\n' +
    '<footer>由 <code>build-docs.cjs</code> 从 Markdown 生成 —— 请修改源文件后重新执行，不要手改本页。' +
    '图与源码自检：<code>node check-mermaid.cjs</code>。</footer>\n</body>\n</html>\n';
}

/* ══════════════ 执行 ══════════════ */
let built = 0;
for (const p of PAGES) {
  if (!p.md) continue;
  const src = path.join(DIR, p.md);
  if (!fs.existsSync(src)) { console.log('跳过（源文件不存在）: ' + p.md); continue; }
  const md = fs.readFileSync(src, 'utf8');
  const r = render(md);
  const html = buildPage(p.file, r.title || p.label, r.html, r.toc);
  fs.writeFileSync(path.join(DIR, p.file), html, 'utf8');
  const tables = (r.html.match(/<table>/g) || []).length;
  const pre = (r.html.match(/<pre>/g) || []).length;
  console.log(p.md + '  →  ' + p.file +
    '   [' + Math.round(html.length / 1024) + ' KB · h2/h3 ' + r.toc.length +
    ' · 表格 ' + tables + ' · 代码块 ' + pre + ']');
  built++;
}
console.log('\n完成：生成 ' + built + ' 个页面。');
