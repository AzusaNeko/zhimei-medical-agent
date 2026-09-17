# Shared UI Components

This project has **no component framework** — no React/Vue/Svelte, no npm, no build step, no
CSS modules, no Tailwind. Each page is one self-contained `.html` file with an inline `<style>`
and an inline `<script>`.

"Component" below therefore means one of two real things:

1. a **CSS class family** defined in the page's inline `<style>`, and/or
2. a **DOM region built by the inline JS** (or the literal markup that mirrors it).

Source files:
- `Code/app/web/chat.html` — customer chat UI (1765 lines: `<style>` 7–256, markup 258–394, `<script>` 396–1763)
- `Code/app/ops/panel.html` — ops console UI (689 lines: `<style>` 7–111, markup 113–175, `<script>` 176–688)

Every code block below is the **actual source**, copied verbatim. Chinese UI strings are preserved exactly.

---

## 1. Topbar / AppHeader

Two variants. The chat variant is a static flex bar; the ops variant is `position:sticky` and holds
the identity block, connection dot, test-ticket toggle and action buttons.

### CSS — `Code/app/web/chat.html` lines 20–31, 254–255

```css
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
```

```css
.topbar .who{display:flex;align-items:center;gap:8px}
.topbar .who button{font-size:12.5px;padding:4px 10px;border-radius:999px}
```

### Markup — `Code/app/web/chat.html` lines 260–276

```html
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
```

### Connection-state JS — `Code/app/web/chat.html` lines 917–921, 1712–1715

```js
/* ── 顶栏状态 ── */
function setConn(state, text) {
  $("connDot").className = "dot" + (state ? " " + state : "");
  $("connText").textContent = text;
}
```

```js
fetch("/api/health").then((r) => r.json()).then((h) => {
  $("profilePill").textContent = "档位 " + h.profile + " · " + h.checkpointer;
  setConn("on", "已连接");
}).catch(() => setConn("", "离线"));
```

### CSS — `Code/app/ops/panel.html` lines 14–31, 97–103

```css
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
```

```css
/* 顶栏身份显示 */
.whoami{display:flex;align-items:center;gap:7px;font-size:12.5px}
.role-badge{font:11px Consolas,monospace;padding:2px 8px;border-radius:4px;
            background:#e8f1fb;color:#17405f}
.raw-flag{font:11px Consolas,monospace;padding:2px 8px;border-radius:4px;
          background:#ffe3de;color:#8a2a1e}
.testtoggle{display:flex;align-items:center;gap:5px;font-size:12.5px;color:var(--muted)}
```

### Markup — `Code/app/ops/panel.html` lines 114–138

```html
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
```

### Identity-fill JS — `Code/app/ops/panel.html` lines 250–260

```js
function applyIdentity() {
  const role = (me && me.role) || "?";
  document.getElementById("agentName").textContent =
    (me && me.name) || (me && me.agent_id) || "—";
  document.getElementById("agentRole").textContent = role;
  // 一眼看出当前身份能不能看到明文 PII —— 这是坐席最需要心里有数的一件事
  document.getElementById("rawFlag").hidden = !["doctor", "compliance", "admin"].includes(role);
  // 清理测试工单是不可逆操作，只给有权限的角色（服务端也会再判一次）
  document.getElementById("purgeTest").hidden = !myPerms.includes("admin:purge");
  document.title = `坐席工作台 · ${role}`;
}
```

---

## 2. AuthOverlay / AuthCard

Modal login overlay, shared in shape between both pages; the chat one has a Login/Register tab pair,
the ops one is a single login form with a demo-credentials tip block.

### CSS — `Code/app/web/chat.html` lines 231–253

```css
/* ── 登录 / 注册浮层 ── */
.auth{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;
      background:rgba(23,47,64,.45);backdrop-filter:blur(3px)}
.auth[hidden]{display:none}
.auth-card{width:min(420px,92vw);background:#fff;border-radius:14px;padding:26px 28px 22px;
           box-shadow:0 18px 60px rgba(12,30,42,.28)}
.auth-card h1{margin:0 0 4px;font-size:19px}
.auth-card .sub{margin:0 0 18px;font-size:12.5px;color:var(--muted);line-height:1.6}
.tabs{display:flex;gap:6px;margin-bottom:16px;border-bottom:1px solid var(--line)}
.tabs button{border:none;background:none;padding:8px 14px;font-size:13.5px;color:var(--muted);
             border-bottom:2px solid transparent;border-radius:0}
.tabs button.on{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.field{display:flex;flex-direction:column;gap:5px;margin-bottom:12px}
.field label{font-size:12.5px;color:var(--muted)}
.field input{padding:9px 11px;border:1px solid var(--line);border-radius:8px;font:inherit}
.field input:focus{outline:none;border-color:#8fbfb3;box-shadow:0 0 0 3px #e8f2f0}
.auth-card .submit{width:100%;padding:11px;margin-top:4px;font-size:14.5px}
.auth-msg{min-height:20px;font-size:12.5px;margin-top:10px;line-height:1.6}
.auth-msg.err{color:var(--bad)}
.auth-msg.ok{color:var(--ok)}
.auth-tip{margin-top:14px;padding:9px 11px;background:#f7faf9;border:1px solid #e3ecea;
          border-radius:8px;font-size:12px;color:var(--muted);line-height:1.7}
.auth-tip code{color:var(--accent)}
```

### Markup — `Code/app/web/chat.html` lines 279–312

```html
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
```

### Tab-switch + show/hide JS — `Code/app/web/chat.html` lines 546–556, 642–649

```js
function showAuth(msg) {
  $("authLayer").hidden = false;
  $("whoBox").hidden = true;
  $("authMsg").className = "auth-msg" + (msg ? " err" : "");
  $("authMsg").textContent = msg || "";
}

function hideAuth() {
  $("authLayer").hidden = true;
  $("authMsg").textContent = "";
}
```

```js
function switchTab(which) {
  const isLogin = which === "login";
  $("tabLogin").classList.toggle("on", isLogin);
  $("tabReg").classList.toggle("on", !isLogin);
  $("formLogin").hidden = !isLogin;
  $("formReg").hidden = isLogin;
  $("authMsg").textContent = "";
}
```

### CSS — `Code/app/ops/panel.html` lines 80–96

```css
/* ── 登录浮层 ── */
.auth{position:fixed;inset:0;z-index:60;display:flex;align-items:center;justify-content:center;
      background:rgba(23,47,64,.5);backdrop-filter:blur(3px)}
.auth[hidden]{display:none}
.auth-card{width:min(430px,92vw);background:#fff;border-radius:14px;padding:26px 28px 20px;
           box-shadow:0 18px 60px rgba(12,30,42,.3)}
.auth-card h1{margin:0 0 6px;font-size:19px}
.auth-card .sub{margin:0 0 18px;font-size:12.5px;color:var(--muted);line-height:1.7}
.auth-card .field{display:flex;flex-direction:column;gap:5px;margin-bottom:12px}
.auth-card .field label{font-size:12.5px;color:var(--muted)}
.auth-card .field input{padding:9px 11px;border:1px solid var(--line);border-radius:8px;font:inherit}
.auth-card .field input:focus{outline:none;border-color:#8fbfb3;box-shadow:0 0 0 3px #e8f2f0}
.auth-card button{width:100%;padding:11px;font-size:14.5px;margin-top:4px}
.auth-msg{min-height:20px;font-size:12.5px;margin-top:10px;line-height:1.6;color:var(--bad)}
.auth-tip{margin-top:14px;padding:9px 11px;background:#f7faf9;border:1px solid #e3ecea;
          border-radius:8px;font-size:11.5px;color:var(--muted);line-height:1.8}
.auth-tip code{color:var(--accent)}
```

> Note: `panel.html` uses `color:var(--bad)` in `.auth-msg` but never defines `--bad` in its `:root`.
> The declaration is invalid at computed-value time and the color falls back to inherit.

### Markup — `Code/app/ops/panel.html` lines 140–158

```html
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
```

---

## 3. Button family

No `.btn` class — bare `button` is the base, with `.primary` / `.danger` variants and a `:disabled` state.

`Code/app/web/chat.html` lines 120–124:

```css
button{border:1px solid var(--line);background:#fff;border-radius:8px;padding:9px 15px;color:var(--ink)}
button:hover:not(:disabled){background:#eef5f3;border-color:#8fbfb3}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover:not(:disabled){background:var(--accent2)}
button:disabled{opacity:.45;cursor:not-allowed}
```

`Code/app/ops/panel.html` lines 24–29 (smaller padding, 6px radius, plus `.danger`):

```css
button{border:1px solid var(--line);background:#fff;border-radius:6px;padding:7px 13px;color:var(--ink)}
button:hover{background:#e8f2f0;border-color:#75a69a}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover{background:#1b554e}
button.danger{border-color:#d99a8c;color:#8d3024}
button:disabled{opacity:.45;cursor:not-allowed}
```

Pill button variant used inside the conversation-list header and topbar, `chat.html` lines 48–49:

```css
.convs>h2 button{font-size:12.5px;padding:5px 11px;border-radius:999px;background:#eef5f3;
                 border-color:#8fbfb3;color:var(--accent);white-space:nowrap}
```

---

## 4. ConversationListItem

Left-column history row, built entirely by `renderConversations()`. Active state, unread metadata,
an AI-takeover badge, and an on-hover-only delete button whose click is stopped from bubbling into
the row's switch handler.

### CSS — `Code/app/web/chat.html` lines 45–70

```css
/* ── 会话列表 ── */
.convs{height:calc(100vh - 132px);min-height:560px}
.convs>h2{padding:10px 12px 10px 16px}
.convs>h2 button{font-size:12.5px;padding:5px 11px;border-radius:999px;background:#eef5f3;
                 border-color:#8fbfb3;color:var(--accent);white-space:nowrap}
.conv-list{flex:1;overflow-y:auto;padding:6px 0}
.conv{padding:9px 16px;border-bottom:1px solid #eef2f5;cursor:pointer;
      border-left:3px solid transparent;position:relative}
.conv:hover{background:#f4f9f8}
.conv.active{background:#eaf4f2;border-left-color:var(--accent)}
/* 删除按钮：默认半透明，鼠标移到这一行才明显 —— 免得列表看着像一排叉 */
.cv-del{position:absolute;top:7px;right:10px;width:20px;height:20px;padding:0;
        border:1px solid transparent;border-radius:5px;background:transparent;
        color:var(--muted);font-size:12px;line-height:1;cursor:pointer;opacity:0;
        transition:opacity .12s}
.conv:hover .cv-del{opacity:.65}
.cv-del:hover{opacity:1;background:#ffe3de;color:#8a2a1e;border-color:#f0c9c2}
.cv-title{font-size:13px;line-height:1.45;overflow:hidden;text-overflow:ellipsis;
          white-space:nowrap;padding-right:26px}   /* 让开右侧的删除按钮 */
.conv.unread .cv-title{color:var(--muted)}
.cv-meta{font:11px Consolas,monospace;color:var(--muted);margin-top:3px;
         display:flex;gap:8px;align-items:center}
.cv-badge{font:10px Consolas,monospace;padding:1px 5px;border-radius:3px;
          background:#e8f1fb;color:#17405f}
.cv-badge.off{background:#ffe3de;color:#8a2a1e}
.conv-empty{color:var(--muted);font-size:12.5px;padding:18px 16px;line-height:1.7}
```

