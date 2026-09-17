# Page Layouts (Shells)

There is no layout framework and no shared layout file. Each page is one self-contained `.html`
file, so a "layout" here is:

1. the literal shell markup in the page's `<body>`, and
2. the layout CSS in that page's inline `<style>` that positions it.

Two shells exist:

| Page | File | Shell |
|---|---|---|
| Chat UI | `Code/app/web/chat.html` | `header.topbar` → auth overlay → `.wrap` 3-column grid |
| Ops console | `Code/app/ops/panel.html` | `header.topbar` → auth overlay → `.metrics` strip → `.layout` 2-pane grid |

---

## 1. Chat UI shell — `Code/app/web/chat.html`

### 1.1 Full shell markup — lines 258–394

```html
<body>

<header class="topbar">
  <div class="brand">
    <span class="eyebrow">LANGGRAPH MULTI-AGENT</span>
    <b>智美医美顾问 · 在线咨询</b>
  </div>
  <div class="right">
    <span class="pill"><i class="dot" id="connDot"></i><span id="connText">未连接</span></span>
    <span class="pill" id="profilePill">档位 —</span>
    <label style="display:flex;align-items:center;gap:6px">
      <input type="checkbox" id="traceOn" checked> 显示执行轨迹
    </label>
    <span class="who" id="whoBox" hidden>
      <span class="pill" id="whoName">—</span>
      <button id="logout">退出</button>
    </span>
  </div>
</header>

<!-- ══════ 登录 / 注册 ══════ -->
<div class="auth" id="authLayer" hidden>
  <div class="auth-card">
    <h1>智美医美顾问</h1>
    <p class="sub">登录后即可开始咨询。您的对话只有自己能看见。</p>
    <div class="tabs">
      <button id="tabLogin" class="on">登录</button>
      <button id="tabReg">注册</button>
    </div>

    <form id="formLogin">
      <div class="field"><label>邮箱</label>
        <input id="loginEmail" type="email" autocomplete="username" placeholder="you@example.com"></div>
      <div class="field"><label>密码</label>
        <input id="loginPwd" type="password" autocomplete="current-password" placeholder="••••••••"></div>
      <button class="primary submit" type="submit">登录</button>
    </form>

    <form id="formReg" hidden>
      <div class="field"><label>邮箱</label>
        <input id="regEmail" type="email" autocomplete="username" placeholder="you@example.com"></div>
      <div class="field"><label>昵称（可空）</label>
        <input id="regName" maxlength="40" placeholder="怎么称呼您"></div>
      <div class="field"><label>密码（至少 10 位）</label>
        <input id="regPwd" type="password" autocomplete="new-password" placeholder="••••••••"></div>
      <button class="primary submit" type="submit">注册</button>
    </form>

    <div class="auth-msg" id="authMsg"></div>
    <div class="auth-tip">
      演示账号：<code>demo@zhimei.test</code> / <code>zhimei-demo-2026</code><br>
      本项目没有邮件服务，注册后验证令牌会**直接返回**，页面上会显示验证按钮。
    </div>
  </div>
</div>

<div class="wrap">
  <!-- ══════ 左：会话列表 ══════ -->
  <aside class="card convs">
    <h2>对话 <button id="newChat">＋ 新对话</button></h2>
    <div class="conv-list" id="convList">
      <div class="conv-empty">还没有历史对话。<br>发第一条消息就会出现在这里。</div>
    </div>
  </aside>

  <!-- ══════ 中：对话 ══════ -->
  <section class="card chat">
    <h2>对话 <span class="sub" id="sidLabel">新对话</span></h2>
    <div class="messages" id="messages"></div>
    <div class="statusbar" id="statusbar"><i class="spin"></i><span id="statusText"></span></div>
    <!-- 人工接管提示条：输入框**不禁用**（用户仍可补充情况），只是 AI 不回答 -->
    <div class="takeover-bar" id="takeoverBar" hidden>
      <span class="tk-badge">人工接管</span><span id="takeoverText"></span>
    </div>
    <div class="composer">
      <div class="row">
        <textarea id="input" placeholder="说说你想了解的项目，或要办的事…（Enter 发送，Shift+Enter 换行）"></textarea>
        <button class="primary" id="send">发送</button>
      </div>
      <div class="examples" id="examples">
        <span>试试：</span>
      </div>
    </div>
  </section>

  <!-- ══════ 右：执行轨迹 + 工作流架构（同屏，各自独立滚动）══════ -->
  <aside class="card trace">
    <h2>Agent 执行轨迹 <span class="sub" id="traceMeta">等待提问</span></h2>

    <!--
      ★ 两个视图**同屏展示**，取消原来的一栏切换。
        理由：它们回答的是两个不同的问题，而且看的时候往往是**同时**看 ——
        "这一步怎么走的"（轨迹）和"整条路走到哪了"（架构）。
        做成切换的话，每次对照都要点一下、还得记住刚才那一屏，
        等于把一份信息拆成两半让人自己拼。
      ★ 但**分成两个独立窗口**：各自有自己的标题栏与滚动条，
        互不干扰（滚架构不会把轨迹滚跑）。
      ★ 架构窗口给固定高度、轨迹吃掉剩下的：轨迹条数不定、需要更多空间，
        而架构是固定的一张图，高度够看就行。
    -->
    <div class="pane" id="paneGraph">
      <div class="pane-head">
        <span>工作流架构 <span class="sub" id="gSummaryInline">等待提问</span></span>
        <span class="pane-tools">
          <button id="gFit" title="缩放到适应宽度">适应</button>
          <button id="gZoomIn">＋</button>
          <button id="gZoomOut">－</button>
          <button id="gClear" title="清空本次高亮">清空</button>
        </span>
      </div>
      <!-- "本次对话经过了哪些层 / 哪些子图" -->
      <div class="gsummary" id="gSummary"></div>
      <div class="gwrap" id="gWrap">
        <svg id="gSvg" xmlns="http://www.w3.org/2000/svg"></svg>
      </div>
    </div>

    <div class="pane" id="paneTrace">
      <div class="pane-head"><span>执行轨迹</span></div>
      <div class="legend">
        <span><i style="background:var(--g-main)"></i>主图</span>
        <span><i style="background:var(--g-kb)"></i>科普子图</span>
        <span><i style="background:var(--g-risk)"></i>风险审查子图</span>
        <span><i style="background:#fff4dd;border:1px solid #e6d3a8"></i>会调用大模型（烧 token）</span>
      </div>
    </div>
    <ol class="nodes" id="nodes"></ol>
    <div class="trace-foot" id="traceFoot" style="display:none"></div>
    <div class="hint" id="traceHint">
      提问后，这里会按顺序实时列出图里真正执行过的节点（含子图内部节点）。
      耗时是相邻两次事件之间的间隔。<b style="color:#8a5a1e">点任意节点可展开它的输出。</b><br>
      <b style="color:#8a5a1e">★ 这是调试视图</b>：展开后能看到节点写回的原始状态，
      其中可能包含<b>未经审查的中间产物</b>（草稿、审查意见、检索到的原文）。
      顾客只应看到对话区里那份「已审正文」。<b>面向真实顾客时必须关掉这个勾选框。</b>
    </div>
  </aside>
</div>

<script>
```

