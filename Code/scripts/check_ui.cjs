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
 *   E. ★ 测试工单：默认不进队列、只有 admin 能清、且清库 SQL 只删 is_test
 *
 * 为什么 B 需要：这两个名字表分属不同语言、不同文件，没有任何编译器会告诉你
 * 它们对不上了 —— 症状只会是界面上冒出几行"（未登记的节点）"。
 * 写好的当天就抓到 `gate_handoff` 被误登记成节点（它其实是审计事件名）。
 *
 * 为什么 D 需要：坐席角色一度是**客户端在请求头里自己声明**的
 * （`X-Agent-Role: compliance`），改个头就能拿到未脱敏手机号。
 * 现在角色来自 JWT + 数据库，这条检查防止有人在后续改动里把它加回来。
 *
 * 为什么 E 需要：清理测试工单是**不可逆**的删库操作，而它的正确性完全靠
 * 一条 WHERE 子句撑着 —— 有人手滑把 `is_test = true` 删掉、或者把默认值
 * 改成 true，脚本不会报错，只会安静地把真实顾客的工单清空。
 * 权限名 `admin:purge` 也同时写在 Python 和 JS 里，同样没有任何编译器
 * 会告诉你两边拼得不一样（症状是按钮显示了但一点就 403）。
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

// ── E. 测试工单：默认隐藏 + 只有 admin 能清 + SQL 只删 is_test ──
console.log('\nE. 测试工单的隔离与清理');
const opsRoutes = read('app/ops/routes.py');

// E1 默认值必须是 False。改成 True 不会报任何错，只会让几十张测试工单
//    重新混进真实队列，而这正是这个功能要解决的问题。
check('队列默认不含测试工单（include_test 默认 False）',
      /include_test:\s*bool\s*=\s*Query\(\s*default=False/.test(opsRoutes),
      '默认值被改成了 True，测试工单会重新混进真实队列');

// E2 面板必须真的把勾选框传下去，否则那个复选框是个摆设
check('面板拉队列时带上 include_test', /include_test=\$\{showTest\}/.test(panelJs),
      '勾选框没有生效，勾了也看不到测试工单');

// E3 权限名跨语言一致：Python 定义 / Python 判定 / JS 门控三处必须同一个串
const purgePerm = (opsDeps.match(/PURGE_PERMISSION\s*=\s*"([^"]+)"/) || [])[1];
check('deps.py 定义了 PURGE_PERMISSION', Boolean(purgePerm));
check('清理端点要求该权限（不是只靠前端藏按钮）',
      /require\(agent,\s*PURGE_PERMISSION\)/.test(opsRoutes),
      '后端没有 require，前端改 localStorage 就能清库');
check(`前端门控用的权限名与后端相同（${purgePerm}）`,
      Boolean(purgePerm) && panelJs.includes(`"${purgePerm}"`),
      '两边拼写不一致 → 按钮显示了但一点就 403');

// E4 ★ 最有价值的一条：清理 SQL 里的 WHERE is_test = true 是"不误删真实
//    工单"的**唯一**保障。这条子句一旦丢失，代码照跑、测试不报错，
//    只会在某次执行时把顾客的工单删光。
const pgSrc = read('app/services/pg.py');
const purgeFn = pgSrc.slice(pgSrc.indexOf('async def purge_test_tickets'),
                            pgSrc.indexOf('async def get_ticket',
                                          pgSrc.indexOf('async def purge_test_tickets')));
// 注意两种引号都要匹配：子表那条是三引号长句，主表那条是单引号短句 ——
// 第一版只写了三引号，于是"只找到 1 条"误报，而那条恰恰是主表的 DELETE。
// 也不能只写 `DELETE`：下面注释里就有一句 `"DELETE 12" 这样的状态串`，
// 会被当成一条没有 WHERE 的删除语句 —— 要求 `DELETE FROM` 才排除掉它。
const deletes = [...purgeFn.matchAll(/"""(DELETE\s+FROM[\s\S]*?)"""|"(DELETE\s+FROM[^"\n]*)"/g)]
  .map((m) => m[1] ?? m[2]);
check('找到清理用的 DELETE 语句', deletes.length >= 2, `只找到 ${deletes.length} 条`);
check('每条 DELETE 都带 is_test = true（否则会删到真实工单）',
      deletes.length > 0 && deletes.every((s) => /is_test\s*=\s*true/.test(s)),
      `共 ${deletes.length} 条，缺 WHERE 的：${deletes.filter((s) => !/is_test\s*=\s*true/.test(s)).length} 条`);

// E5 内存档位必须用同一条规则判定，否则 fake 档位测过、真实档位行为不同
const fakeLine = read('app/services/fakes.py').split('\n')
  .find((l) => /"is_test"\s*:/.test(l)) || '';
check('FakePg 用同一条 channel == "test" 规则判定 is_test',
      /channel/.test(fakeLine) && /"test"/.test(fakeLine),
      fakeLine.trim() || '没有找到 is_test 的赋值行');

// E6 ★ 指标也必须排除测试工单。
//    只把测试工单从**队列**里藏起来是不够的：avg_wait_seconds 就是 SLA 指标，
//    而自动化脚本每跑一次就塞一张 P0 工单、且从不接单（accepted_at 永远为空）——
//    结果是"平均等待时长"被历史测试数据永久污染。
//    指标里的脏数据比队列里的脏数据更危险：队列里看得见，指标里会被当成结论用。
const mStart = pgSrc.indexOf('async def ops_metrics');
let mEnd = pgSrc.indexOf('async def', mStart + 10);
if (mEnd < 0) mEnd = pgSrc.length;
const metricFn = mStart < 0 ? '' : pgSrc.slice(mStart, mEnd);
check('找到 ops_metrics', mStart >= 0);
check('指标统计排除测试工单（否则 SLA 被测试数据永久污染）',
      /FROM ops\.handoff_ticket WHERE is_test = false/.test(metricFn),
      'ops_metrics 的工单统计没有 WHERE is_test = false');

const fakeMetrics = read('app/services/fakes.py')
  .split('\n').find((l) => /is_test 的过滤与 PgStore 一致/.test(l)) || '';
check('FakePg 的指标也排除测试工单（两边判定必须一致）', Boolean(fakeMetrics),
      'FakePg.ops_metrics 没有排除 is_test');

// E7 测试工单在界面上必须能一眼认出（只隐藏不标记 = 勾选后还是分不清）
check('测试工单行上有「测试」徽章',
      /\.badge\.test/.test(panelHtml) && /is_test \? '<span class="badge test">/.test(panelJs),
      '没有徽章，勾选后仍分不清哪张是测试的');

console.log(`\n${'═'.repeat(56)}`);
console.log(`失败 ${fail} 项`);
console.log('═'.repeat(56));
process.exit(fail ? 1 : 0);