### Markup shell — `Code/app/web/chat.html` lines 316–321

```html
<!-- ══════ 左：会话列表 ══════ -->
<aside class="card convs">
  <h2>对话 <button id="newChat">＋ 新对话</button></h2>
  <div class="conv-list" id="convList">
    <div class="conv-empty">还没有历史对话。<br>发第一条消息就会出现在这里。</div>
  </div>
</aside>
```

### DOM builder — `Code/app/web/chat.html` lines 687–739

```js
function relTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const d = (Date.now() - t) / 1000;
  if (d < 60) return "刚刚";
  if (d < 3600) return Math.floor(d / 60) + " 分钟前";
  if (d < 86400) return Math.floor(d / 3600) + " 小时前";
  if (d < 86400 * 2) return "昨天";
  const dt = new Date(t);
  return (dt.getMonth() + 1) + "-" + String(dt.getDate()).padStart(2, "0");
}

async function loadConversations() {
  try {
    const r = await api("/api/sessions?channel=web&limit=40");
    conversations = (await r.json()).sessions || [];
  } catch (e) {
    conversations = [];
  }
  renderConversations();
}

function renderConversations() {
  const box = $("convList");
  box.innerHTML = "";
  if (!conversations.length) {
    box.appendChild(el("div", "conv-empty",
      "还没有历史对话。\n发第一条消息就会出现在这里。"));
    return;
  }
  for (const c of conversations) {
    const item = el("div", "conv" + (c.session_id === sid ? " active" : ""));
    item.appendChild(el("div", "cv-title",
      c.title || "（新对话 · 还没说话）"));
    const meta = el("div", "cv-meta");
    meta.appendChild(el("span", null, relTime(c.last_active_at)));
    if (c.msg_count) meta.appendChild(el("span", null, c.msg_count + " 条"));
    if (c.ai_enabled === false) meta.appendChild(el("span", "cv-badge off", "人工接管"));
    item.appendChild(meta);

    // ── 删除这个对话 ──
    // ★ 按钮不放进 item.onclick 的冒泡里：否则点删除会先触发"切换到该会话"。
    //   用 stopPropagation 挡住。
    const del = el("button", "cv-del", "✕");
    del.title = "删除这个对话";
    del.onclick = (ev) => { ev.stopPropagation(); deleteConversation(c); };
    item.appendChild(del);

    item.onclick = () => switchTo(c.session_id);
    box.appendChild(item);
  }
}
```

### Helpers it depends on — `Code/app/web/chat.html` lines 475–481

```js
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
```

---

## 5. ChatMessageBubble

Four bubble roles (`.msg.user` / `.msg.bot` / `.msg.sys` / `.msg.err`), an optional `.meta` footer
carrying inline `.tag` chips, and the `.typing` caret used by the typewriter presentation.

### CSS — `Code/app/web/chat.html` lines 72–97

```css
/* ── 对话区 ── */
.chat{height:calc(100vh - 132px);min-height:560px}
.messages{flex:1;overflow-y:auto;padding:16px 18px;display:flex;flex-direction:column;gap:12px}
.msg{max-width:88%;padding:10px 14px;border-radius:12px;white-space:pre-wrap;word-break:break-word;
     animation:rise .18s ease-out}
@keyframes rise{from{opacity:0;transform:translateY(6px)}}
.msg.user{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:3px}
.msg.bot{align-self:flex-start;background:#f1f6f5;border:1px solid #dbe8e5;border-bottom-left-radius:3px}
.msg.sys{align-self:center;background:#fff8e8;border:1px solid #f0e0bb;color:#6d4c08;
         font-size:12.5px;max-width:96%;border-radius:8px;padding:9px 13px}
.msg.err{align-self:center;background:#ffeceb;border:1px solid #f3cfcb;color:#8a2a1e;
         font-size:12.5px;max-width:96%;border-radius:8px;padding:9px 13px}
.msg .meta{display:block;margin-top:7px;font:11px Consolas,monospace;color:var(--muted);
           letter-spacing:.2px}
.msg.user .meta{color:#cfe6e2}
.msg.bot .meta{color:var(--muted)}
/* 打字机呈现中的正文：光标闪一下，表示"还在显示"（内容是完整的，只是没显示完） */
.msg.typing{cursor:pointer}
.msg.typing::after{content:"▍";margin-left:1px;color:var(--accent);
                   animation:caret .9s steps(1) infinite}
@keyframes caret{50%{opacity:0}}
.tag{display:inline-block;font:11px Consolas,monospace;padding:1px 7px;border-radius:4px;
     margin-right:6px;background:#e8f1fb;color:#17405f}
.tag.ok{background:#e6f4ee;color:#1a5044}
.tag.warn{background:#fff4dd;color:#6d4c08}
.tag.bad{background:#ffe3de;color:#8a2a1e}
```

### Markup shell — `Code/app/web/chat.html` lines 324–327

```html
<!-- ══════ 中：对话 ══════ -->
<section class="card chat">
  <h2>对话 <span class="sub" id="sidLabel">新对话</span></h2>
  <div class="messages" id="messages"></div>
```

### Plain renderer — `Code/app/web/chat.html` lines 923–939

```js
/* ── 消息渲染 ── */
function addMsg(kind, text, meta) {
  const m = el("div", "msg " + kind);
  m.appendChild(document.createTextNode(text));
  if (meta) {
    const s = el("span", "meta");
    meta.forEach((t, i) => {
      if (i) s.appendChild(document.createTextNode("  "));
      if (typeof t === "string") s.appendChild(document.createTextNode(t));
      else { const b = el("span", "tag " + (t.cls || ""), t.text); s.appendChild(b); }
    });
    m.appendChild(s);
  }
  $("messages").appendChild(m);
  $("messages").scrollTop = $("messages").scrollHeight;
  return m;
}
```

### Typewriter renderer — `Code/app/web/chat.html` lines 962–1023

```js
const TYPE_MS = 900;          // 整段正文的目标呈现时长
const TYPE_MIN_CHUNK = 2;     // 每次至少吐几个字（太碎会像卡顿）
const TYPE_MIN_LEN = 24;      // 短文本不值得做动画（还没开始就结束了）

function prefersReducedMotion() {
  try {
    return window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (e) { return false; }
}

/** 这段文本会不会走打字机呈现。
 *  单独抽出来是为了让"角标文案"和"实际行为"用同一个判断 ——
 *  两处各写一遍的话，短文本会标着"逐字呈现"却整段出现。 */
function typingEligible(text) {
  return String(text || "").length >= TYPE_MIN_LEN && !prefersReducedMotion();
}

/** 追加一条"打字机"呈现的正文消息。返回节点。 */
function addMsgTyped(kind, text, meta) {
  const full = String(text || "");
  // 短文本、或系统要求减少动效 → 不做动画，直接整段（动画是锦上添花，不该添乱）
  if (!typingEligible(full)) return addMsg(kind, full, meta);

  const m = el("div", "msg " + kind);
  const node = document.createTextNode("");
  m.appendChild(node);
  m.classList.add("typing");
  if (meta) {
    const s = el("span", "meta");
    meta.forEach((t, i) => {
      if (i) s.appendChild(document.createTextNode("  "));
      if (typeof t === "string") s.appendChild(document.createTextNode(t));
      else { const b = el("span", "tag " + (t.cls || ""), t.text); s.appendChild(b); }
    });
    m.appendChild(s);
  }
  $("messages").appendChild(m);

  const tick = Math.max(16, Math.round(TYPE_MS / Math.ceil(full.length / TYPE_MIN_CHUNK)));
  let i = 0;
  let timer = null;
  const finish = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    node.data = full;
    m.classList.remove("typing");
    m.onclick = null;
    $("messages").scrollTop = $("messages").scrollHeight;
  };
  const step = () => {
    i = Math.min(full.length, i + TYPE_MIN_CHUNK);
    node.data = full.slice(0, i);
    $("messages").scrollTop = $("messages").scrollHeight;
    if (i >= full.length) { finish(); return; }
    timer = setTimeout(step, tick);
  };
  // 点一下直接看全文 —— 长文本不该强迫人等着
  m.onclick = finish;
  m.title = "点一下直接显示全文";
  timer = setTimeout(step, tick);
  return m;
}
```

### Role → bubble mapper (history/poll rendering) — `Code/app/web/chat.html` lines 806–814

```js
/** 按角色把一条落库消息画出来。首屏、增量追加、整体重画都走这一个函数，
 *  免得三套渲染逻辑各写一遍、然后慢慢长得不一样。 */
function msgNode(m) {
  const role = m.role || "";
  if (role === "user") addMsg("user", m.content || "");
  else if (role === "agent") addMsg("bot", m.content || "", [{text: "坐席", cls: "warn"}]);
  else if (role === "system") addMsg("sys", m.content || "");
  else addMsg("bot", m.content || "", [{text: "已审正文", cls: "ok"}]);
}
```

### Opening message — `Code/app/web/chat.html` lines 1689–1693

```js
function greeting() {
  addMsg("bot", "您好，我是智美医美的在线顾问。\n" +
    "可以问我项目区别、恢复期、门店与医生资质，也可以让我帮您改约。\n" +
    "涉及个人是否适合、具体方案与效果，需要面诊评估。", [{text: "开场白", cls: ""}]);
}
```

---

## 6. StatusBar & Spinner

Hidden-by-default progress strip pinned above the composer.

### CSS — `Code/app/web/chat.html` lines 99–105

```css
/* 状态条 */
.statusbar{display:none;align-items:center;gap:9px;padding:9px 18px;border-top:1px solid var(--line);
           background:#fbfcfd;font-size:13px;color:var(--muted)}
.statusbar.show{display:flex}
.spin{width:13px;height:13px;border:2px solid #cfdde3;border-top-color:var(--accent);
      border-radius:50%;animation:spin .7s linear infinite;flex:none}
@keyframes spin{to{transform:rotate(360deg)}}
```

### Markup — `Code/app/web/chat.html` line 327

```html
<div class="statusbar" id="statusbar"><i class="spin"></i><span id="statusText"></span></div>
```

### JS — `Code/app/web/chat.html` lines 1025–1028

```js
function setStatus(text) {
  $("statusbar").classList.toggle("show", !!text);
  if (text) $("statusText").textContent = text;
}
```

---

## 7. TakeoverBar

Human-agent takeover notice. The composer stays **enabled** during takeover — this bar only explains
that the AI is paused and the message will be routed to a human.

### CSS — `Code/app/web/chat.html` lines 107–112

```css
/* 人工接管提示条：输入框不禁用，只是明确告诉用户"AI 暂停、话会转给客服" */
.takeover-bar{display:flex;align-items:center;gap:8px;padding:8px 18px;
              border-top:1px solid var(--line);background:#fdf7ec;
              font-size:12.5px;color:#7a5a1e}
.tk-badge{flex:none;padding:2px 7px;border-radius:4px;background:#f6e2b8;color:#6b4a12;
          font-size:11.5px}
```

### Markup — `Code/app/web/chat.html` lines 328–331