### 1.2 Layout CSS — `Code/app/web/chat.html` lines 13–43, 132–139

```css
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);
     font:14px/1.7 "Microsoft YaHei","PingFang SC",-apple-system,sans-serif}
a,button{cursor:pointer;font:inherit}
code,.mono{font-family:Consolas,"SF Mono",Menlo,monospace}

/* ── 顶栏 ── */
.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;
        padding:12px 22px;background:#fff;border-bottom:1px solid var(--line)}
.brand{display:flex;flex-direction:column;line-height:1.25}
.brand .eyebrow{font:11px Consolas,monospace;color:var(--accent);letter-spacing:1.6px}
.brand b{font-size:16px;letter-spacing:.3px}
.topbar .right{display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--muted)}
.pill{border:1px solid var(--line);border-radius:999px;padding:3px 10px;background:#fbfcfd}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#c3ced5;margin-right:5px}
.dot.on{background:var(--ok)}
.dot.busy{background:var(--warn);animation:pulse 1s infinite}
@keyframes pulse{50%{opacity:.3}}

/* ── 布局：会话列表 | 对话 | 执行轨迹 ── */
.wrap{display:grid;
      grid-template-columns:236px minmax(360px,1fr) minmax(330px,.92fr);
      gap:16px;max-width:1660px;margin:16px auto;padding:0 22px 28px;align-items:start}
@media(max-width:1360px){.wrap{grid-template-columns:236px minmax(360px,1fr)} .trace{display:none}}
@media(max-width:900px){.wrap{grid-template-columns:1fr} .convs{display:none}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      display:flex;flex-direction:column;overflow:hidden}
.card>h2{margin:0;padding:12px 18px;font-size:14px;border-bottom:1px solid var(--line);
         background:#fbfcfd;display:flex;justify-content:space-between;align-items:center;gap:10px}
.card>h2 .sub{font:12px Consolas,monospace;color:var(--muted);font-weight:400}
```

