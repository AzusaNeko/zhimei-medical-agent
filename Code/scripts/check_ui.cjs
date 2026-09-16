/**
 * 前端一致性 + 安全回归检查：不启动服务器、不需要浏览器，纯静态校验。
 *
 *     node scripts/check_ui.cjs          （在 Code 目录下执行）
 *
 * 检查项：
 *   A. 两个页面的 <script> 语法有效
 *   B. chat.html 的「节点 → 中文名」表与后端 add_node **双向一致**
 *   C. JS 里引用的 DOM id 在 HTML 中真实存在
 *   D. ★ 运营面板里**不得再出现** X-Agent-Role / X-Agent-Id
 *
 * 为什么 B 需要：这两个名字表分属不同语言、不同文件，没有任何编译器会告诉你
 * 它们对不上了 —— 症状只会是界面上冒出几行"（未登记的节点）"。
 * 写好的当天就抓到 `gate_handoff` 被误登记成节点（它其实是审计事件名）。
 *
 * 为什么 D 需要：坐席角色一度是**客户端在请求头里自己声明**的
 * （`X-Agent-Role: compliance`），改个头就能拿到未脱敏手机号。
 * 现在角色来自 JWT + 数据库，这条检查防止有人在后续改动里把它加回来。
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
let fail = 0;
const check = (name, ok, detail = '') => {
  if (!ok) fail++;
  console.log(`  ${ok ? '✓' : '✗'} ${name}${ok || !detail ? '' : '   —— ' + detail}`);
};
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const scriptOf = (html) => {
  const m = html.match(/<script>([\s\S]*?)<\/script>/);
  return m ? m[1] : '';
};

/** 剥掉 JS 注释再扫描。
 *
 *  ★ 这是必须的，第一版没做就误报了：代码里恰恰**应该**留着
 *    「以前这么写、为什么不行」的注释（比如记录 X-Agent-Role 那个洞），
 *    而不带注释剥离的全文扫描会把这种说明当成违规。
 *    为了通过检查去删掉解释性注释，是本末倒置。
 *
 *  只匹配「整行以 // 开头」和块注释，不会误伤字符串里的 https:// 。
 */
const stripComments = (js) => js
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^[ \t]*\/\/.*$/gm, '');

const chatHtml = read('app/web/chat.html');
const panelHtml = read('app/ops/panel.html');

// ── A. 语法 ──
console.log('A. JS 语法');
for (const [name, html] of [['chat.html', chatHtml], ['panel.html', panelHtml]]) {
  const js = scriptOf(html);
  if (!js) { check(`${name} 有 <script> 块`, false); continue; }
  try {
    new vm.Script(js);
    check(`${name} 语法有效`, true);
  } catch (e) {
    check(`${name} 语法有效`, false, e.message);
  }
}

// ── B. 节点表双向一致 ──
console.log('\nB. 节点表与后端 add_node 一致（chat.html）');
const js = scriptOf(chatHtml);
const backend = [];
for (const f of ['app/graph/build.py', 'app/graph/sub_knowledge.py', 'app/graph/sub_risk.py']) {
  for (const x of read(f).matchAll(/add_node\("([a-z_]+)"/g)) backend.push(x[1]);
}
const uniq = (a) => [...new Set(a)].sort();
const be = uniq(backend);
const fe = uniq([...js.matchAll(/^\s{2}([a-z_]+):\s*\{n:/gm)].map((x) => x[1]));
console.log(`   后端 ${be.length} 个 / 前端登记 ${fe.length} 个`);
const missing = be.filter((n) => !fe.includes(n));
const extra = fe.filter((n) => !be.includes(n));
check('前端覆盖全部后端节点（漏了会显示"未登记的节点"）', missing.length === 0, missing.join(', '));
check('前端没有登记不存在的节点（多半是拼错或误用了审计事件名）', extra.length === 0, extra.join(', '));

// ── C. DOM id ──
console.log('\nC. JS 引用的 DOM id 存在');
// 两种写法都要覆盖：$("id") 与 document.getElementById("id")
const idsOf = (src) => uniq([
  ...[...src.matchAll(/\$\("([a-zA-Z]+)"\)/g)].map((x) => x[1]),
  ...[...src.matchAll(/getElementById\("([a-zA-Z]+)"\)/g)].map((x) => x[1]),
]);
const declaredOf = (html) => new Set([...html.matchAll(/id="([a-zA-Z]+)"/g)].map((x) => x[1]));
for (const [name, html] of [['chat.html', chatHtml], ['panel.html', panelHtml]]) {
  const ids = idsOf(stripComments(scriptOf(html)));
  const declared = declaredOf(html);
  const bad = ids.filter((i) => !declared.has(i));
  check(`${name} 引用的 ${ids.length} 个 DOM id 全部存在`, bad.length === 0, bad.join(', '));
}

// ── D. 安全回归：不得再用请求头声明坐席角色 ──
console.log('\nD. 坐席角色不得由客户端声明');
const panelJs = stripComments(scriptOf(panelHtml));
const headerLeak = [
  ['X-Agent-Role', /X-Agent-Role/i],
  ['X-Agent-Id', /X-Agent-Id/i],
].filter(([, re]) => re.test(panelJs)).map(([n]) => n);
check('panel.html 不含 X-Agent-Role / X-Agent-Id（角色必须来自令牌）',
      headerLeak.length === 0, headerLeak.join(', '));
check('panel.html 的 headers() 会带上 Authorization',
      /Authorization/.test(panelJs) && /Bearer/.test(panelJs), '没有找到 Bearer 令牌');

// 后端同理：ops 的依赖里不该再有 Header(...) 读角色
const opsDeps = read('app/ops/deps.py');
check('ops/deps.py 不再从请求头解析角色',
      !/x_agent_role/i.test(opsDeps) && !/Header\(/.test(opsDeps),
      '仍在使用 Header 读取角色');

console.log(`\n${'═'.repeat(56)}`);
console.log(`失败 ${fail} 项`);
console.log('═'.repeat(56));
process.exit(fail ? 1 : 0);