```html
<!-- 人工接管提示条：输入框**不禁用**（用户仍可补充情况），只是 AI 不回答 -->
<div class="takeover-bar" id="takeoverBar" hidden>
  <span class="tk-badge">人工接管</span><span id="takeoverText"></span>
</div>
```

### State-sync JS — `Code/app/web/chat.html` lines 836–875

```js
function resetTakeoverUi() {
  syncTakeover(false, true);
}

function syncTakeover(taken, initial) {
  takeover = taken;
  $("takeoverBar").hidden = !taken;
  if (taken) {
    // ★ 措辞不写"人工已接入"：`ai_enabled=false` 时工单确实已被接单，
    //   但这句话还要覆盖"排队中"的情形，而项目里有一条硬约束 ——
    //   没接单就不能对用户承诺"人工已接入"。"处理中"两种情形都成立。
    $("takeoverText").textContent =
      "人工客服处理中：AI 已暂停自动回复，你发的消息会直接转给客服。";
    $("input").placeholder = "继续补充情况，客服会看到…";
  } else {
    $("input").placeholder = DEFAULT_PLACEHOLDER;
  }
  if (taken && !lastSeenTaken) {
    addMsg("sys", "该会话已由人工客服接管，AI 已暂停自动回复。"
           + "你仍然可以继续发言，消息会直接转给客服；客服结束接管后 AI 会自动恢复。",
           [{text: "人工接管", cls: "warn"}]);
  } else if (!taken && lastSeenTaken && !initial) {
    addMsg("sys", "人工客服已结束接管，AI 已恢复自动回复。",
           [{text: "AI 已恢复", cls: "ok"}]);
    setStatus("人工客服已结束接管，AI 已恢复自动回复");
    setTimeout(() => { if (!takeover) setStatus(""); }, 4000);
  }
  lastSeenTaken = taken;
}
```

---

## 8. Composer

Textarea + send button + example-question chip row + the inline `.confirm` action bar injected under
an `awaiting_confirmation` system message.

### CSS — `Code/app/web/chat.html` lines 114–130

```css
/* 输入区 */
.composer{border-top:1px solid var(--line);padding:12px 18px;background:#fff}
.composer .row{display:flex;gap:10px;align-items:flex-end}
textarea{flex:1;min-height:52px;max-height:150px;resize:vertical;padding:10px 12px;border-radius:9px;
         border:1px solid var(--line);font:inherit;color:var(--ink);line-height:1.6}
textarea:focus{outline:none;border-color:#8fbfb3;box-shadow:0 0 0 3px #e8f2f0}
button{border:1px solid var(--line);background:#fff;border-radius:8px;padding:9px 15px;color:var(--ink)}
button:hover:not(:disabled){background:#eef5f3;border-color:#8fbfb3}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover:not(:disabled){background:var(--accent2)}
button:disabled{opacity:.45;cursor:not-allowed}
.examples{display:flex;flex-wrap:wrap;gap:7px;padding:10px 18px 0;border-top:1px solid #eef2f5;
          margin-top:12px}
.examples span{font-size:12px;color:var(--muted);padding:3px 0}
.examples button{font-size:12.5px;padding:4px 11px;border-radius:999px;background:#f7faf9}
.confirm{display:flex;gap:9px;margin-top:10px}
.confirm button{font-size:13px;padding:7px 14px}
```

### Markup — `Code/app/web/chat.html` lines 332–340

```html
<div class="composer">
  <div class="row">
    <textarea id="input" placeholder="说说你想了解的项目，或要办的事…（Enter 发送，Shift+Enter 换行）"></textarea>
    <button class="primary" id="send">发送</button>
  </div>
  <div class="examples" id="examples">
    <span>试试：</span>
  </div>
</div>
```

### Example chips + send JS — `Code/app/web/chat.html` lines 467–473, 1664–1710

```js
const EXAMPLES = [
  "热玛吉和超声炮有什么区别？",
  "帮我把热玛吉的预约改到浦东店周五下午",
  "我做完水光第三天，现在脸发白还特别疼，眼睛也有点看不清",
  "那个怎么样",
  "你们的医生有执业资质吗？"
];
```

```js
/* ── 发送 ── */
async function send() {
  const text = $("input").value.trim();
  if (!text || busy) return;
  $("input").value = "";
  addMsg("user", text);
  clearTrace();
  busy = true;
  $("send").disabled = true;
  if (!sid) {
    // 会话是"懒创建"的：点「新对话」不建会话，发第一条消息时才建，
    // 免得库里堆一堆没说过话的空会话。
    const r = await api("/api/sessions", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({channel: "web"})
    });
    sid = (await r.json()).session_id;
    localStorage.setItem(LS_KEY, sid);
    $("sidLabel").textContent = "会话 " + sid.slice(0, 8) + "…";
  }
  await stream("/api/chat/" + sid + "/stream", {text: text});
  await loadConversations();      // 标题/时间/条数都变了
}
```

```js
EXAMPLES.forEach((t) => {
  const b = el("button", null, t.length > 18 ? t.slice(0, 17) + "…" : t);
  b.title = t;
  b.onclick = () => { $("input").value = t; send(); };
  $("examples").appendChild(b);
});
$("send").onclick = send;
$("newChat").onclick = newChat;
$("logout").onclick = logout;
$("tabLogin").onclick = () => switchTab("login");
$("tabReg").onclick = () => switchTab("reg");
$("formLogin").addEventListener("submit", doLogin);
$("formReg").addEventListener("submit", doRegister);
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
```

### Confirm bar — `Code/app/web/chat.html` lines 1029–1032, 1590–1616

```js
function clearConfirm() {
  document.querySelectorAll(".confirm").forEach((n) => n.remove());
  pendingPlan = null;
}
```

```js
    case "awaiting_confirmation": {
      pendingPlan = d;
      const m = addMsg("sys", "以下操作需要你确认后才会执行：\n\n" + (d.plan || ""), [
        {text: "等待确认", cls: "warn"},
        "方案哈希 " + String(d.plan_hash || "").slice(0, 12) + "…"
      ]);
      const bar = el("div", "confirm");
      const yes = el("button", "primary", "确认执行");
      const no = el("button", null, "取消");
      yes.onclick = () => {
        clearConfirm();
        addMsg("user", "确认执行");
        stream("/api/chat/" + sid + "/confirm",
               {confirmed: true, plan_hash: d.plan_hash});
      };
      no.onclick = () => {
        clearConfirm();
        addMsg("user", "取消");
        stream("/api/chat/" + sid + "/confirm",
               {confirmed: false, plan_hash: d.plan_hash});
      };
      bar.appendChild(yes);
      bar.appendChild(no);
      m.appendChild(bar);
      $("messages").scrollTop = $("messages").scrollHeight;
      return true;
    }
```

---

## 9. GraphPane (SVG workflow architecture view)

The right column's upper window: a head bar with zoom tools, a "本次经过" chip summary, and a
layered SVG DAG drawn from `GET /api/graph`. Includes its own layer / node / edge / edge-label CSS.

### CSS — `Code/app/web/chat.html` lines 132–186

```css
/* ── 轨迹区 ── */
.trace{height:calc(100vh - 132px);min-height:560px;display:flex;flex-direction:column}

/* 右栏两个**独立窗口**：架构在上（固定高度）、轨迹在下（吃掉剩余空间）。
   各自有自己的标题栏和滚动条 —— 滚架构不会把轨迹滚跑，反之亦然。 */
.pane{display:flex;flex-direction:column;min-height:0}
#paneGraph{flex:0 0 auto;height:46%;border-bottom:2px solid var(--line)}
#paneTrace{flex:1 1 auto}
.pane-head{display:flex;align-items:center;justify-content:space-between;gap:8px;
           padding:6px 14px;background:#f7fafc;border-bottom:1px solid #eef2f5;
           font-size:12px;color:var(--muted);font-weight:600}
.pane-tools{display:flex;gap:4px}
.pane-tools button{padding:2px 7px;font-size:11px;border:1px solid var(--line);
                   border-radius:5px;background:#fff;color:var(--muted);cursor:pointer}
.pane-tools button:hover{border-color:var(--accent);color:var(--accent)}
.glegend{display:flex;flex-wrap:wrap;gap:6px 12px;padding:7px 14px;font-size:11.5px;
         color:var(--muted);border-bottom:1px solid #eef2f5}
.glegend i{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:4px;
           vertical-align:middle}
/* "本次经过"摘要：单独占一块，说明这一次走了哪条路、进了哪几个子图 */
.gsummary{padding:8px 14px;border-bottom:1px solid #eef2f5;background:#fcfdfe}
.gsum-title{font-size:11.5px;color:var(--muted);margin-bottom:5px}
.gsum-row{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:4px}
.gchip{font-size:11px;padding:2px 7px;border-radius:10px;background:#f0f3f6;color:#8b98a3;
       border:1px solid #e3e9ee}
.gchip.on{background:#e9f4f0;color:#1a5044;border-color:#bcdccf;font-weight:600}
.gchip.sub.on{background:#eef4f9;color:#17405f;border-color:#c3d6e6}
.gsum-note{font-size:11px;color:var(--muted)}
/* 架构图里的"层"：一层一个浅色底 + 标题，这是它区别于"节点云"的关键 */
.glayer rect{fill:#f7f9fb;stroke:#e6ecf1;stroke-width:1}
.glayer.touched rect{fill:#f2f8f6;stroke:#cfe3da}
.glayer-title{font:11px "Microsoft YaHei",system-ui,sans-serif;fill:#7b8b96}
.glayer.touched .glayer-title{fill:#2f6b58;font-weight:600}
.gedge-label{font:9.5px "Microsoft YaHei",system-ui,sans-serif;fill:#a8b4bd;
             text-anchor:middle}
.gwrap{flex:1;overflow:auto;background:#fbfcfd;padding:8px}
#gSvg{display:block}
/* 工作流图里的节点：三种状态（未走 / 走过 / 正在走） */
.gnode rect,.gnode polygon{fill:#fff;stroke:#cfd8de;stroke-width:1.2}
.gnode text{font:11px "Microsoft YaHei",system-ui,sans-serif;fill:var(--muted);
            pointer-events:none}
.gnode.visited rect,.gnode.visited polygon{stroke-width:1.8}
.gnode.visited text{fill:var(--ink);font-weight:600}
.gnode.g-main.visited rect,.gnode.g-main.visited polygon{fill:#eef4f9;stroke:var(--g-main)}
.gnode.g-kb.visited rect{fill:#e9f4f0;stroke:var(--g-kb)}
.gnode.g-risk.visited rect{fill:#fbeeeb;stroke:var(--g-risk)}
.gnode.decision rect,.gnode.decision polygon{fill:#fcfdff}
.gnode{cursor:pointer}
.gedge{stroke:#dde5ea;stroke-width:1.2;fill:none;marker-end:url(#arrow)}
.gedge.conditional{stroke-dasharray:4 3}
.gedge.subgraph{stroke:#b9c9d6;stroke-dasharray:2 3}
/* 走过的边：流动效果 —— 这就是"数据流向"的可视化 */
.gedge.flowed{stroke:var(--accent);stroke-width:2;stroke-dasharray:7 5;
              animation:flow .7s linear infinite}
@keyframes flow{to{stroke-dashoffset:-12}}
```