```css
.trace{height:calc(100vh - 132px);min-height:560px;display:flex;flex-direction:column}

/* 右栏两个**独立窗口**：架构在上（固定高度）、轨迹在下（吃掉剩余空间）。
   各自有自己的标题栏和滚动条 —— 滚架构不会把轨迹滚跑，反之亦然。 */
.pane{display:flex;flex-direction:column;min-height:0}
#paneGraph{flex:0 0 auto;height:46%;border-bottom:2px solid var(--line)}
#paneTrace{flex:1 1 auto}
```

### 1.3 Layout model

```
body                                   grid: --bg #f3f6f8
├── header.topbar                      flex row, space-between, padding 12px 22px, white, 1px bottom border
└── .wrap                              grid, gap 16px, max-width 1660px, margin 16px auto, padding 0 22px 28px
    ├── aside.card.convs               236px fixed; height calc(100vh - 132px), min-height 560px
    ├── section.card.chat              minmax(360px, 1fr); same height rule
    │   ├── h2.card header
    │   ├── .messages                  flex:1, overflow-y auto, column flex, gap 12px
    │   ├── .statusbar                 display:none → .show flex
    │   ├── .takeover-bar              [hidden] by default
    │   └── .composer                  border-top, own padding
    └── aside.card.trace               minmax(330px, .92fr); column flex
        ├── h2.card header
        ├── .pane#paneGraph            flex 0 0 auto, height 46%
        │   ├── .pane-head             + .pane-tools buttons
        │   ├── .gsummary              chip summary
        │   └── .gwrap                 flex:1, overflow auto → svg#gSvg
        ├── .pane#paneTrace            flex 1 1 auto (head + .legend only)
        ├── ol.nodes                   flex:1, overflow-y auto
        ├── .trace-foot                display:none until a round finishes
        └── .hint
```

Breakpoints: `<1360px` drops the trace column entirely (`.trace{display:none}`);
`<900px` collapses to one column and hides `.convs`.

Card chrome is a single shared rule: `.card` = white surface + `1px solid var(--line)` +
`border-radius:12px` + `display:flex;flex-direction:column;overflow:hidden`, with `.card>h2` as the
shared section header (grey `#fbfcfd` strip, bottom border, `.sub` monospace right-hand label).
Both side columns and the centre column are the same `.card` with a different width track, and each
column sets its own `height:calc(100vh - 132px);min-height:560px`.

---

## 2. Ops console shell — `Code/app/ops/panel.html`

### 2.1 Full shell markup — lines 113–175

