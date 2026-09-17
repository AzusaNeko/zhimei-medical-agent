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
let pass = 0;
const check = (name, ok, detail = '') => {
  if (ok) pass++; else fail++;
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
const apiEvents = read('app/api/events.py');

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

// E8 ★ 服务端异常必须留日志。
//    这条也是踩出来的：图执行抛异常时只发了一个 SSE error 事件、**没有打日志**，
//    于是服务端日志里查无此事 —— 只有恰好盯着浏览器事件流的人才知道出过错。
//    error 事件是发给**用户**的，不能顶替给运维的记录。
const apiRoutes = read('app/api/routes.py');
check('图执行异常会写服务端日志（logger.exception）',
      /logger\.exception\(/.test(apiRoutes),
      'routes.py 里没有 logger.exception —— 服务端异常在日志里将查无此事');

// E9 ★ 引用编号的"宽/严"是有意为之的一对，别把它抹平。
//    给回答类 Prompt 注入对话历史之后，模型会看到**上一轮回答**里的 [E1] 标记，
//    于是顺手把它填进本轮的 citations —— 实测就是这么炸的：
//      specialist_draft 结构化输出两次均不合格：citations.0 Input should be an object
//      input_value='E1'
//    → LLMError → 兜底转人工。顾客只问了一句"那个更适合我？"，
//    收到的却是"已为您转接人工客服"，原因只是一个引用编号的写法。
//    所以：专业 Agent 宽容（那里的 citations 不是溯源凭据），
//    知识库草稿严格（那里的 citations **就是**可溯源的凭据本身）。
const schemas = read('app/graph/schemas.py');
const specBlock = schemas.slice(schemas.indexOf('class SpecialistDraftOut'),
                                schemas.indexOf('class ReceiptOut'));
const kbBlock = schemas.slice(schemas.indexOf('class KbDraftOut'),
                              schemas.indexOf('class ClaimCheck'));
check('专业 Agent 的 citations 容忍裸字符串（历史里的 [E1] 不该炸掉整轮）',
      /_coerce_bare_ids/.test(specBlock),
      'SpecialistDraftOut 没有裸字符串收敛 —— 上一轮的 [E1] 会让整轮变成转人工');
check('知识库草稿的 citations 仍然严格（那是"可溯源"的凭据本身）',
      !/_coerce_bare_ids|field_validator\("citations"/.test(kbBlock),
      'KbDraftOut 也放宽了 —— 引用可能缺少 quote/doc_id，无法定位到原文');

// E10 对话历史块必须提醒模型别抄历史里的引用编号
check('注入历史时明确说明历史里的 [E1] 不属于本轮',
      /\[E1\]/.test(read('app/prompts/system.py')),
      'HISTORY_RULES 没提这件事 —— 模型会继续把上一轮的引用编号抄进本轮');

// ── F. 两个页面在"已登录 + 刷新"下必须真的能启动 ──
//
// ★ 这一节是踩出来的，不是预防性设计。`bootData()` 里写了一个
//   `connectStream()`，而函数真名是 `connect` —— 抛出的 ReferenceError
//   被 start() 的大 try/catch 吞掉，界面上表现为**每次刷新都弹回登录页**，
//   而队列其实加载得好好的。排查方向被"登录"两个字带偏了很久。
//
//   A~E 全是静态文本扫描，抓不到这种东西：语法没错、id 都在、
//   函数名也在别处定义了。只有**真的把脚本跑一遍**才能发现。
//
//   所以这里给一个最小 DOM 桩 + 假 fetch，在两个页面各自的
//   "已登录、正在刷新"状态下把脚本完整执行一次，然后看：
//     · 登录层是不是保持隐藏（= 页面没把自己当成未登录）
//     · 启动过程有没有 console.error（两页的启动失败都会走这里）
//     · 面板有没有真的去连实时流（守住 connect 被调用）
console.log('\nF. 页面启动（桩环境里真跑一遍脚本）');

const newEl = (id, onText) => {
  const e = {
    id, hidden: false, innerHTML: '', className: '', value: '',
    checked: false, disabled: false, title: '', onclick: null, onchange: null,
    style: {}, dataset: {}, children: [], parentNode: null,
    scrollTop: 0, scrollHeight: 0, clientHeight: 0, offsetHeight: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, remove() {}, insertBefore() {}, replaceChildren() {},
    focus() {}, blur() {}, click() {},
    setAttribute() {}, getAttribute: () => null,
    querySelector: () => null, querySelectorAll: () => [],
    getContext: () => ({ measureText: () => ({ width: 0 }), fillText() {}, fillRect() {}, clearRect() {} }),
  };
  // textContent 用带记录的 setter：这样"某条消息有没有被画出来"可断言
  let text = '';
  Object.defineProperty(e, 'textContent', {
    get: () => text,
    set: (v) => { text = String(v); if (onText) onText(text); },
  });
  return e;
};

/** 跑一个页面的脚本。返回 {els, seen, errors, logged, texts, intervals} */
async function bootPage(html, { token, routes, local }) {
  const declared = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
  const els = new Map();
  const texts = [];        // 所有被写进 textContent / createTextNode 的文字
  const record = (t) => texts.push(String(t));
  const document = {
    title: '', hidden: false,
    getElementById(id) {
      if (!declared.has(id)) return null;   // 和浏览器一致：引用不存在的 id 会抛错
      if (!els.has(id)) els.set(id, newEl(id, record));
      return els.get(id);
    },
    createElement: (t) => newEl(t, record),
    // SVG 用的命名空间版本。工作流图整张都是 SVG，少了这个 stub 会直接报
    // "createElementNS is not a function"，而真实浏览器里不会 —— 那就是个假失败。
    createElementNS: (ns, t) => newEl(t, record),
    createTextNode: (t) => { record(t); return newEl('#text', record); },
    addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
    body: newEl('body', record), documentElement: newEl('html', record),
  };
  const store = new Map([['zhimei.token', token], ['zhimei.agentToken', token]]);
  for (const [k, v] of Object.entries(local || {})) store.set(k, String(v));
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  const seen = [];
  const logged = [];
  const intervals = [];    // 捕获 setInterval 的回调，供测试手动触发一次
  // 假服务：不发真请求，全部返回预置 JSON。/ops/stream 给一条**永不结束**的流，
  // 这样 connect() 会停在 read() 上（像真浏览器一样），不会反复重连刷屏。
  const fetchStub = async (url) => {
    const u = String(url);
    seen.push(u);
    if (u.includes('/ops/stream')) {
      return new Response(new ReadableStream({ start() {} }), { status: 200 });
    }
    const hit = Object.keys(routes).find((k) => u.includes(k));
    const body = hit ? routes[hit] : {};
    return new Response(JSON.stringify(body),
                        { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  const errors = [];
  const ctx = {
    document, localStorage, fetch: fetchStub,
    console: { log() {}, warn() {}, error: (...a) => logged.push(a.join(' ')) },
    setTimeout: () => 0, clearTimeout() {},
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval() {},
    JSON, Math, Date, TextDecoder, TextEncoder, Response, Request, Headers,
    ReadableStream, AbortController, URL, URLSearchParams,
    confirm: () => false, alert() {}, prompt: () => null,
    location: { href: 'http://127.0.0.1:8090/', reload() {} },
  };
  ctx.window = ctx; ctx.globalThis = ctx; ctx.self = ctx;
  const js = scriptOf(html);
  try {
    vm.createContext(ctx);
    new vm.Script(js).runInContext(ctx);
  } catch (e) {
    errors.push('同步抛错: ' + e.message);
  }
  await new Promise((r) => setTimeout(r, 60));   // 等启动里的 await 链走完
  return { els, seen, errors, logged, texts, intervals, routes, ctx };
}

const TOKEN = 'x'.repeat(40);   // 只用来占位，服务端是桩

/** 桩用的最小架构数据。所有 chat.html 场景都给一份 —— 因为架构窗口是常驻的，
 *  登录后就会去取，缺了它页面会走"加载失败"分支（那条分支另有专门断言）。 */
const MINI_ARCH = {
  architecture: {
    layers: [
      {id: 'L0', title: '入口与调度层', hint: '标准化 → 意图 → 分派',
       nodes: ['normalize', 'classify', 'dispatch']},
      {id: 'L1', title: '草稿产出层 · 七路并行', hint: '只产出草稿',
       nodes: ['k_agent', 'p_agent']},
      {id: 'GATE', title: '聚合与必经关卡', hint: '必经', nodes: ['aggregate', 'risk_gate']},
      {id: 'EXITS', title: '出口层', hint: '持有凭据', nodes: ['send', 'human_handoff']},
    ],
    decisions: ['dispatch', 'risk_gate'],
    subgraphs: [
      {id: 'kb', title: '知识科普子图', hint: '检索 → 证据', hosts: ['k_agent'],
       entry: 'kb_intake', nodes: ['kb_intake', 'kb_draft']},
      {id: 'risk', title: '风险审查子图', hint: '规则 + 面板', hosts: ['risk_gate'],
       entry: 'gate_in', nodes: ['gate_in', 'issue_token']},
    ],
    edges: [['normalize', 'classify', ''], ['classify', 'dispatch', ''],
            ['dispatch', 'k_agent', '科普咨询'], ['k_agent', 'aggregate', ''],
            ['aggregate', 'risk_gate', ''], ['risk_gate', 'send', 'pass 通过']],
    subgraph_edges: {},
  },
  graph: {nodes: [], sub_nodes: {}, sub_edges: {}},
  warnings: [],
};

(async () => {
  // ── F1 坐席台：刷新后不该弹回登录页，且必须去连实时流 ──
  const panel = await bootPage(panelHtml, {
    token: TOKEN,
    routes: {
      '/ops/auth/me': { agent: { agent_id: 'a1', name: '客服小美', role: 'service' },
                        permissions: ['ticket:read'], sees_raw_pii: false },
      '/ops/tickets': { agent: { agent_id: 'a1' }, tickets: [], include_test: false },
      '/ops/metrics': { tickets_total: 0, tickets_open: 0, review_rounds: 0, hard_rule_top: [] },
    },
  });
  check('panel.html 刷新后不弹回登录页（令牌有效时）',
        panel.els.get('authLayer') && panel.els.get('authLayer').hidden === true,
        '登录层显示了：说明启动过程里抛错并被当成"未登录"');
  check('panel.html 启动时真的去连了实时流 /ops/stream',
        panel.seen.some((u) => u.includes('/ops/stream')),
        '没请求过 /ops/stream —— 实时流没接上，新工单不会自动出现（曾因函数名拼错踩过）');
  check('panel.html 启动过程没有 console.error',
        panel.logged.length === 0, panel.logged.join(' | ').slice(0, 160));
  check('panel.html 启动过程没有抛错', panel.errors.length === 0, panel.errors.join(' | '));

  // ── F2 C 端：刷新后不该弹回登录页；带一个"人工接管中"的会话 ──
  const SID = '11111111-2222-3333-4444-555555555555';
  const chat = await bootPage(chatHtml, {
    token: TOKEN,
    local: { 'zhimei.currentSession': SID },   // 刷新后回到上次那个会话
    routes: {
      '/api/health': { profile: 'fake', checkpointer: 'memory' },
      '/api/auth/me': { user: { user_id: 'u1', display_name: '演示用户',
                                email: 'demo@zhimei.test' } },
      ['/api/sessions/' + SID]: {
        session: { session_id: SID, ai_enabled: false },
        messages: [
          { role: 'user', content: '我做完水光第三天，脸发白还特别疼' },
          { role: 'assistant', content: '请尽快到院急诊。' },
          { role: 'agent', content: '您好，我是值班客服小美，正在为您登记。' },
        ],
      },
      '/api/sessions': { sessions: [{ session_id: SID, title: '术后不适',
                                      ai_enabled: false, message_count: 3 }] },
    },
  });
  check('chat.html 刷新后不弹回登录页（令牌有效时）',
        chat.els.get('authLayer') && chat.els.get('authLayer').hidden === true,
        '登录层显示了：说明启动过程里抛错并被当成"未登录"');
  check('chat.html 启动过程没有 console.error',
        chat.logged.length === 0, chat.logged.join(' | ').slice(0, 160));
  check('chat.html 启动过程没有抛错', chat.errors.length === 0, chat.errors.join(' | '));

  // 人工接管中的会话：★ 输入框必须**保持可用**。
  //   早期版本在这里禁用输入框（怕用户发出去收到 409），结果很荒唐：
  //   顾客刚被告知"已为您转接人工客服"，下一句就发不出去了。
  //   现在的约定是"消息照收、AI 不答"（后端 _takeover_source）。
  const input = chat.els.get('input');
  check('chat.html 打开"人工接管中"的会话仍让用户能继续说话',
        input && input.disabled === false,
        '输入框被禁用了 —— 用户被告知"马上有人来"之后却说不了话');  check('chat.html 接管中会显示提示条（告诉用户 AI 暂停、消息会转给客服）',
        chat.els.get('takeoverBar') && chat.els.get('takeoverBar').hidden === false,
        '没有提示条 —— 用户会一直等 AI 回复');

  // ★ 点「＋ 新对话」后提示条必须收掉。
  //   这条是用户报的 bug：新建对话里还挂着"人工客服已接管"。
  //   根因是接管状态散在四个地方（takeover / lastSeenTaken / 提示条 hidden /
  //   placeholder），而 newChat() 直接给前两个变量赋值、跳过了 DOM。
  const newBtn = chat.els.get('newChat');
  if (newBtn && newBtn.onclick) {
    newBtn.onclick();
    check('点「＋ 新对话」后接管提示条收掉（不能带到新会话）',
          chat.els.get('takeoverBar').hidden === true,
          '提示条还在 —— 新对话里会一直显示"人工客服处理中"');
    check('点「＋ 新对话」后输入框 placeholder 复位',
          chat.els.get('input').placeholder !== '继续补充情况，客服会看到…',
          `placeholder 仍是：${chat.els.get('input').placeholder}`);
  } else {
    check('chat.html 的「＋ 新对话」按钮绑定了处理函数', false, '没找到 onclick');
  }

  // ── F3 轮询：坐席回复必须能**主动**出现在用户这一侧 ──
  //
  //  ★ 这是这次报的第二个问题：坐席回复走的是另一条链路
  //    （/ops/tickets/{id}/reply 直接写库），用户这一侧的 SSE 早就断了，
  //    没有任何东西会通知他 —— 不轮询就只能手动刷新。
  //
  //  这里做的是**行为测试**而不是文本检查：先让"服务端"只有一条用户消息，
  //  启动页面，然后模拟坐席在这期间回复（改掉桩数据 + 把会话置为人工接管），
  //  手动触发一次轮询定时器，看这条回复有没有被画到界面上、提示条有没有点上。
  const w = await bootPage(chatHtml, {
    token: TOKEN,
    local: { 'zhimei.currentSession': SID },
    routes: {
      '/api/health': { profile: 'fake', checkpointer: 'memory' },
      '/api/auth/me': { user: { display_name: '演示用户' } },
      ['/api/sessions/' + SID]: {
        session: { session_id: SID, ai_enabled: true },
        messages: [{ role: 'user', content: '我做完水光第三天，脸发白还特别疼' }],
      },
      '/api/sessions': { sessions: [{ session_id: SID, title: '术后不适', ai_enabled: true }] },
    },
  });
  const poller = w.intervals.find((i) => i.fn);
  check('chat.html 注册了轮询定时器',
        Boolean(poller), '没有 setInterval —— 坐席回复不可能自动出现');

  if (poller) {
    const AGENT_TEXT = '您好，我是值班客服小美，已经帮您登记，请尽快到院。';
    // 模拟坐席这一刻的回复 + 接单（接单会把 ai_enabled 置 false）
    w.routes['/api/sessions/' + SID] = {
      session: { session_id: SID, ai_enabled: false },
      messages: [
        { role: 'user', content: '我做完水光第三天，脸发白还特别疼' },
        { role: 'assistant', content: '请尽快到院急诊。' },
        { role: 'agent', content: AGENT_TEXT },
      ],
    };
    const before = w.seen.filter((u) => u.includes('/api/sessions/' + SID)).length;
    await poller.fn();
    const after = w.seen.filter((u) => u.includes('/api/sessions/' + SID)).length;
    check('一次轮询会去拉当前会话', after > before, `拉取次数没变（${before} → ${after}）`);
    check('坐席的回复被画到了对话区（无需手动刷新）',
          w.texts.some((t) => t.includes('值班客服小美')),
          '界面上找不到坐席那句话');
    check('坐席接单后用户仍能继续说话（消息转给坐席，AI 不答）',
          w.els.get('input') && w.els.get('input').disabled === false,
          '输入框被禁用了 —— 用户被接管后就说不了话了');
    check('坐席接单后接管提示条出现',
          w.els.get('takeoverBar') && w.els.get('takeoverBar').hidden === false,
          '没有提示条 —— 用户不知道 AI 已暂停');
    check('已经画过的消息不会被重复追加',
          w.texts.filter((t) => t.includes('脸发白还特别疼')).length === 1,
          `用户那句话出现了 ${w.texts.filter((t) => t.includes('脸发白还特别疼')).length} 次`);
  }

  // ── H. 工作流执行图（切到那一栏要真的画出来）──
  //
  //  ★ 只做静态检查是不够的："能取到拓扑" 和 "能画出来" 是两件事，
  //    中间隔着布局、SVG 组装、分组着色好几步，任何一步错了界面都是一片空白。
  //    所以这里给一个桩 /api/graph，真的点一下「工作流图」那一栏，看 SVG 里
  //    有没有东西。
  console.log('\nH. 工作流执行图');
  const GRAPH_STUB = {
    architecture: {
      layers: [
        {id: 'L0', title: '入口与调度层', hint: '标准化 → 意图 → 分派',
         nodes: ['normalize', 'classify', 'dispatch']},
        {id: 'L1', title: '草稿产出层 · 七路并行', hint: '只产出草稿',
         nodes: ['k_agent', 'p_agent']},
        {id: 'GATE', title: '聚合与必经关卡', hint: '必经', nodes: ['aggregate', 'risk_gate']},
        {id: 'EXITS', title: '出口层', hint: '持有凭据', nodes: ['send', 'human_handoff']},
      ],
      decisions: ['dispatch', 'risk_gate'],
      subgraphs: [
        {id: 'kb', title: '知识科普子图', hint: '检索 → 证据', hosts: ['k_agent'],
         entry: 'kb_intake', nodes: ['kb_intake', 'kb_draft']},
        {id: 'risk', title: '风险审查子图', hint: '规则 + 面板', hosts: ['risk_gate'],
         entry: 'gate_in', nodes: ['gate_in', 'issue_token']},
      ],
      edges: [['normalize', 'classify', ''], ['classify', 'dispatch', ''],
              ['dispatch', 'k_agent', '科普咨询'], ['k_agent', 'aggregate', ''],
              ['aggregate', 'risk_gate', ''], ['risk_gate', 'send', 'pass 通过']],
      subgraph_edges: {},
    },
    graph: {nodes: ['normalize', 'classify', 'dispatch', 'k_agent', 'p_agent', 'aggregate',
                    'risk_gate', 'send', 'human_handoff', 'kb_intake', 'kb_draft',
                    'gate_in', 'issue_token'],
            sub_nodes: {}, sub_edges: {}},
    warnings: [],
  };
  const chatG = await bootPage(chatHtml, {
    token: TOKEN,
    routes: {
      '/api/health': { profile: 'fake', checkpointer: 'memory' },
      '/api/auth/me': { user: { display_name: '演示用户' } },
      '/api/graph': MINI_ARCH,
      '/api/sessions': { sessions: [] },
    },
  });
  const doc = chatG.ctx && chatG.ctx.document;
  const paneG = doc && doc.getElementById('paneGraph');
  const paneT = doc && doc.getElementById('paneTrace');
  // ★ 两个窗口**同屏**：都没有被 hidden，而且是两个独立的容器
  //   （各自有自己的标题栏与滚动区）。上一版是一栏切换，对照时要来回点。
  check('chat.html 有两个独立的视图窗口（架构 + 轨迹）',
        Boolean(paneG) && Boolean(paneT) && paneG !== paneT,
        `paneGraph=${Boolean(paneG)} paneTrace=${Boolean(paneT)}`);
  check('两个窗口**同时可见**（不再是一栏切换）',
        Boolean(paneG) && paneG.hidden === false
        && Boolean(paneT) && paneT.hidden === false,
        `paneGraph.hidden=${paneG && paneG.hidden} paneTrace.hidden=${paneT && paneT.hidden}`);
  check('已移除"切换栏"的按钮（两个都常驻，不需要切）',
        !doc.getElementById('tabGraph') && !doc.getElementById('tabTrace'),
        '还留着切换按钮');
  check('工作流架构请求了后端数据 /api/graph',
        chatG.seen.some((u) => u.includes('/api/graph')),
        '没有请求架构数据 —— 图是画不出来的');
  const svg = doc.getElementById('gSvg');
  const drawn = svg ? svg.children.length : 0;
  check('架构窗口在同屏状态下就画出来了（不用点任何东西）', drawn > 0,
        `SVG 里是空的（${drawn} 个子元素）—— 布局或绘制那一步断了`);
  const summary = String(doc.getElementById('gSummary').innerHTML);
  check('渲染了"本次经过"摘要（哪几层 / 哪几个子图）',
        summary.includes('本次对话经过') && summary.includes('知识科普子图'),
        `摘要是：${summary.slice(0, 120)}`);
  check('架构与代码一致时摘要不报警告', !summary.includes('⚠'), summary.slice(0, 160));
  check('拓扑/架构不在前端写死（必须来自 /api/graph）',
        !/const\s+GRAPH\s*=|const\s+EDGES\s*=|const\s+LAYERS\s*=/.test(stripComments(scriptOf(chatHtml))),
        '前端出现了写死的架构常量 —— 那会和设计图、代码三方漂移');

  // ── I. 接管状态的两条不变量（直接驱动事件处理函数，不是扫文本）──
  //
  //  ★ 这两条都是用户报的 bug：
  //    ① 刚转人工就弹出"AI 已暂停自动回复" —— 可那时 AI 并没有停
  //       （按设计要等坐席**接单**才停），等于对用户谎报；
  //    ② 接管结束后提示条下不去 —— 因为"同步提示条"被放在轮询的
  //       提前 return 之后，只要对话内容没变化它就永远不执行。
  console.log('\nI. 接管状态（提示条不能说谎、也不能卡住）');
  const bars = await bootPage(chatHtml, {
    token: TOKEN,
    routes: {
      '/api/health': { profile: 'fake', checkpointer: 'memory' },
      '/api/auth/me': { user: { display_name: '演示用户' } },
      '/api/sessions': { sessions: [] },
      '/api/graph': MINI_ARCH,
      ['/api/sessions/' + SID]: { session: { session_id: SID, ai_enabled: true }, messages: [] },
    },
  });
  const fn = bars.ctx && bars.ctx.handle;
  // ★ 元素要通过 document 拿：桩是按需创建元素的（页面没碰过的 id 不在表里），
  //   直接读 els.get() 会拿到 undefined，后面整段断言就**静默跳过**了 ——
  //   而"静默跳过的检查"比失败的检查更糟：它看起来是绿的。
  const bar = bars.ctx && bars.ctx.document.getElementById('takeoverBar');
  const msgBox = bars.ctx && bars.ctx.document.getElementById('messages');
  const statusEl = bars.ctx && bars.ctx.document.getElementById('statusText');
  check('chat.html 的事件处理函数与提示条可用（否则后面几条会静默跳过）',
        typeof fn === 'function' && Boolean(bar) && Boolean(msgBox) && Boolean(statusEl),
        `handle=${typeof fn} bar=${Boolean(bar)} messages=${Boolean(msgBox)}`);
  if (typeof fn === 'function' && bar && msgBox && statusEl) {
    bar.hidden = true;                                   // 先归零
    fn('handoff', {ticket_id: 'T-1', priority: 'P0', reason: 'emergency',
                   text: '已为您转接人工客服。'});
    check('收到 handoff 事件时不点亮提示条（那一刻 AI 还没停）',
          bar.hidden === true,
          '提示条亮了 —— 但坐席还没接单，AI 其实仍在作答，这是在谎报');

    // 接管开始后的第一条：要有气泡（用户得知道 AI 停了、消息转给谁）
    const before1 = msgBox.children.length;
    fn('human_takeover', {text: '已收到，并已转达正在为您服务的客服。', accepted: true});
    const after1 = msgBox.children.length;
    check('接管后第一条回执会插进对话区（必须让用户知道 AI 已暂停）',
          after1 > before1, `消息数没变（${before1} → ${after1}）`);

    // 之后的每一条：只在状态栏提示，不再插气泡（否则连着补充几句就刷屏了）
    const before2 = msgBox.children.length;
    fn('human_takeover', {text: '已转达客服', quiet: true, accepted: true});
    const after2 = msgBox.children.length;
    check('接管期间的后续消息不再重复插气泡（quiet 只走状态栏）',
          after2 === before2, `又多插了 ${after2 - before2} 个气泡`);
    check('quiet 回执走了状态栏',
          /已转达客服/.test(String(statusEl.textContent)),
          `状态栏是：${statusEl.textContent}`);

    check('回执文案里不出现内部坐席工号（agent-xxx）',
          !/agent-[a-z0-9]/i.test(bars.texts.join(' ')),
          '把内部工号显示给顾客了');
  }
  check('提示条状态只在 applyPoll 的提前 return **之前**同步（否则会卡住）',
        /syncTakeover\(taken, false\)[\s\S]{0,900}?if \(sameTail/.test(
          stripComments(scriptOf(chatHtml))),
        'syncTakeover 又跑到提前 return 后面去了 —— 对话没变化时提示条就永远不更新');

  // ── G. 正文的"流式"呈现 ──
  //
  //  ★ 这个项目**不流式发送正文**，而且这是刻意的：正文必须整段通过风险闸
  //    （规则 + 三份面板意见）之后才能出站。按 token 往外吐，用户看到的就是
  //    **未经审查的内容** —— 违规用语、未核实的疗效承诺都会先到他眼前。
  //    所以做的是**送达之后的显示动画**（打字机），内容安全性一个字都没变。
  //    这几条锁的就是"别哪天有人把它改成真流式"。
  console.log('\nG. 正文呈现（送到之后的动画，不是流式发送）');
  const chatJs = stripComments(scriptOf(chatHtml));
  check('正文走打字机渲染（final 事件用 addMsgTyped）',
        /case "final":[\s\S]{0,400}?addMsgTyped\(/.test(chatJs),
        'final 还是直接整段插入 —— 或者被改成了真流式发送');
  check('打字机效果可以点掉（长文本不该强迫人干等）',
        /m\.onclick = finish/.test(chatJs) && /const finish = /.test(chatJs),
        '没有"点一下显示全文"的出口');
  check('尊重 prefers-reduced-motion（系统设了减少动效就整段显示）',
        /prefers-reduced-motion/.test(chatJs),
        '无障碍设置被忽略');
  check('后端仍然是"整段 final"，没有改成 token 流',
        !/event: *chunk|EVENT_CHUNK|stream_token/.test(apiEvents),
        'events.py 里出现了分片事件 —— 那意味着未经审查的正文开始出站');
  check('角标不再写"不流式"（那会让人以为逐字呈现是坏了）',
        !/整段送达（不流式）/.test(chatJs),
        '角标还写着"不流式" —— 与用户看到的逐字呈现自相矛盾');
  check('角标文案与实际行为用同一个判断（短文本不会标成"逐字呈现"）',
        /typingEligible\(/.test(chatJs) && /typingEligible\(full\)/.test(chatJs),
        '角标和渲染各写了一套条件 —— 短文本会标着逐字呈现却整段出现');

  // ── J. 删除历史对话 ──
  //
  //  ★ 删除要问确认，而"确认框写了什么"是有产品含义的：
  //    这次删除是真的删（消息 / 会话 / 图状态 / 工单里的对话快照），
  //    但**审查审计记录按合规要求保留**。所以确认框必须把"删什么、留什么"
  //    逐条说清 —— 只写"确定删除？"会让人不敢点，写成"全部彻底删除"
  //    又是误导（审计其实留着）。
  console.log('\nJ. 删除历史对话');
  const del = await bootPage(chatHtml, {
    token: TOKEN,
    local: { 'zhimei.currentSession': SID },
    routes: {
      '/api/health': { profile: 'fake', checkpointer: 'memory' },
      '/api/auth/me': { user: { display_name: '演示用户' } },
      '/api/graph': MINI_ARCH,
      '/api/sessions': { sessions: [{ session_id: SID, title: '要删掉的对话',
                                      ai_enabled: true, message_count: 3 }] },
      ['/api/sessions/' + SID]: { session: { session_id: SID, ai_enabled: true },
                                  messages: [{ role: 'user', content: '你好' }] },
    },
  });
  const ddoc = del.ctx && del.ctx.document;
  const convList = ddoc && ddoc.getElementById('convList');
  const rowEl = convList && convList.children[0];
  const delBtn = rowEl && rowEl.children.find((c) => /cv-del/.test(String(c.className)));
  check('会话列表每一行都有删除按钮', Boolean(delBtn),
        `行内元素：${rowEl && rowEl.children.map((c) => c.className)}`);
  if (delBtn && ddoc) {
    // ① 先点"取消" → 不该发请求
    //    ★ 弹窗已从原生 confirm 换成自建弹窗（window.dialogs），这里替换的是
    //      自建弹窗那一层入口。断言意图完全不变：取消不发请求、确认才发，
    //      且确认文案必须**逐条**说明删什么、留什么。
    let asked = "";
    del.ctx.dialogs.confirm = async (o) => { asked = JSON.stringify(o); return false; };
    // ★ 注意：开机时 afterLogin → switchTo 已经拉过一次会话详情了，
    //   所以这里要比**增量**，不能比"有没有出现过" —— 否则永远为真。
    const openCount = () => del.seen.filter(
      (u) => u.includes('/api/sessions/' + SID) && u.includes('limit=60')).length;
    const opensBefore = openCount();
    const before = del.seen.length;
    delBtn.onclick({ stopPropagation() {} });
    await new Promise((r) => setTimeout(r, 60));
    check('确认框里逐条说明了"删什么"和"留什么"',
          asked.includes("会删掉") && asked.includes("会保留（合规要求）"),
          `确认文案：${asked.slice(0, 80)}`);
    check('点删除不会连带触发"切换到该会话"（冒泡被挡住了）',
          openCount() === opensBefore,
          `会话详情请求从 ${opensBefore} 涨到 ${openCount()} —— 点击冒泡到了整行`);
    check('用户取消时**不发**删除请求', del.seen.length === before,
          `又发了 ${del.seen.length - before} 个请求`);

    // ② 点"确定" → 该发 DELETE
    del.ctx.dialogs.confirm = async () => true;
    delBtn.onclick({ stopPropagation() {} });
    await new Promise((r) => setTimeout(r, 60));
    check('确认后才真的发 DELETE 请求',
          del.seen.some((u) => u.includes('/api/sessions/' + SID)),
          `请求过：${[...new Set(del.seen)].slice(-4)}`);
  }
  check('删除调用的是 DELETE 方法（不是"标记隐藏"这类变通）',
        /api\("\/api\/sessions\/" \+ c\.session_id, \{method: "DELETE"\}\)/.test(
          stripComments(scriptOf(chatHtml))),
        '没有找到 DELETE 调用');

  // ── K. 弹窗一律自建 ────────────────────────────────────────────
  //   原生 alert / confirm / prompt 的问题：样式由浏览器决定、与整页视觉脱节
  //   且完全无法定制；confirm / alert 还**同步阻塞**页面；prompt 只有单行输入、
  //   做不了必填校验（坐席"关闭原因"是必填的，原生点确定给个空串也能过去）。
  //   两页统一改用自建弹窗 window.dialogs。
  console.log('\nK. 弹窗（一律自建，不用原生）');
  // ★ 负向后行断言把 dialogs.confirm(...) / dialogs.prompt(...) 这类**带点**的调用
  //   排除掉，否则自建弹窗自己会被误判成原生弹窗。
  //   Node 的 V8 支持后行断言（ripgrep 不支持，所以别指望用 rg 复核这条）。
  const NATIVE_DIALOG = /(?<![.\w$])(alert|confirm|prompt)\s*\(/;
  for (const [name, html] of [['chat.html', chatHtml], ['panel.html', panelHtml]]) {
    const body = stripComments(scriptOf(html));
    const hit = NATIVE_DIALOG.exec(body);
    check(`${name} 不再使用原生 alert / confirm / prompt`, !hit,
          hit ? `残留原生弹窗调用：${hit[0]}` : '');
    check(`${name} 定义了自建弹窗入口 window.dialogs`, /window\.dialogs\s*=/.test(body));
  }

  // ── L. 卡片可拉伸（拖分隔条改宽度）────────────────────────────
  //   分隔条占的就是栏间距本身：拖拽把手与视觉间距是同一个东西，
  //   不额外占空间，也不会让整页多出几条常驻竖线。
  console.log('\nL. 卡片可拉伸（拖分隔条改宽度）');
  const countSplitters = (h) => (h.match(/class="splitter"/g) || []).length;
  check('chat.html 四栏之间有 3 条分隔条', countSplitters(chatHtml) === 3,
        `实际 ${countSplitters(chatHtml)} 条`);
  check('panel.html 两栏之间有 1 条分隔条', countSplitters(panelHtml) === 1,
        `实际 ${countSplitters(panelHtml)} 条`);
  for (const [name, html] of [['chat.html', chatHtml], ['panel.html', panelHtml]]) {
    const flat = html.replace(/\s+/g, ' ');
    check(`${name} 分隔条可聚焦并声明 separator 语义（键盘也能用）`,
          /<div class="splitter"[^>]*role="separator"[^>]*tabindex="0"/.test(flat));
    check(`${name} 分隔条是 col-resize 拖拽光标`, /\.splitter\{[^}]*cursor:col-resize/.test(html));
    check(`${name} 堆叠/单栏模式下隐藏分隔条`, /\.splitter\{display:none\}/.test(html));
    // ★ 这条最关键：宽度必须走 CSS 变量。直接内联 grid-template-columns 会因为
    //   优先级高于媒体查询，把窄屏的堆叠布局永久钉死 —— 再也回不去。
    check(`${name} 宽度走 CSS 变量、不内联 grid-template-columns`,
          /var\(--col-1/.test(html) &&
          /setProperty\("--col-"/.test(html) &&
          !/style\.gridTemplateColumns/.test(html));
    check(`${name} 拖动用 pointer 事件且宽度持久化`,
          /addEventListener\("pointerdown"/.test(html) &&
          /addEventListener\("pointermove"/.test(html) &&
          /localStorage\.setItem\(SPLIT_STORE/.test(html));
  }

  console.log(`\n${'═'.repeat(56)}`);
  console.log(`通过 ${pass} 项，失败 ${fail} 项`);
  console.log('═'.repeat(56));
  process.exit(fail ? 1 : 0);
})();
