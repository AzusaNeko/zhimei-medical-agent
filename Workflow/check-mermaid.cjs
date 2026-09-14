// 智美医美顾问 · Mermaid 图表自检工具（无需浏览器）
//
// 用法（在本目录下执行）：
//   node check-mermaid.cjs                          # 检查当前目录全部 .mmd
//   node check-mermaid.cjs langgraph-main.mmd       # 只检查指定文件
//
// 做两件事：
//   1. 用同目录 mermaid.min.js 做真实语法解析（DOM 使用桩实现），语法错误会定位到行号；
//   2. 输出结构摘要：节点数、子图数、边数、悬空引用、关键节点出口。
//
// 注意：本工具只校验语法与引用完整性，不做视觉渲染。

const fs = require('fs');
const path = require('path');

/* ---------------- 极简 DOM 桩，供 mermaid 在 Node 中加载 ---------------- */
function makeEl() {
  return {
    nodeType: 1, nodeName: 'DIV', tagName: 'DIV', localName: 'div', namespaceURI: null,
    style: {}, dataset: {}, classList: { add() {}, remove() {}, contains: () => false },
    setAttribute() {}, getAttribute: () => null, removeAttribute() {}, hasAttribute: () => false,
    setAttributeNS() {}, getAttributeNS: () => null,
    appendChild(c) { return c; }, removeChild() {}, remove() {}, insertBefore() {},
    querySelector: () => null, querySelectorAll: () => [],
    getElementsByTagName: () => [], getElementsByClassName: () => [],
    addEventListener() {}, removeEventListener() {}, removeAllRanges() {},
    getBoundingClientRect: () => ({ x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 }),
    innerHTML: '', outerHTML: '', textContent: '', innerText: '',
    children: [], childNodes: [], attributes: [], firstChild: null, lastChild: null,
    parentNode: null, ownerDocument: null, nodeValue: null,
    cloneNode() { return makeEl(); }, contains: () => false, focus() {},
    createNodeIterator: () => ({ nextNode: () => null }),
  };
}
function makeDoc() {
  const doc = {
    nodeType: 9, compatMode: 'CSS1Compat', documentMode: undefined, doctype: null,
    createElement: () => makeEl(), createElementNS: () => makeEl(),
    createTextNode: (t) => ({ nodeType: 3, textContent: t, nodeValue: t }),
    createComment: () => ({ nodeType: 8, textContent: '' }),
    createDocumentFragment: () => makeEl(),
    createNodeIterator: () => ({ nextNode: () => null, currentNode: null }),
    createTreeWalker: () => ({ nextNode: () => null }),
    querySelector: () => null, querySelectorAll: () => [],
    getElementById: () => null, getElementsByTagName: () => [],
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true,
    importNode: (n) => n, contains: () => false,
    body: makeEl(), head: makeEl(), documentElement: makeEl(),
    implementation: { createHTMLDocument: () => makeDoc(), createDocument: () => makeDoc() },
  };
  doc.body.ownerDocument = doc;
  return doc;
}
class NodeStub {}
NodeStub.ELEMENT_NODE = 1; NodeStub.ATTRIBUTE_NODE = 2; NodeStub.TEXT_NODE = 3;
NodeStub.CDATA_SECTION_NODE = 4; NodeStub.ENTITY_REFERENCE_NODE = 5; NodeStub.ENTITY_NODE = 6;
NodeStub.PROCESSING_INSTRUCTION_NODE = 7; NodeStub.COMMENT_NODE = 8; NodeStub.DOCUMENT_NODE = 9;
NodeStub.DOCUMENT_TYPE_NODE = 10; NodeStub.DOCUMENT_FRAGMENT_NODE = 11; NodeStub.NOTATION_NODE = 12;