### Markup — `Code/app/web/chat.html` lines 358–373

```html
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
```

### Layout constants + layered layout — `Code/app/web/chat.html` lines 1057–1099

```js
let graphData = null;     // /api/graph 的结果
let graphPos = {};        // 节点 id → {x, y}
let graphVisited = {};    // 节点 id → {seq, ms, llm}
let graphLastNode = null; // 上一个执行过的节点（用来点亮走过的那条边）
let graphView = {x: 0, y: 0, k: 1};

/** 分层：层号 = 从"没有入边"的节点出发的最长路径。 */
//: 架构画布上的尺寸常量。TB 布局（自上而下），和 `langgraph-main.mmd` 的阅读顺序一致。
const GNODE_W = 118, GNODE_H = 30, GNODE_GAP = 12;
const LAYER_H = 92, LAYER_PAD = 34;      // 层高 / 层标题占位

function graphLayout() {
  graphPos = {};
  const arch = graphData.architecture;
  let y = LAYER_PAD;
  let maxX = 0;
  graphData._layers = [];
  for (const layer of arch.layers) {
    const n = layer.nodes.length;
    const rowW = n * GNODE_W + (n - 1) * GNODE_GAP;
    // 居中：整行宽度按最宽的一层对齐（L1 七路最宽）
    const startX = 16;
    const top = y + 22;                       // 让开层标题
    layer.nodes.forEach((id, i) => {
      graphPos[id] = {x: startX + i * (GNODE_W + GNODE_GAP), y: top};
    });
    maxX = Math.max(maxX, startX + rowW);
    graphData._layers.push({...layer, y: y, top: top, width: rowW});
    y = top + GNODE_H + LAYER_H - GNODE_H;    // 下一层的标题位置
  }
  graphData._size = {w: maxX + 32, h: y + 40};
}
```

### SVG element helper — `Code/app/web/chat.html` lines 1101–1105

```js
function graphEl(tag, attrs) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
```

### Full draw (layers → edges → nodes) — `Code/app/web/chat.html` lines 1107–1209

```js
function graphDraw() {
  // ★ 三道守卫，缺一不可。这套绘制是从 `clearTrace()` 里被调到的，而
  //   `clearTrace()` 又在"新对话/切换会话/开始新一轮"这些**主路径**上 ——
  //   也就是说这里一旦抛错，坏的不是"图没画出来"，而是**整个页面动不了**。
  //   实测踩到过：/api/graph 返回了一个没有 architecture 的响应，
  //   `graphLayout()` 抛错 → graphData 被赋了值但没有 _size →
  //   此后的 `graphReset()` 在 `graphData._size` 上解构失败 → 新对话直接崩。
  if (!graphData || !graphData._size || !graphData._layers) return;
  if (!graphData.architecture || !graphData.architecture.layers) return;
  const svg = $("gSvg");
  if (!svg) return;
  const {w, h} = graphData._size;
  svg.innerHTML = "";
  svg.setAttribute("viewBox", `${graphView.x} ${graphView.y} ${w / graphView.k} ${h / graphView.k}`);
  svg.setAttribute("width", Math.round(w / graphView.k));
  svg.setAttribute("height", Math.round(h / graphView.k));

  const arch = graphData.architecture;
  const decisions = new Set(arch.decisions);

  const defs = graphEl("defs", {});
  const marker = graphEl("marker", {id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5",
                                    markerWidth: "6", markerHeight: "6", orient: "auto"});
  marker.appendChild(graphEl("path", {d: "M0,0 L10,5 L0,10 z", fill: "#cfd8de"}));
  defs.appendChild(marker);
  svg.appendChild(defs);

  // ── 层的背景与标题（这是"架构"读得懂的关键：一眼看出有哪几层）──
  for (const layer of graphData._layers) {
    const done = layer.nodes.filter((n) => graphVisited[n]).length;
    const bg = graphEl("g", {class: "glayer" + (done ? " touched" : "")});
    bg.appendChild(graphEl("rect", {x: 8, y: layer.y, width: layer.width + 16,
                                    height: GNODE_H + LAYER_H - 12, rx: 8}));
    const t = graphEl("text", {x: 18, y: layer.y + 16, class: "glayer-title"});
    t.textContent = `${layer.title}　·　${layer.hint}`
      + (done ? `　［本次走过 ${done} 个节点］` : "");
    bg.appendChild(t);
    svg.appendChild(bg);
  }

  // ── 先画边（压在节点下面）──
  for (const [src, dst, label] of arch.edges) {
    const a = graphPos[src], b = graphPos[dst];
    if (!a || !b) continue;
    const x1 = a.x + GNODE_W / 2, y1 = a.y + GNODE_H;
    const x2 = b.x + GNODE_W / 2, y2 = b.y;
    // 竖直方向用三次贝塞尔：往回走的边（revise → aggregate）也能看出方向
    const dy = Math.max(18, Math.abs(y2 - y1) / 2);
    const d = `M${x1},${y1} C${x1},${y1 + dy} ${x2},${y2 - dy} ${x2},${y2}`;
    const cls = "gedge" + (graphVisited[src] && graphVisited[dst] ? " flowed" : "");
    const p = graphEl("path", {d, class: cls});
    p.dataset.from = src;
    p.dataset.to = dst;
    svg.appendChild(p);
    if (label) {
      const lt = graphEl("text", {x: (x1 + x2) / 2 + 4, y: (y1 + y2) / 2,
                                  class: "gedge-label"});
      lt.textContent = label;
      svg.appendChild(lt);
    }
  }

  // ── 再画节点 ──
  for (const layer of arch.layers) {
    for (const id of layer.nodes) {
      const p = graphPos[id];
      if (!p) continue;
      const meta = NODES[id] || {n: id, llm: 0, d: "（未登记的节点）", g: "main"};
      const g = graphEl("g", {class: `gnode g-${meta.g}` + (graphVisited[id] ? " visited" : "")
                                     + (decisions.has(id) ? " decision" : ""),
                              transform: `translate(${p.x},${p.y})`});
      if (decisions.has(id)) {
        // 菱形：路由点（和 .mmd 里的 { } 节点对应）
        const pad = 9;
        g.appendChild(graphEl("polygon", {
          points: `${GNODE_W / 2},0 ${GNODE_W},${GNODE_H / 2} ${GNODE_W / 2},${GNODE_H} 0,${GNODE_H / 2}`}));
        const t = graphEl("text", {x: GNODE_W / 2, y: 19, "text-anchor": "middle"});
        t.textContent = (meta.n || id).slice(0, 9);
        g.appendChild(t);
        void pad;
      } else {
        g.appendChild(graphEl("rect", {width: GNODE_W, height: GNODE_H, rx: 6}));
        const t = graphEl("text", {x: 8, y: 19});
        t.textContent = (meta.n || id).slice(0, 11);
        g.appendChild(t);
      }
      if (meta.llm) {
        const dot = graphEl("circle", {cx: GNODE_W - 9, cy: 9, r: 3, fill: "#e0a94a"});
        dot.appendChild(graphEl("title", {})).textContent = "会调用大模型（烧 token）";
        g.appendChild(dot);
      }
      const title = graphEl("title", {});
      title.textContent = `${meta.n || id}（${id}）`
        + (graphVisited[id]
            ? `\n第 ${graphVisited[id].seq} 个执行 · 用时 ${graphVisited[id].ms}ms`
            : "\n本次没有走到")
        + `${meta.llm ? " · 调用大模型" : ""}\n${meta.d}`;
      g.appendChild(title);
      svg.appendChild(g);
    }
  }
  graphSummary();
}
```

### "本次经过" chip summary — `Code/app/web/chat.html` lines 1218–1255

```js
function graphSummary() {
  const box = $("gSummary");
  if (!box) return;
  if (!graphData) return;
  const arch = graphData.architecture;
  const ran = (id) => Boolean(graphVisited[id]);
  const chips = [];
  for (const layer of arch.layers) {
    const hit = layer.nodes.filter(ran);
    const cls = hit.length ? "gchip on" : "gchip";
    const who = hit.length && layer.id === "L1"
      ? `（${hit.map((n) => (NODES[n] || {n}).n).join("、")}）` : "";
    chips.push(`<span class="${cls}">${hit.length ? "✓" : "·"} ${layer.title}${who}</span>`);
  }
  const subs = arch.subgraphs.map((s) => {
    const hit = s.nodes.filter(ran);
    return `<span class="${hit.length ? "gchip on sub" : "gchip sub"}">`
      + `${hit.length ? "✓" : "·"} ${s.title}`
      + (hit.length ? `（${hit.length} 步：${hit.slice(0, 4).map((n) => (NODES[n] || {n}).n).join("、")}${hit.length > 4 ? "…" : ""}）` : "")
      + `</span>`;
  }).join("");
  const total = Object.keys(graphVisited).length;
  const subHit = arch.subgraphs.filter((s) => s.nodes.some(ran)).length;
  // 窗口标题上的迷你摘要：不点开也知道"这次走了多少、进了几个子图"
  const inline = $("gSummaryInline");
  if (inline) {
    inline.textContent = total
      ? `本次 ${total} 个节点 · ${subHit} 个子图`
      : "等待提问";
  }
  box.innerHTML = `<div class="gsum-title">本次对话经过：</div>`
    + `<div class="gsum-row">${chips.join("")}</div>`
    + `<div class="gsum-row">${subs}</div>`
    + `<div class="gsum-note">共执行 ${total} 个节点`
    + (graphData.warnings && graphData.warnings.length
        ? `　<span style="color:#8a2a1e">⚠ ${graphData.warnings.join("；")}</span>`
        : "　（架构与代码一致）") + `</div>`;
}
```

### Mark / reset / fetch / zoom — `Code/app/web/chat.html` lines 1264–1315

```js
function graphMark(nodeId, ms) {
  if (!nodeId) return;
  graphVisited[nodeId] = {
    seq: Object.keys(graphVisited).length + 1,
    ms: ms || 0,
    llm: !!((NODES[nodeId] || {}).llm),
  };
  graphLastNode = nodeId;
  if (graphData && !$("paneGraph").hidden) graphDraw();   // 图没显示就先只记状态
}

function graphReset() {
  graphVisited = {};
  graphLastNode = null;
  if (graphData && !$("paneGraph").hidden) graphDraw();
}

async function graphEnsure() {
  if (graphData && graphData._size) return true;
  try {
    const r = await api("/api/graph");
    const data = await r.json();
    // ★ 先校验再赋值：赋值了但没布局数据，后面的 graphDraw() 会一直失败，
    //   而且失败点散在好几个调用处（见 graphDraw 的说明）。
    if (!data || !data.architecture || !data.architecture.layers) {
      throw new Error("架构数据不完整");
    }
    graphData = data;
    graphLayout();
    graphDraw();
    return true;
  } catch (e) {
    graphData = null;                  // 保持"没数据"的一致状态，别留半成品
    // ★ 用 el() + textContent 而不是拼 innerHTML：一是聊天页没有 esc() 这种
    //   转义助手（坐席台才有），二是错误信息里可能带服务端返回的任意文本 ——
    //   直接塞进 innerHTML 就是个注入口子。
    const wrap = $("gWrap");
    if (wrap) {
      wrap.innerHTML = "";
      wrap.appendChild(el("div", "trace-empty", "工作流架构加载失败：" + e.message));
    }
    return false;
  }
}

function switchPane(which) {
  void which;   // 两个窗口现在同屏展示，不再需要切换（保留函数名便于外部调用无副作用）
}
$("gFit").onclick = () => { graphView = {x: 0, y: 0, k: 1}; graphDraw(); };
$("gZoomIn").onclick = () => { graphView.k = Math.min(2.5, graphView.k * 1.25); graphDraw(); };
$("gZoomOut").onclick = () => { graphView.k = Math.max(0.4, graphView.k / 1.25); graphDraw(); };
$("gClear").onclick = () => graphReset();
```