```html
<body>
<header class="topbar">
  <div class="brand">
    <span class="eyebrow">ZHIMEI / OPS CONSOLE</span>
    <b>坐席工作台 · 高风险回复处理</b>
  </div>
  <div class="tools">
    <!-- ★ 这里原来是一个「角色」下拉框（service / doctor / compliance）。
         那是一个真实的安全洞：角色由**客户端自己声明**，改个下拉框就能拿到
         未脱敏的手机号。现在角色来自登录令牌 + 数据库，下面只做**显示**。 -->
    <span class="whoami" id="whoami" hidden>
      <b id="agentName">—</b>
      <span class="role-badge" id="agentRole">—</span>
      <span class="raw-flag" id="rawFlag" hidden>可见明文 PII</span>
    </span>
    <span><i class="dot" id="connDot"></i> <span id="connText">未连接</span></span>
    <!-- 测试工单（channel=test）默认不进队列；要看时勾这里，行上带"测试"徽章 -->
    <label class="testtoggle" title="测试工单默认不进队列。勾上才会显示，行上会标「测试」">
      <input type="checkbox" id="showTest"> 显示测试工单
    </label>
    <button id="purgeTest" hidden
            title="只清理带 is_test 标记的工单，真实工单不受影响">清理测试工单</button>
    <button id="refresh">刷新</button>
    <button id="logout" hidden>退出</button>
  </div>
</header>

<!-- ══════ 坐席登录 ══════ -->
<div class="auth" id="authLayer" hidden>
  <form class="auth-card" id="loginForm">
    <h1>坐席登录</h1>
    <p class="sub">角色由后台账号决定，登录后自动生效。<br>
      演示账号见 <code>README</code>；四个角色各一个，口令相同。</p>
    <div class="field"><label>邮箱</label>
      <input id="loginEmail" type="email" autocomplete="username" required></div>
    <div class="field"><label>密码</label>
      <input id="loginPwd" type="password" autocomplete="current-password" required></div>
    <button class="primary" type="submit" id="loginBtn">登录</button>
    <div class="auth-msg" id="authMsg"></div>
    <div class="auth-tip">
      客服 <code>service@zhimei.test</code> · 医师 <code>doctor@zhimei.test</code><br>
      合规 <code>compliance@zhimei.test</code> · 管理员 <code>admin@zhimei.test</code><br>
      口令均为 <code>zhimei-demo-2026</code>
    </div>
  </form>
</div>

<div class="metrics" id="metrics"></div>

<div class="layout">
  <section class="pane">
    <h2>待处理队列 <span class="sub" id="queueCount"></span></h2>
    <div class="pane-body" id="queue"><div class="empty">加载中…</div></div>
  </section>

  <section class="pane">
    <h2>工单详情 <span class="sub" id="detailTitle"></span></h2>
    <div class="pane-body" id="detail"><div class="empty">从左侧选择一个工单</div></div>
  </section>
</div>

<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script>
```

### 2.2 Layout CSS — `Code/app/ops/panel.html` lines 10–46, 110

```css
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:14px/1.7 "Microsoft YaHei","PingFang SC",sans-serif}
a,button{cursor:pointer;font:inherit}
.topbar{position:sticky;top:0;z-index:10;display:flex;flex-wrap:wrap;gap:12px 20px;
        align-items:center;justify-content:space-between;padding:12px 24px;
        background:#fff;border-bottom:1px solid var(--line)}
.brand{display:flex;flex-direction:column;line-height:1.3}
.brand .eyebrow{font:11px Consolas,monospace;color:var(--accent);letter-spacing:1.5px}
.brand b{font-size:15px}
.tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
select,input,textarea{font:inherit;padding:7px 10px;border:1px solid var(--line);
                      border-radius:6px;background:#fff;color:var(--ink)}
textarea{width:100%;min-height:84px;resize:vertical;line-height:1.6}
button{border:1px solid var(--line);background:#fff;border-radius:6px;padding:7px 13px;color:var(--ink)}
button:hover{background:#e8f2f0;border-color:#75a69a}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover{background:#1b554e}
button.danger{border-color:#d99a8c;color:#8d3024}
button:disabled{opacity:.45;cursor:not-allowed}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--p2)}
.dot.on{background:var(--ok)}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;
         max-width:1560px;margin:16px auto 0;padding:0 24px}
.metric{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.metric b{display:block;font-size:20px;letter-spacing:-.5px}
.metric span{color:var(--muted);font-size:12px}
.layout{display:grid;grid-template-columns:minmax(320px,1fr) minmax(420px,1.35fr);gap:18px;
        max-width:1560px;margin:18px auto;padding:0 24px 48px}
.pane{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;
      display:flex;flex-direction:column;min-height:520px}
.pane>h2{margin:0;padding:14px 18px;font-size:15px;border-bottom:1px solid var(--line);
         background:#fbfcfd;display:flex;justify-content:space-between;align-items:center}
.pane-body{overflow:auto;flex:1}
```