global.document = makeDoc();
global.window = global;
global.Node = NodeStub;
global.NodeFilter = {
  SHOW_ALL: 0xFFFFFFFF, SHOW_ELEMENT: 1, SHOW_ATTRIBUTE: 2, SHOW_TEXT: 4, SHOW_CDATA_SECTION: 8,
  SHOW_ENTITY_REFERENCE: 16, SHOW_ENTITY: 32, SHOW_PROCESSING_INSTRUCTION: 64, SHOW_COMMENT: 128,
  SHOW_DOCUMENT: 256, SHOW_DOCUMENT_TYPE: 512, SHOW_DOCUMENT_FRAGMENT: 1024, SHOW_NOTATION: 2048,
  FILTER_ACCEPT: 1, FILTER_REJECT: 2, FILTER_SKIP: 3,
};
for (const name of ['Element', 'HTMLElement', 'HTMLTemplateElement', 'HTMLFormElement', 'DocumentFragment',
  'NamedNodeMap', 'SVGElement', 'Text', 'Comment', 'Attr', 'Document', 'CharacterData',
  'ProcessingInstruction', 'HTMLCollection', 'NodeList', 'Range', 'Window', 'XMLDocument']) {
  global[name] = class {};
}
global.DOMParser = class { parseFromString() { return global.document; } };
global.XMLSerializer = class { serializeToString() { return ''; } };
global.trustedTypes = undefined;
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.dispatchEvent = () => true;
global.CustomEvent = class { constructor(t, o) { this.type = t; Object.assign(this, o || {}); } };
global.Event = global.CustomEvent;
global.navigator = { userAgent: 'node', language: 'zh-CN' };
global.location = { href: 'http://localhost/', protocol: 'http:' };
global.getComputedStyle = () => ({ getPropertyValue: () => '' });
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);
global.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
global.MutationObserver = class { observe() {} disconnect() {} };
global.ResizeObserver = class { observe() {} disconnect() {} };

/* ---------------- 结构摘要 ---------------- */
function summarize(file) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  const defs = new Set(), subs = new Set(), edges = [];
  lines.forEach((raw, i) => {
    const l = raw.trim();
    if (l === '' || l.startsWith('%%') || l === 'end') return;
    const sg = l.match(/^subgraph\s+([A-Za-z_]\w*)/);
    if (sg) { subs.add(sg[1]); return; }
    // 节点定义可以出现在行内任意位置：A["x"] --> B["y"]
    const defRe = /\b([A-Za-z_]\w*)\s*[\[\{\(]/g;
    let dm;
    while ((dm = defRe.exec(l)) !== null) defs.add(dm[1]);
    // 把行内定义形态与边标签内容抹平，便于识别边
    const flat = l
      .replace(/\|[^|]*\|/g, '|L|')
      .replace(/([A-Za-z_]\w*)\s*\[[^\]]*\]/g, '$1')
      .replace(/([A-Za-z_]\w*)\s*\([^()]*\)/g, '$1')
      .replace(/([A-Za-z_]\w*)\s*\{[^{}]*\}/g, '$1');
    const m = flat.match(/^([A-Za-z_]\w*)\s*(-->|-\.->|==>)\s*(?:\|L\|\s*)?([A-Za-z_]\w*)/);
    if (m) edges.push({ from: m[1], to: m[3], line: i + 1, label: (l.match(/\|([^|]*)\|/) || [])[1] || '' });
  });
  const known = new Set([...defs, ...subs]);
  const dangling = edges.filter((e) => !known.has(e.from) || !known.has(e.to));
  const quoted = lines.filter((l) => /\|"/.test(l));
  console.log('  节点 ' + defs.size + ' · 子图 ' + subs.size + ' · 边 ' + edges.length);
  console.log('  悬空引用: ' + (dangling.length ? dangling.map((e) => 'L' + e.line + ' ' + e.from + '->' + e.to).join(', ') : '无'));
  if (quoted.length) console.log('  ⚠ ' + quoted.length + ' 行的边标签内出现引号（mermaid 不支持，会导致解析失败）');
  return edges;
}

/* ---------------- 主流程 ---------------- */
(async () => {
  const dir = __dirname;
  let files = process.argv.slice(2);
  if (files.length === 0) files = fs.readdirSync(dir).filter((f) => f.endsWith('.mmd'));
  let mermaid = null;
  try {
    const m = require(path.join(dir, 'mermaid.min.js'));
    mermaid = m.default || m;
    mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });
  } catch (e) {
    console.log('无法加载 mermaid.min.js（跳过语法解析）: ' + String(e.message).split('\n')[0]);
  }
  let failed = 0;
  for (const f of files) {
    console.log('=== ' + f);
    if (mermaid) {
      try {
        await mermaid.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
        console.log('  语法: OK');
      } catch (e) {
        failed++;
        console.log('  语法: FAIL  ' + String(e && e.message).split('\n').slice(0, 3).join(' | '));
      }
    }
    const edges = summarize(path.join(dir, f));
    const gate = edges.filter((e) => e.from === 'risk_gate');
    if (gate.length) console.log('  risk_gate 出口: ' + gate.map((e) => e.label).join(' / '));
    console.log('');
  }
  process.exit(failed ? 1 : 0);
})();