### Layer data the pane renders — `Code/app/api/graph_topology.py` lines 39–80

```python
ARCHITECTURE: dict[str, Any] = {
    "layers": [
        {"id": "L0", "title": "入口与调度层",
         "hint": "标准化 → 紧急筛查 → 意图与槽位 → 分派",
         "nodes": ["normalize", "emergency_screen", "classify", "dispatch"]},
        {"id": "L1", "title": "草稿产出层 · 七路并行",
         "hint": "每个 Agent 只产出草稿，都不直接对用户说话",
         "nodes": ["k_agent", "r_agent", "c_agent", "b_agent", "p_agent",
                   "clarify", "emergency_draft"]},
        {"id": "GATE", "title": "聚合与必经关卡",
         "hint": "唯一汇聚点；风险审查子图是**必经**的",
         "nodes": ["aggregate", "risk_gate"]},
        {"id": "OUT", "title": "输出与执行闭环",
         "hint": "操作类请求要用户明确确认后才执行，执行结果还要再送审一次",
         "nodes": ["output_type", "await_confirm", "execute_op", "receipt",
                   "set_result_kind", "recheck", "final_check"]},
        {"id": "LOOP", "title": "修订回路与作废",
         "hint": "按审查意见回到原专业 Agent 重写；预算用尽则转人工",
         "nodes": ["feedback", "retry_check", "revise", "plan_void"]},
        {"id": "EXITS", "title": "出口层 · 所有出站内容都必须持有放行凭据",
         "hint": "send 会校验 release_token，校验不过一律不出站",
         "nodes": ["send", "human_handoff", "audit_block"]},
    ],
    #: 画成菱形的路由点（对应 .mmd 里的 { } 节点）
    "decisions": ["emergency_screen", "dispatch", "risk_gate", "retry_check",
                  "output_type", "final_check"],
    #: 子图：挂在主图的哪个节点上（`hosts`），内部有哪些节点
    "subgraphs": [
        {"id": "risk", "title": "风险审查子图",
         "hint": "硬性规则 + 三份面板意见 → 裁决 → 放行凭据",
         "hosts": ["risk_gate", "recheck"], "entry": "gate_in",
         "nodes": ["gate_in", "review_input", "emergency_check", "hard_rules",
                   "review_medical", "review_ad", "review_privacy", "panel_fanin",
                   "check_op", "biz_check", "merge_verdict", "issue_token",
                   "need_info", "block", "handoff", "escalate_review"]},
        {"id": "kb", "title": "知识科普子图",
         "hint": "检索 → 证据筛选 → 草稿 / 降级 / 追问 → 逐句核对",
         "hosts": ["k_agent"], "entry": "kb_intake",
         "nodes": ["kb_revise_in", "kb_intake", "kb_context", "kb_decompose",
                   "kb_clarify", "kb_ctx_reply", "kb_retrieve", "kb_evidence",
                   "kb_limit", "kb_draft", "kb_verify", "kb_return"]},
    ],
```

---

## 10. NodeMetaTable

The front-end's own documentation table: node id → 中文名 / 所属图 / 是否调大模型 / 一句话说明.
It is the only source of node labels and group colours for the graph pane and trace rows.

`Code/app/web/chat.html` lines 399–465:

```js
/* ══════════════════════════════════════════════════════════════
   节点元数据：中文名 / 所属图 / 是否调用大模型 / 一句话说明
   —— 这是**文档**，不是从事件流推断出来的。事件流只给节点 id。
   ══════════════════════════════════════════════════════════════ */
const NODES = {
  /* 主图 */
  normalize:        {n:"输入标准化",       g:"main", llm:0, d:"脱敏、预扫描、落库、读权限"},
  emergency_screen: {n:"紧急信号筛查",     g:"main", llm:0, d:"规则层词表匹配；命中则走急症快路径"},
  classify:         {n:"意图识别",         g:"main", llm:1, d:"判断这句话想干什么 + 抽取槽位"},
  dispatch:         {n:"总控调度",         g:"main", llm:1, d:"规划任务顺序，操作类排最前"},
  k_agent:          {n:"科普子图",         g:"main", llm:0, d:"子图整体（内部节点见下方蓝色行）"},
  r_agent:          {n:"项目推荐",         g:"main", llm:1, d:"基于画像与项目库给方向性建议"},
  c_agent:          {n:"资质门店",         g:"main", llm:1, d:"查医生资质与门店信息"},
  b_agent:          {n:"预约管理",         g:"main", llm:1, d:"查档期、查可改约的预约、生成方案"},
  p_agent:          {n:"术后护理",         g:"main", llm:1, d:"术后注意事项与随访"},
  clarify:          {n:"澄清追问",         g:"main", llm:1, d:"信息不足时提 2–3 个封闭式问题"},
  emergency_draft:  {n:"急症固定模板",     g:"main", llm:0, d:"不经模型生成，直接取预审模板"},
  aggregate:        {n:"草稿汇总",         g:"main", llm:0, d:"合并各 Agent 草稿，计算方案哈希"},
  risk_gate:        {n:"风险审查关卡",     g:"main", llm:0, d:"★ 必经关卡：任何出站都要过这道门"},
  output_type:      {n:"出口类型判定",     g:"main", llm:0, d:"决定是直接发送还是挂起等确认"},
  await_confirm:    {n:"等待用户确认",     g:"main", llm:0, d:"挂起；恢复时代码会被重放"},
  execute_op:       {n:"执行操作",         g:"main", llm:0, d:"校验凭据 → 幂等执行"},
  receipt:          {n:"生成执行回执",     g:"main", llm:1, d:"把执行结果写成给用户看的话"},
  set_result_kind:  {n:"结果送审标记",     g:"main", llm:0, d:"回执也要过审，不能直接发"},
  recheck:          {n:"执行结果复审",     g:"main", llm:0, d:"第二类审查对象"},
  final_check:      {n:"终检",             g:"main", llm:0, d:"出站前最后一次内容校验"},
  feedback:         {n:"审查意见整理",     g:"main", llm:1, d:"把面板意见转成可执行的修改要点"},
  retry_check:      {n:"修订预算检查",     g:"main", llm:0, d:"超预算就转人工，不允许无限改"},
  revise:           {n:"退回修订",         g:"main", llm:0, d:"唯一用 Command(goto=) 的节点"},
  plan_void:        {n:"方案作废",         g:"main", llm:0, d:"用户否认后清掉旧方案，防止误执行"},
  send:             {n:"放行发送",         g:"main", llm:0, d:"★ 唯一能产生 final 事件的节点"},
  human_handoff:    {n:"转人工",           g:"main", llm:0, d:"建工单；与 send 可并行"},
  audit_block:      {n:"硬性阻断",         g:"main", llm:0, d:"命中 block 级规则，不出任何内容"},

  /* 科普子图 */
  kb_intake:    {n:"入口分流",         g:"kb", llm:0, d:"判断是首次进入还是被退回修订"},
  kb_revise_in: {n:"修订再入",         g:"kb", llm:0, d:"带审查意见重新生成（不是只重新核对）"},
  kb_context:   {n:"上下文装配",       g:"kb", llm:0, d:"拼装已知槽位与历史"},
  kb_decompose: {n:"检索子任务拆分",   g:"kb", llm:1, d:"把口语问题拆成 2–5 个检索式"},
  kb_clarify:   {n:"追问（缺信息）",   g:"kb", llm:1, d:"关键信息不足，先问清楚再答"},
  kb_ctx_reply: {n:"越界前先看上下文", g:"kb", llm:1, d:"不是知识库该答的问题时，先用手上已有的信息试答一次"},
  kb_retrieve:  {n:"混合检索",         g:"kb", llm:0, d:"BGE-M3 dense+sparse → Milvus → RRF 融合"},
  kb_evidence:  {n:"证据筛选与充分性", g:"kb", llm:1, d:"精排 + 阈值过滤；高影响问题做二次判断"},
  kb_limit:     {n:"缩小回答范围",     g:"kb", llm:1, d:"证据不足时坦白说不知道，不编"},
  kb_draft:     {n:"生成科普草稿",     g:"kb", llm:1, d:"每句事实都要挂 evidence 编号"},
  kb_verify:    {n:"事实与引用核对",   g:"kb", llm:1, d:"逐句核对；软问题放行、硬问题回炉"},
  kb_return:    {n:"交回待审草稿",     g:"kb", llm:0, d:"子图出口，只产出待审草稿"},

  /* 风险审查子图 */
  gate_in:         {n:"审查入口",         g:"risk", llm:0, d:"标准化审查请求，先做 PII 预扫描"},
  review_input:    {n:"输入完整性与权限", g:"risk", llm:0, d:"身份未核验时操作类请求一律拦下"},
  emergency_check: {n:"紧急信号复核",     g:"risk", llm:0, d:"规则层与模型提示的边界核对"},
  hard_rules:      {n:"硬规则检查",       g:"risk", llm:0, d:"确定性规则，不调用模型"},
  review_medical:  {n:"医疗边界审查",     g:"risk", llm:1, d:"是否越过科普进入医疗建议"},
  review_ad:       {n:"广告合规审查",     g:"risk", llm:1, d:"疗效承诺、绝对化用语、制造焦虑"},
  review_privacy:  {n:"隐私与越权审查",   g:"risk", llm:1, d:"敏感信息回显、越权表述"},
  panel_fanin:     {n:"三审汇合",         g:"risk", llm:0, d:"等三个面板都出结果再往下"},
  escalate_review: {n:"二次复核",         g:"risk", llm:1, d:"单模型下与首审同族，审计会如实标注"},
  check_op:        {n:"敏感操作校验",     g:"risk", llm:0, d:"仅操作类走这里"},
  biz_check:       {n:"业务前置校验",     g:"risk", llm:0, d:"档期/状态是否还成立"},
  merge_verdict:   {n:"裁决表",           g:"risk", llm:0, d:"确定性决策表，不用模型拍板"},
  issue_token:     {n:"签发放行凭据",     g:"risk", llm:0, d:"HMAC 绑定内容+方案+类别+有效期"},
  handoff:         {n:"人工接管",         g:"risk", llm:0, d:"落工单 + 如实标注是否独立复核"},
  block:           {n:"阻断出口",         g:"risk", llm:0, d:"硬性阻断终态"},
  need_info:       {n:"资料不全出口",     g:"risk", llm:0, d:"要求先补齐信息"}
};
const GROUP_COLOR = {main:"var(--g-main)", kb:"var(--g-kb)", risk:"var(--g-risk)"};
```

---

