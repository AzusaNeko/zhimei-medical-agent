/**
 * 前端一致性检查：不启动服务器、不需要浏览器，纯静态校验。
 *
 *     node scripts/check_ui.cjs          （在 Code 目录下执行）
 *
 * 为什么需要它：`app/web/chat.html` 里有一张「节点 → 中文名」的元数据表，
 * 它必须与后端的图**保持同步**。而这两边分属不同语言、不同文件，
 * 没有任何编译器会告诉你它们对不上了 —— 症状只会是界面上冒出几行
 * "（未登记的节点）"，或者某个节点悄悄少了"LLM"标记，很难被发现。
 *
 * 实测价值：写好的当天就抓到 `gate_handoff` 被误登记成了节点 ——
 * 它其实是**审计事件名**而不是节点名，永远不会作为 node 事件出现（死条目）。
 * 在真实的图轨迹里这两种名字长得很像，肉眼很容易混。
 *
 * 检查项：
 *   1. <script> 块的 JS 语法有效
 *   2. 前端登记的节点集合 == 后端 add_node 的节点集合（双向：不漏、不多）
 *   3. JS 里引用的 DOM id 在 HTML 中真实存在
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'app', 'web', 'chat.html'), 'utf8');

let fail = 0;
const check = (name, ok, detail = '') => {
  if (!ok) fail++;
  console.log(`  ${ok ? '✓' : '✗'} ${name}${ok || !detail ? '' : '   —— ' + detail}`);
};

// ── 1. 语法 ──
const m = html.match(/<script>([\s\S]*?)<\/script>/);
check('找到 <script> 块', !!m);
const js = m ? m[1] : '';
try {
  new vm.Script(js);
  check('JS 语法有效', true);
} catch (e) {
  check('JS 语法有效', false, e.message);
}

// ── 2. 节点集合双向比对 ──
const files = [
  'app/graph/build.py',
  'app/graph/sub_knowledge.py',
  'app/graph/sub_risk.py',
];
const backend = [];
for (const f of files) {
  const src = fs.readFileSync(path.join(root, f), 'utf8');
  for (const x of src.matchAll(/add_node\("([a-z_]+)"/g)) backend.push(x[1]);
}
const uniq = (a) => [...new Set(a)].sort();
const be = uniq(backend);
const fe = uniq([...js.matchAll(/^\s{2}([a-z_]+):\s*\{n:/gm)].map((x) => x[1]));

const missing = be.filter((n) => !fe.includes(n));
const extra = fe.filter((n) => !be.includes(n));
console.log(`\n  后端节点 ${be.length} 个 / 前端登记 ${fe.length} 个`);
check('前端覆盖全部后端节点（漏了会显示"未登记的节点"）', missing.length === 0, missing.join(', '));
check('前端没有登记不存在的节点（多半是拼错或误用了审计事件名）',
      extra.length === 0, extra.join(', '));

// ── 3. DOM id ──
const ids = uniq([...js.matchAll(/\$\("([a-zA-Z]+)"\)/g)].map((x) => x[1]));
const declared = new Set([...html.matchAll(/id="([a-zA-Z]+)"/g)].map((x) => x[1]));
const badIds = ids.filter((i) => !declared.has(i));
check(`JS 引用的 ${ids.length} 个 DOM id 全部存在`, badIds.length === 0, badIds.join(', '));

console.log(`\n${'═'.repeat(56)}`);
console.log(`失败 ${fail} 项`);
console.log('═'.repeat(56));
process.exit(fail ? 1 : 0);