```css
@media(max-width:1000px){.layout{grid-template-columns:1fr}}
```

### 2.3 Layout model

```
body                                    --bg #f3f6f8
├── header.topbar                       position:sticky, top 0, z-index 10, flex-wrap, padding 12px 24px
└── (after login)
    ├── div.metrics                     grid auto-fit minmax(140px,1fr), gap 10px, max-width 1560px
    │   └── div.metric × 7              value (20px bold) + label (12px muted)
    ├── div.layout                      grid, 2 cols, gap 18px, max-width 1560px, padding 0 24px 48px
    │   ├── section.pane  (queue)        minmax(320px, 1fr); min-height 520px
    │   │   ├── h2 + span.sub#queueCount
    │   │   └── .pane-body#queue         overflow:auto, flex:1
    │   └── section.pane  (detail)       minmax(420px, 1.35fr)
    │       ├── h2 + span.sub#detailTitle
    │       └── .pane-body#detail
    └── div.toast#toast                  position:fixed, right/bottom 20px, z-index 20
```

Note `.pane` is used in **both** pages but means different things: in `chat.html` `.pane` is an
unstyled-surface flex column inside the right card (`#paneGraph` / `#paneTrace`), while in
`panel.html` `.pane` is the bordered white card itself (the same role `.card` plays in `chat.html`).
The chat page's 3-column `.wrap` has no analogue in the ops page; the ops page instead stacks a
full-width metric strip above a 2-column `.layout`.

### 2.4 Dynamic boot shell state

Both shells open in an "unauthenticated" state driven by inline JS on load: `#authLayer` is shown
and the identity elements are hidden until a token validates.

`Code/app/ops/panel.html` lines 649–687:

```js
// ★ 先验令牌再干活。没登录就去拉队列只会拿到 401 再被弹回登录页，
//   用户会看到界面闪一下才跳走。
//
// ★ 关键约定：**只有 401 / 403 才算"没登录"**。其它一切异常（网络不通、
//   500、响应不是 JSON、启动过程里某个函数拼错）都保留令牌并如实报出来。
//   这条约定是踩出来的：原来这里用一个大 try/catch 包住"校验 + 全部初始化"，
//   结果 `bootData()` 里一个拼错的函数名让每次刷新都跳登录页，
//   而排查方向被"登录"两个字带偏了很久。
(async function start() {
  const t = localStorage.getItem("zhimei.agentToken");
  if (!t) { showAuth(); return; }

  let r;
  try {
    r = await fetch("/ops/auth/me", {headers: {Authorization: "Bearer " + t}});
  } catch (e) {
    keepTokenBoot("连不上服务（" + e.message + "）");
    return;
  }
  if (r.status === 401 || r.status === 403) {
    localStorage.removeItem("zhimei.agentToken");
    showAuth("登录已过期，请重新登录");
    return;
  }
  if (!r.ok) { keepTokenBoot("服务返回 HTTP " + r.status); return; }

  let d;
  try {
    d = await r.json();
  } catch (e) {
    keepTokenBoot("响应不是合法 JSON（" + e.message + "）");
    return;
  }
  me = d.agent;
  myPerms = d.permissions || [];
  applyIdentity();
  hideAuth();
  await bootData();
})();
```