## 11. TraceNodeRow & NodeDetailPanel

The trace list: one `<li class="node g-{group}">` per executed node plus a sibling `.nd` output panel
that expands on click and dumps the raw state patch.

### CSS — `Code/app/web/chat.html` lines 187–229

```css
.legend{padding:10px 18px;border-bottom:1px solid #eef2f5;font-size:12px;color:var(--muted);
        display:flex;flex-wrap:wrap;gap:6px 14px;background:#fcfdfe}
.legend i{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:5px;
          vertical-align:middle}
.nodes{flex:1;overflow-y:auto;margin:0;padding:8px 0;list-style:none}
.node{display:grid;grid-template-columns:26px 1fr auto;gap:9px;align-items:center;
      padding:6px 18px;border-left:3px solid transparent;animation:rise .16s ease-out;
      cursor:pointer}
.node:hover{background:#f2f8f6}
.node .idx{font:11px Consolas,monospace;color:#9fb0bb;text-align:right}
.node .nm{font-size:13px;display:flex;align-items:center;gap:7px;min-width:0}
.node .nm b{font-weight:400;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.node .id{font:10.5px Consolas,monospace;color:#9fb0bb}
.node .ms{font:11px Consolas,monospace;color:var(--muted);white-space:nowrap}
.node .llm{font:10px Consolas,monospace;padding:1px 5px;border-radius:3px;
           background:#fff4dd;color:#6d4c08;flex:none}
.node .caret{font:10px Consolas,monospace;color:#b6c4cd;flex:none;transition:transform .12s}
.node.open .caret{transform:rotate(90deg)}
.node.g-kb{border-left-color:var(--g-kb);background:#f7fafd}
.node.g-risk{border-left-color:var(--g-risk);background:#fdfaf6}
.node.g-main{border-left-color:#e3eaee}
.node.hot{background:#eaf4f2;border-left-color:var(--accent)}
.node.hot .idx{color:var(--accent)}
.node.empty .nm b{color:#9fb0bb}

/* 节点输出（点开展示） */
.nd{margin:0 0 4px 26px;padding:9px 12px 11px;border-left:3px solid var(--line);
    background:#fbfcfd;border-radius:0 8px 8px 0;animation:rise .14s ease-out}
.nd-desc{font-size:12px;color:var(--muted);padding-bottom:7px;margin-bottom:7px;
         border-bottom:1px dashed #e6ecef}
.nd-row{display:grid;grid-template-columns:132px 1fr;gap:10px;padding:3px 0;
        font-size:12px;align-items:start}
.nd-key{font:11.5px Consolas,monospace;color:var(--accent);word-break:break-all}
.nd-val{font:11.5px/1.65 Consolas,monospace;color:#2b4256;white-space:pre-wrap;
        word-break:break-word;margin:0}
.nd-none{font-size:12px;color:var(--muted)}
.nd-warn{font-size:11px;color:#8a5a1e;background:#fff8e8;border:1px solid #f0e0bb;
         border-radius:5px;padding:4px 8px;margin-bottom:7px}
.trace-empty{color:var(--muted);font-size:13px;padding:22px 18px;text-align:center}
.trace-foot{padding:11px 18px;border-top:1px solid var(--line);background:#fbfcfd;
            font-size:12.5px;color:var(--muted);display:flex;flex-wrap:wrap;gap:6px 16px}
.trace-foot b{color:var(--ink);font-weight:600}
.hint{font-size:12px;color:var(--muted);padding:8px 18px 0;line-height:1.6}
```

### Markup (trace pane, legend, list, footer, hint) — `Code/app/web/chat.html` lines 375–392

```html
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
```

### Row builder — `Code/app/web/chat.html` lines 1317–1377

```js
function addNode(d) {
  const meta = NODES[d.node] || {n: d.node, g: "main", llm: 0, d: "（未登记的节点）"};
  graphMark(d.node, d.ms);      // 工作流图同步点亮（切到图那一栏就能看到整条路径）
  const li = el("li", "node g-" + meta.g);
  nodeSeq += 1;
  li.appendChild(el("span", "idx", String(nodeSeq)));
  const nm = el("span", "nm");
  const caret = el("span", "caret", "▶");
  nm.appendChild(caret);
  nm.appendChild(el("b", null, meta.n));
  nm.appendChild(el("span", "id", d.node));
  if (meta.llm) nm.appendChild(el("span", "llm", "LLM"));
  li.appendChild(nm);
  li.appendChild(el("span", "ms", d.ms + "ms"));

  const detail = d.detail || {};
  const nKeys = Object.keys(detail).length;
  if (!nKeys) li.classList.add("empty");
  li.title = meta.d + "　（点击查看该节点的输出）";

  // ── 点开展示该节点写回的状态 ──
  const box = el("div", "nd");
  box.style.display = "none";
  const desc = el("div", "nd-desc", meta.d);
  box.appendChild(desc);
  if (!nKeys) {
    box.appendChild(el("div", "nd-none", "（该节点没有写出状态 —— 它只做判断或只调用模型）"));
  } else {
    // 提醒放在最显眼处：这里可能包含未经审查的中间产物
    const warn = el("div", "nd-warn",
      "调试信息：以下为该节点写回的原始状态，可能包含未经审查的中间产物，勿对顾客展示");
    box.appendChild(warn);
    for (const k of Object.keys(detail)) {
      const row = el("div", "nd-row");
      row.appendChild(el("span", "nd-key", k));
      const v = detail[k];
      let text;
      if (typeof v === "string") text = v;
      else if (v === null || typeof v === "number" || typeof v === "boolean") text = String(v);
      else text = JSON.stringify(v, null, 1);
      row.appendChild(el("pre", "nd-val", text));
      box.appendChild(row);
    }
  }
  li.onclick = () => {
    const open = box.style.display !== "none";
    box.style.display = open ? "none" : "block";
    li.classList.toggle("open", !open);
  };

  $("nodes").appendChild(li);
  $("nodes").appendChild(box);
  // 只保留最近一条高亮，避免整列都在闪
  document.querySelectorAll(".node.hot").forEach((n) => n.classList.remove("hot"));
  li.classList.add("hot");
  // ★ 有节点被展开时不要自动滚到底：用户正在看某个节点的输出，
  //   把它滚走等于把用户正在读的东西抢走。
  if (!document.querySelector(".node.open")) {
    $("nodes").scrollTop = $("nodes").scrollHeight;
  }
}
```

### Trace summary footer + clear — `Code/app/web/chat.html` lines 1035–1042, 1378–1408

```js
/* ── 轨迹渲染 ── */
function clearTrace(showHint) {
  $("nodes").innerHTML = "";
  $("traceFoot").style.display = "none";
  $("traceMeta").textContent = showHint ? "等待提问" : "执行中…";
  $("traceHint").style.display = showHint ? "block" : "none";
  nodeSeq = 0;
  graphReset();      // 工作流图的高亮也要跟着清掉（同一轮对话的一份记录）
}
```

```js
function finishTrace(ok) {
  const rows = [...document.querySelectorAll(".node")];
  if (!rows.length) return;
  const total = rows.reduce((s, r) => {
    const v = parseInt(r.querySelector(".ms").textContent, 10);
    return s + (isNaN(v) ? 0 : v);
  }, 0);
  const llm = document.querySelectorAll(".node .llm").length;
  const kb = document.querySelectorAll(".node.g-kb").length;
  const risk = document.querySelectorAll(".node.g-risk").length;
  $("traceMeta").textContent = `${rows.length} 个节点`;
  const f = $("traceFoot");
  f.innerHTML = "";
  const add = (label, val) => {
    const s = el("span");
    s.appendChild(document.createTextNode(label + " "));
    s.appendChild(el("b", null, String(val)));
    f.appendChild(s);
  };
  add("节点数", rows.length);
  add("调用大模型", llm);
  add("科普子图", kb);
  add("风险审查", risk);
  add("事件间隔累计", total + "ms");
  f.style.display = "flex";
  if (!ok) {
    const s = el("span", null, "（本轮出现错误或转人工）");
    s.style.color = "var(--bad)";
    f.appendChild(s);
  }
}
```

---

## 12. SseStreamReader

Not a visual component, but the shared transport: both pages read SSE with `fetch` + a
`ReadableStream` reader (never `EventSource`, which cannot send an `Authorization` header).

`Code/app/web/chat.html` lines 1510–1567:

```js
async function stream(path, body) {
  const useTrace = $("traceOn").checked;
  const url = path + (useTrace ? "?trace=1" : "");
  setConn("busy", "处理中");
  clearConfirm();
  let name = "?", ok = true;
  try {
    const r = await api(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    if (!r.ok) {
      let detail = {};
      try { detail = (await r.json()); } catch (e) { /* 忽略 */ }
      addMsg("err", "请求被拒绝（HTTP " + r.status + "）：" +
             (detail.message || ""));
      ok = false;
      return;
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const blocks = buf.split("\n\n");
      buf = blocks.pop();
      for (const blk of blocks) {
        for (const line of blk.split("\n")) {
          if (line.startsWith("event: ")) name = line.slice(7).trim();
          else if (line.startsWith("data: ")) {
            let d = {};
            try { d = JSON.parse(line.slice(6)); } catch (e) { /* 忽略坏帧 */ }
            ok = handle(name, d) && ok;
          }
        }
      }
    }
  } catch (e) {
    addMsg("err", "连接中断：" + e.message);
    ok = false;
  } finally {
    setStatus("");
    setConn("on", "已连接");
    // ★ 先把"已看到的位置"对齐到库里，**再**放开 busy：
    //   放在 busy 之后的话，轮询会在这一小段窗口里发现"库里有新消息"，
    //   把刚刚流式渲染过的两条再追加一遍。
    await resyncSeen();
    busy = false;
    // ★ 输入框不再因为人工接管而禁用（用户要能继续补充情况），
    //   所以这里只需恢复发送按钮 —— 唯一要保证的是别把 busy 状态留在原地。
    setComposeEnabled(true);
    finishTrace(ok);
  }
  return ok;
}
```

### Event dispatcher — `Code/app/web/chat.html` lines 1569–1662

```js
function handle(name, d) {
  switch (name) {
    case "node":
      addNode(d);
      return true;
    case "status":
      setStatus(d.text || "");
      return true;
    case "final":
      // ★ 正文用打字机呈现：内容是**整段审后送达**的（见 addMsgTyped 的说明），
      //   这里只是送达之后的显示动画。
      //
      // ★ 角标不再写"不流式"。那句话描述的是**传输方式**，而用户看到的是
      //   **呈现方式** —— 一边逐字往外走、一边标着"不流式"，只会让人以为坏了。
      //   角标要说的是"这份正文是整段过审之后才发出来的"，那才是它想表达的保证。
      addMsgTyped("bot", d.text || "", [
        {text: "已审正文", cls: "ok"},
        "凭据 " + String(d.token || "").slice(0, 12) + "…",
        typingEligible(d.text) ? "逐字呈现 · 整段过审后送达" : "整段过审后送达",
      ]);
      return true;
    case "awaiting_confirmation": {
      pendingPlan = d;
      const m = addMsg("sys", "以下操作需要你确认后才会执行：\n\n" + (d.plan || ""), [
        {text: "等待确认", cls: "warn"},
        "方案哈希 " + String(d.plan_hash || "").slice(0, 12) + "…"
      ]);
      const bar = el("div", "confirm");
      const yes = el("button", "primary", "确认执行");
      const no = el("button", null, "取消");
      yes.onclick = () => {
        clearConfirm();
        addMsg("user", "确认执行");
        stream("/api/chat/" + sid + "/confirm",
               {confirmed: true, plan_hash: d.plan_hash});
      };
      no.onclick = () => {
        clearConfirm();
        addMsg("user", "取消");
        stream("/api/chat/" + sid + "/confirm",
               {confirmed: false, plan_hash: d.plan_hash});
      };
      bar.appendChild(yes);
      bar.appendChild(no);
      m.appendChild(bar);
      $("messages").scrollTop = $("messages").scrollHeight;
      return true;
    }
    case "handoff":
      addMsg("sys", (d.text || "已转接人工客服。") + "\n\n工单 " +
             String(d.ticket_id || "").slice(0, 8) + "… 优先级 " + (d.priority || ""), [
        {text: "转人工", cls: "warn"}, "原因 " + (d.reason || "")
      ]);
      // ★ 这里**故意不点亮接管提示条**。
      //   刚建完工单时 AI 并没有被停用 —— 按设计，AI 要一直答到坐席**接单**
      //   为止（因为人工可能一时接不上，AI 至少还能帮上忙）。所以此刻挂出
      //   "AI 已暂停自动回复"是**谎报**：用户会以为 AI 不理他了。
      //   真实状态由服务端的 `ai_enabled` 决定，轮询每 3 秒同步一次
      //   （见 applyPoll：那一步现在放在所有提前 return 之前，不会卡住）。
      return true;
    case "human_takeover":
      // ★ 接管期间用户发的话被收下了，但 AI 不回答 —— 必须**明确**告诉他
      //   结果是什么，否则他会以为消息发出去了、在等 AI 回复。
      //   文案由服务端给（它知道坐席有没有接单），前端不自己编。
      //
      // ★ quiet=true：这是接管期间的**后续**消息，只在状态栏提示一行。
      //   接管时顾客常常连着补充好几句，每条都插一个气泡会变成刷屏，
      //   真正重要的那句话反而被淹掉。
      if (d.quiet) {
        setStatus(d.text || "已转达客服");
        setTimeout(() => { if (!busy) setStatus(""); }, 3500);
      } else {
        addMsg("sys", d.text || "当前由人工客服接管，AI 已暂停自动回复。", [
          {text: "人工接管", cls: "warn"},
          d.accepted ? "客服已接单" : "排队等待客服接单",
        ]);
      }
      // 顺手把提示条点上（不等下一轮轮询）
      lastSeenTaken = takeover;
      syncTakeover(true, true);
      return true;
    case "blocked":
      addMsg("err", "本次内容被硬性阻断，未产生任何业务出站内容。命中规则：" +
             (d.rule_ids || []).join("、"), [{text: "阻断", cls: "bad"}]);
      return false;
    case "error":
      addMsg("err", "处理出错：" + (d.message || d.code || ""));
      return false;
    case "done":
      return true;
    default:
      return true;
  }
}
```

### Polling fallback (human-agent replies arrive on another link) — `Code/app/web/chat.html` lines 492–493, 1428–1488

```js
//: 后台轮询间隔（毫秒）。坐席的回复是走另一条链路写进库的（见 startWatch）。
const WATCH_MS = 3000;
```

```js
function startWatch() {
  if (watchTimer) return;
  watchTimer = setInterval(watchOnce, WATCH_MS);
}

function stopWatch() {
  if (watchTimer) { clearInterval(watchTimer); watchTimer = null; }
}

async function watchOnce() {
  if (busy || !sid || document.hidden) return;
  let d;
  try {
    const r = await api("/api/sessions/" + sid + "?limit=60");
    if (!r.ok) return;
    d = await r.json();
  } catch (e) {
    return;   // 网络抖动不在对话区里刷错误，下一轮自己会好
  }
  try {
    await applyPoll(d);
  } catch (e) {
    // ★ 轮询是后台行为，任何异常都不能冒成 unhandled rejection ——
    //   setInterval 不会 await 它，没人接得住，而后果是整条定时器链悄悄失效。
    console.warn("[C端] 轮询处理失败：", e);
  }
}

/** 把一次轮询的结果落到界面上（分离出来只为让上面的 catch 兜得住）。 */
async function applyPoll(d) {
  const msgs = d.messages || [];
  const taken = (d.session || {}).ai_enabled === false;

  // ★ 接管状态**每次轮询都同步**，而且必须放在所有提前 return 之前。
  //
  //   之前它被放在"内容有变化"的分支里，于是只要对话内容没变（绝大多数轮询
  //   都是这样），提示条就永远不更新 —— 用户看到的是
  //   "人工早就结束了，界面还挂着 AI 已暂停"，而且怎么刷新都下不去。
  //
  //   这里的判据只有服务端的 `ai_enabled` 一个来源，所以它**不可能说谎**，
  //   也不可能卡住。对话内容的增量更新是另一件事，两者不该互相牵制。
  syncTakeover(taken, false);

  // 没有任何变化就什么都不做（绝大多数轮询都走到这里）
  const sameTail = lastIndex >= 0 && lastIndex < msgs.length &&
                   msgSig(msgs[lastIndex]) === lastSig;
  if (sameTail && msgs.length === lastIndex + 1) return;

  if (!sameTail) {
    // 和记忆里的位置对不上（换了会话、消息被清、或上一轮没对齐）——
    // 整体重画。★ 但用户正停在待确认卡片上时不能重画：那会把
    // 「确认执行」按钮抹掉，方案就再也确认不了了。
    if (pendingPlan) return;
    renderHistory(d);
  } else {
    for (const m of msgs.slice(lastIndex + 1)) msgNode(m);
    markSeen(msgs);
    $("messages").scrollTop = $("messages").scrollHeight;
  }
  await loadConversations();   // 会话列表上的"人工接管"角标也要跟着变
}
```

---

## 13. MetricCard

Ops console KPI tile grid, built from the `GET /ops/metrics` payload.

### CSS — `Code/app/ops/panel.html` lines 32–36

```css
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;
         max-width:1560px;margin:16px auto 0;padding:0 24px}
.metric{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.metric b{display:block;font-size:20px;letter-spacing:-.5px}
.metric span{color:var(--muted);font-size:12px}
```

### Markup — `Code/app/ops/panel.html` line 160

```html
<div class="metrics" id="metrics"></div>
```

### Builder — `Code/app/ops/panel.html` lines 523–537