`Code/app/web/chat.html` lines 1717–1762:

```js
// ★ 先验令牌、再决定进哪个界面。
//   顺序不能反：没登录就去拉会话列表，只会拿到 401 再被弹回登录页 ——
//   用户会看到界面闪一下才跳走。
//
// ★ 和坐席台同一条约定：**只有 401 / 403 才算"没登录"**。
//   原来这里是 `if (!r.ok) { setToken(""); showAuth("请先登录"); }` ——
//   后端一个 500 也会被当成"请先登录"，把好端端的令牌删掉。
(async function boot() {
  const t = getToken();
  if (!t) { showAuth(); return; }

  let r;
  try {
    r = await fetch("/api/auth/me", {headers: {Authorization: "Bearer " + t}});
  } catch (e) {
    // 连不上 ≠ 没登录：令牌留着，连上以后刷新即可恢复
    setConn("", "离线");
    hideAuth();
    addMsg("err", "连不上服务（" + e.message + "）。令牌已保留，恢复后刷新页面即可。");
    return;
  }
  if (r.status === 401 || r.status === 403) {
    setToken("");
    showAuth("登录已过期，请重新登录");
    return;
  }
  if (!r.ok) {
    setConn("", "离线");
    hideAuth();
    addMsg("err", "服务返回 HTTP " + r.status + "，令牌已保留，稍后刷新重试。");
    return;
  }

  let d;
  try {
    d = await r.json();
  } catch (e) {
    setConn("", "离线");
    hideAuth();
    addMsg("err", "响应不是合法 JSON：" + e.message);
    return;
  }
  currentUser = d.user;
  hideAuth();
  await afterLogin();
})();
```

---

## 3. Serving layer (how a shell reaches the browser)

Neither shell is served from a static mount; each is read from disk and returned as an
`HTMLResponse` by its own FastAPI route, with caching disabled so edits show up on refresh.

`Code/app/api/app.py` lines 28–29, 52–67:

```python
#: 单文件页面目录（C 端聊天页与运营面板一样，无构建步骤、由 FastAPI 直接托管）
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
```

```python
    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    async def chat_ui() -> HTMLResponse:
        """C 端聊天页（单文件，无构建步骤）。

        页面默认带 ?trace=1 请求节点执行轨迹 —— 那是给**演示与排障**用的。
        真实线上要对顾客隐藏，前端把「显示执行轨迹」勾掉即可
        （后端默认就是不发 node 事件的，见 events.EVENT_NODE）。

        ★ `Cache-Control: no-store` 是必须的，不是可选的。
          这两个单文件页面是**边改边用**的：没有这个头，浏览器会按启发式规则
          缓存住旧版本 —— 于是改了前端、刷新页面却还是老行为，
          排查方向会被带偏到"是不是没生效/是不是后端没重启"。
          实测踩到过：修好了一个前端 bug，用户刷新后说"还是老样子"。
        """
        return HTMLResponse((_WEB_DIR / "chat.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store, must-revalidate"})
```

`Code/app/ops/routes.py` lines 38–40, 341–347:

```python
router = APIRouter(prefix="/ops")

PANEL_FILE = Path(__file__).parent / "panel.html"
```

```python
@router.get("/panel", response_class=HTMLResponse)
async def panel() -> HTMLResponse:
    if not PANEL_FILE.exists():
        return HTMLResponse("<h1>panel.html 缺失</h1>", status_code=500)
    # 同 /chat：单文件页面边改边用，必须禁掉缓存，否则改了前端刷新还是旧行为
    return HTMLResponse(PANEL_FILE.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store, must-revalidate"})
```

Both page files are shipped as package data — `Code/pyproject.toml`:

```toml
app = ["config/*.yaml", "ops/*.html", "web/*.html"]
```