```js
// ── 指标 ──────────────────────────────────────────────
function renderMetrics(m) {
  if (!m) return;
  const cells = [
    ["待处理工单", m.tickets_open ?? "—"],
    ["其中 P0", m.tickets_p0_open ?? "—"],
    ["已接单", m.tickets_accepted ?? "—"],
    ["一次通过率", m.first_pass_rate == null ? "—" : (m.first_pass_rate * 100).toFixed(1) + "%"],
    ["平均等待", m.avg_wait_seconds == null ? "—" : m.avg_wait_seconds + "s"],
    ["误报待复核", m.misreport_pending ?? "—"],
    ["同族复核次数", m.escalation_same_family ?? "—"],
  ];
  $("metrics").innerHTML = cells.map(([k, v]) =>
    `<div class="metric"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
}
```

---

## 14. TicketQueueRow

Queue row with priority badge, status badge, test badge, SLA-breached badge, initiator, wait time
and context line. Built by string template + `esc()`.

### CSS — `Code/app/ops/panel.html` lines 44–63, 104–107

```css
.row{padding:12px 18px;border-bottom:1px solid #eef2f5;display:grid;gap:4px}
.row:hover{background:#f7fbfa}
.row.active{background:#e8f2f0}
.row .line1{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.badge{font:11px/1.4 Consolas,monospace;padding:2px 7px;border-radius:4px;white-space:nowrap}
.badge.p0{background:#ffe3de;color:#8a2a1e}
.badge.p1{background:#fff4dd;color:#6d4c08}
.badge.p2{background:#eef1f4;color:#384a55}
.badge.st{background:#e8f1fb;color:#17405f}
.badge.warn{background:#ffe3de;color:#8a2a1e}
.badge.ok{background:#e6f4ee;color:#1a5044}
.row .sub{color:var(--muted);font-size:12.5px}
/* 队列行里的发起人与等待时长：这是坐席扫队列时最先看的两件事 */
.row .who{color:var(--ink);font-weight:600}
.row .sub b{color:var(--ink);font-weight:600}
.row .sub b.over{color:var(--p0)}
.row .sub .muted{color:#9aa8b2}
/* 「对话原文已被顾客删除」的提示：必须显眼，否则坐席会以为系统坏了 */
.tdel{margin:0 0 10px;padding:9px 12px;border-radius:7px;background:#fff4dd;
      border:1px solid #e6d3a8;color:#6d4c08;font-size:12.5px;line-height:1.6}
```

```css
/* 测试工单行：整行降饱和 + 徽章，一眼能和真实工单区分开 */
.badge.test{background:#efe7f7;color:#553a78}
.row.is-test{background:#faf8fd}
.row.is-test .line1 strong{color:#553a78}
```

### Markup shell — `Code/app/ops/panel.html` lines 163–166

```html
<section class="pane">
  <h2>待处理队列 <span class="sub" id="queueCount"></span></h2>
  <div class="pane-body" id="queue"><div class="empty">加载中…</div></div>
</section>
```

### Builder + helpers — `Code/app/ops/panel.html` lines 311–313, 349–376

```js
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtWait = (s) => s < 60 ? s + "s" : (s < 3600 ? Math.floor(s / 60) + "m" : Math.floor(s / 3600) + "h");
```

```js
function renderQueue() {
  $("queueCount").textContent = `共 ${state.tickets.length} 条待处理`;
  if (!state.tickets.length) {
    $("queue").innerHTML = '<div class="empty">当前没有待处理工单<br>'
      + '<span style="font-size:11.5px">（已结束的工单不在队列里）</span></div>';
    return;
  }
  $("queue").innerHTML = state.tickets.map((t) => `
    <div class="row ${state.current === t.ticket_id ? "active" : ""} ${t.is_test ? "is-test" : ""}"
         data-id="${t.ticket_id}">
      <div class="line1">
        <span class="badge ${t.priority === "P0" ? "p0" : t.priority === "P1" ? "p1" : "p2"}">${esc(t.priority)}</span>
        <b>${esc(t.reason)}</b>
        <span class="badge st">${esc(t.status)}</span>
        ${t.is_test ? '<span class="badge test">测试</span>' : ""}
        ${t.sla_breached ? '<span class="badge warn">已超时</span>' : ""}
      </div>
      <div class="sub">
        <span class="who" title="发起人（队列里只显示首字，看全名请点开详情）">${esc(t.user_name || "（未登记）")}</span>
        · 已等待 <b class="${t.sla_breached ? "over" : ""}">${fmtWait(t.wait_seconds)}</b>
        <span class="muted">/ SLA ${fmtWait(t.sla_seconds)}</span>
      </div>
      <div class="sub">${esc(t.context)}</div>
      <div class="sub">工单 ${esc(String(t.ticket_id).slice(0, 8))}… · 会话 ${esc(String(t.session_id).slice(0, 8))}…</div>
    </div>`).join("");
  $("queue").querySelectorAll(".row").forEach((el) =>
    el.addEventListener("click", () => openTicket(el.dataset.id)));
}
```

---

## 15. TicketDetailSection

The detail pane body: order kv table, profile summary, risk report (hard-rule hits, panel reviews,
evidence, un-sent draft), transcript, and the disposition action row.

### CSS — `Code/app/ops/panel.html` lines 64–76

```css
.section{padding:14px 18px;border-bottom:1px solid #eef2f5}
.section h3{margin:0 0 8px;font-size:13px;color:var(--muted);letter-spacing:.5px}
.kv{display:grid;grid-template-columns:88px 1fr;gap:4px 10px;font-size:13px}
.kv dt{color:var(--muted)}
.kv dd{margin:0;word-break:break-word}
.msg{padding:8px 10px;border-radius:8px;margin:6px 0;font-size:13px;white-space:pre-wrap}
.msg.user{background:#f3f6f8}
.msg.assistant{background:#e6f4ee}
.msg.agent{background:#e8f1fb}
.msg.system{background:#fff4dd}
.hit{font:12px/1.6 Consolas,monospace;background:#ffe3de;color:#8a2a1e;
     padding:2px 6px;border-radius:4px;margin-right:6px;display:inline-block}
.empty{color:var(--muted);padding:24px 18px;font-size:13px}
```

### Markup shell — `Code/app/ops/panel.html` lines 168–171

```html
<section class="pane">
  <h2>工单详情 <span class="sub" id="detailTitle"></span></h2>
  <div class="pane-body" id="detail"><div class="empty">从左侧选择一个工单</div></div>
</section>
```

### Full detail renderer — `Code/app/ops/panel.html` lines 399–478

```js
function renderDetail() {
  const d = state.detail;
  if (!d) return;
  const t = d.ticket, rp = d.risk_report || {};
  $("detailTitle").textContent = `${t.priority} · ${t.reason} · ${t.status}`;

  const hits = (rp.hard_rule_hits || []).map((h) =>
    `<span class="hit">${esc(h.rule_id)} ${esc(h.title || "")}：${esc(h.span || "")}</span>`).join("");
  const panels = (rp.panel_reviews || []).map((p) =>
    `<div class="sub">· ${esc(p.role)}：level=${esc(p.level)} tags=${esc((p.risk_tags || []).join(",") || "—")}` +
    ` conf=${esc(p.confidence)}${p.abstain ? " (弃权)" : ""}${p.independent === false ? " (同族复核)" : ""}</div>`).join("");
  const evidence = (rp.evidence || []).map((e) =>
    `<div class="sub">[${esc(e.evidence_id)}] ${esc(e.doc_id)} ${esc(e.version)} score=${esc(e.score)}<br>${esc(e.text)}</div>`).join("");
  const msgs = (d.messages || []).map((m) =>
    `<div class="msg ${esc(m.role)}">${esc(m.content)}</div>`).join("");

  $("detail").innerHTML = `
    ${rp.transcript_deleted ? `<div class="tdel">
      <b>对话原文已被顾客删除。</b>按用户的删除请求，本工单里保存的对话快照与
      AI 草稿已抹除；审查结论（下面的规则命中与面板意见）按合规要求保留。
      所以下面的对话区是空的 —— 这是**预期行为**，不是系统故障。
    </div>` : ""}
    <div class="section">
      <h3>工单</h3>
      <dl class="kv">
        <dt>工单号</dt><dd>${esc(t.ticket_id)}</dd>
        <dt>会话</dt><dd>${esc(t.session_id)}</dd>
        <dt>转人工原因</dt><dd>${esc(t.reason)} · 优先级 ${esc(t.priority)}</dd>
        <dt>人工已接入</dt><dd>${t.human_joined
          ? '<span class="badge ok">是（可以告知用户）</span>'
          : '<span class="badge warn">否 —— 此时不得告知用户"人工已接入"</span>'}</dd>
      </dl>
    </div>
    <div class="section">
      <h3>画像摘要</h3>
      <div class="sub">${esc(t.profile_summary || "（无）")}</div>
    </div>
    <div class="section">
      <h3>风险报告</h3>
      <dl class="kv">
        <dt>审查结论</dt><dd>${esc(rp.verdict || "—")} / 风险等级 ${esc(rp.risk_level || "—")}</dd>
        <dt>同族复核</dt><dd>${rp.same_family_review
          ? '<span class="badge warn">是（MVP 单模型的已知缺口）</span>'
          : esc(String(rp.escalation_independent ?? "—"))}</dd>
      </dl>
      <div style="margin-top:8px">${hits || '<span class="sub">无硬规则命中</span>'}</div>
      <div style="margin-top:8px">${panels || '<span class="sub">无专家意见</span>'}</div>
      ${evidence ? `<h3 style="margin-top:12px">证据</h3>${evidence}` : ""}
      ${rp.draft ? `<h3 style="margin-top:12px">AI 未发出的草稿</h3><div class="msg system">${esc(rp.draft)}</div>` : ""}
    </div>
    <div class="section">
      <h3>对话记录${me && me.role === "service" ? "（敏感信息已脱敏）" : ""}</h3>
      ${msgs || '<div class="sub">（无）</div>'}
    </div>
    <div class="section">
      <h3>处置</h3>
      <div class="tools" style="margin-bottom:10px">
        <button class="primary" id="btnAccept" ${t.human_joined ? "disabled" : ""}>接单并接管</button>
        <button id="btnEscalate">转值班医师</button>
        <button id="btnMisreport">标记误报</button>
        <button class="danger" id="btnClose" ${t.status === "closed" ? "disabled" : ""}>关闭工单</button>
      </div>
      <textarea id="replyText" placeholder="给用户的回复（命中禁发词会被硬拦，命中提示类规则会先征求确认）"></textarea>
      <div class="tools" style="margin-top:8px">
        <button class="primary" id="btnReply">发送回复</button>
        <span class="sub">坐席不能代为执行预约/退费操作，只能引导用户走系统流程</span>
      </div>
    </div>`;

  $("btnAccept").onclick = () => act(`/ops/tickets/${t.ticket_id}/accept`, {}, "已接管，AI 已停用");
  $("btnEscalate").onclick = () => act(`/ops/tickets/${t.ticket_id}/escalate`, { reason: "转医师评估" }, "已升级给值班医师");
  $("btnClose").onclick = () => {
    const reason = prompt("关闭原因（必填）", "已电话沟通处理完成");
    if (reason) act(`/ops/tickets/${t.ticket_id}/close`, { reason }, "工单已关闭，AI 已恢复");
  };
  $("btnMisreport").onclick = () => act(`/ops/tickets/${t.ticket_id}/misreport`,
    { source: "emergency", ref_id: "manual", raw_message: t.profile_summary || "",
      verdict: "false_positive", note: "坐席判定误报" }, "已标记误报，进入词表调优队列");
  $("btnReply").onclick = (e) => sendReply(e.target, t.ticket_id, false);
}
```

### Action + reply senders — `Code/app/ops/panel.html` lines 480–521

```js
async function sendReply(btn, ticketId, ack) {
  const text = $("replyText").value.trim();
  if (!text) return toast("回复内容不能为空");
  btn.disabled = true;
  try {
    const r = await fetch(`/ops/tickets/${ticketId}/reply`, {
      method: "POST", headers: headers(),
      body: JSON.stringify({ text, acknowledge_warnings: ack }),
    });
    const data = await r.json();
    if (r.status === 409 && data.code === "soft_warning") {
      const list = (data.warnings || []).map((w) => w.rule_id + " " + (w.title || "")).join("；");
      if (confirm(`内容命中提示类规则：\n${list}\n\n确认仍要发送？`)) {
        btn.disabled = false;
        return sendReply(btn, ticketId, true);
      }
      btn.disabled = false;
      return;
    }
    if (!r.ok) throw new Error(data.message || r.status);
    $("replyText").value = "";
    toast("回复已发送");
    openTicket(ticketId);
  } catch (e) {
    toast("发送失败：" + e.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function act(url, body, okText) {
  try {
    const r = await fetch(url, { method: "POST", headers: headers(), body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.message || r.status);
    toast(okText);
    await loadQueue();
    if (state.current) openTicket(state.current);
  } catch (e) {
    toast("操作失败：" + e.message, true);
  }
}
```

---

## 16. Toast

Fixed bottom-right notification, with an alert variant used for SLA breach events.

### CSS — `Code/app/ops/panel.html` lines 77–78, 108–109

```css
.toast{position:fixed;right:20px;bottom:20px;max-width:380px;background:var(--ink);color:#fff;
       padding:12px 16px;border-radius:8px;font-size:13px;z-index:20}
```

```css
.toast.alert{background:var(--p0)}
.toast:empty{display:none}
```

### Markup + JS — `Code/app/ops/panel.html` lines 174, 303–310

```html
<div class="toast" id="toast" role="status" aria-live="polite"></div>
```

```js
let toastTimer;
function toast(text, isAlert) {
  const el = $("toast");
  el.textContent = text;
  el.className = "toast" + (isAlert ? " alert" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.textContent = ""; }, 5000);
}
```

---

## 17. Shared request wrapper (per page)

Every component that talks to the backend goes through one page-local helper that centralises the
`Authorization` header, the JSON envelope and the 401 recovery.

`Code/app/web/chat.html` lines 519–544:

```js
function getToken() { return localStorage.getItem(LS_TOKEN) || ""; }

function setToken(t) {
  if (t) localStorage.setItem(LS_TOKEN, t);
  else localStorage.removeItem(LS_TOKEN);
}

/** 统一加 Authorization 头；401 时**清掉令牌并弹回登录**。
 *
 *  ★ 401 集中在这里处理，而不是每个调用点各写一遍 ——
 *    散着写必然会漏掉一两处，表现成"某个操作悄悄失败了"。
 */
async function api(url, opts = {}) {
  const o = {...opts};
  o.headers = {...(opts.headers || {})};
  const t = getToken();
  if (t) o.headers["Authorization"] = "Bearer " + t;
  const r = await fetch(url, o);
  if (r.status === 401) {
    setToken("");
    currentUser = null;
    showAuth("登录已过期，请重新登录");
    throw new Error("unauthorized");
  }
  return r;
}
```

`Code/app/ops/panel.html` lines 181–209:

```js
function headers() {
  // ★ 角色**不再由这里声明**。这里只带令牌，角色由服务端从数据库读。
  //   原来的写法是 X-Agent-Role: $("role").value —— 改个下拉框就能拿到
  //   未脱敏的手机号，那是本项目最严重的一个洞。
  const h = {"Content-Type": "application/json"};
  const t = localStorage.getItem("zhimei.agentToken");
  if (t) h["Authorization"] = "Bearer " + t;
  return h;
}
```

```js
async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (r.status === 401) {
    localStorage.removeItem("zhimei.agentToken");
    showAuth("登录已过期，请重新登录");
    throw new Error("unauthorized");
  }
  return r;
}
```

Storage keys: `zhimei.token` + `zhimei.currentSession` (chat), `zhimei.agentToken` (ops).
