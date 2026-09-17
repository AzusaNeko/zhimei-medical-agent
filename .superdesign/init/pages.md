# Pages

Two pages, two routes, two self-contained files. Neither has framework imports or a build step, so
the "dependency tree" is: **serving route → HTML file → inline `<style>` region → inline `<script>`
region → backend endpoints that script calls**.

| Route | File | Kind |
|---|---|---|
| `GET /chat` | `Code/app/web/chat.html` | Customer chat UI (main product surface) |
| `GET /ops/panel` | `Code/app/ops/panel.html` | Agent workbench / ops console |

> There is **no route at `/`**. `GET /` 404s. See `routes.md` §1.

Transport note: neither page uses `EventSource` or `XMLHttpRequest`. Both read SSE with
`fetch()` + `response.body.getReader()` because `EventSource` cannot send an `Authorization` header
(and cannot POST). Source comments:

```js
/* chat.html:1410 ── SSE 解析（用 fetch + reader：EventSource 不能带自定义头，也无法 POST）── */
/* panel.html:539 ── 实时流（fetch + reader，因为 EventSource 不能带自定义头）── */
```

---

## 1. `/chat` — 智美医美顾问 · 在线咨询

**Entry:** `Code/app/web/chat.html` (83609 bytes, 1765 lines)
**Served by:** `GET /chat` → `Code/app/api/app.py:52-67` (`HTMLResponse`, `Cache-Control: no-store`)
**Layout:** `header.topbar` → auth overlay `#authLayer` → `.wrap` 3-column CSS grid

```
GET /chat
└── Code/app/web/chat.html
    ├── <head> meta (lang="zh-CN", viewport, title "智美医美顾问 · 在线咨询")
    ├── <style>  lines 7–256      ← inline stylesheet, :root tokens at 8–12
    │   ├── base / typography           13–18
    │   ├── .topbar .brand .pill .dot   20–31
    │   ├── .wrap grid + .card shell    33–43
    │   ├── .convs conversation list    45–70
    │   ├── .chat message list          72–97
    │   ├── .statusbar .spin            99–105
    │   ├── .takeover-bar .tk-badge     107–112
    │   ├── .composer textarea .examples .confirm   114–130
    │   ├── .trace .pane #paneGraph #paneTrace      132–139
    │   ├── .pane-head .pane-tools .glegend .gsummary .gchip   140–159
    │   ├── .glayer .gnode .gedge SVG graph         160–186
    │   ├── .legend .nodes .node .nd node output    187–228
    │   ├── .trace-empty .trace-foot .hint          225–229
    │   └── .auth .auth-card .tabs .field           231–255
    ├── <body> shell markup lines 258–394
    │   ├── header.topbar                     260–276
    │   ├── #authLayer overlay (login/register) 279–312
    │   └── .wrap
    │       ├── aside.card.convs + #convList  316–321
    │       ├── section.card.chat + #messages + #statusbar + #takeoverBar + .composer  324–341
    │       └── aside.card.trace
    │           ├── #paneGraph → .pane-head, #gSummary, #gWrap > svg#gSvg   358–373
    │           ├── #paneTrace → .legend                                    375–383
    │           ├── ol#nodes / #traceFoot / #traceHint                      384–392
    └── <script> lines 396–1763
        ├── NODES metadata table (55 nodes: id → 中文名/group/llm/描述)   403–464
        ├── GROUP_COLOR {main,kb,risk}                                    465
        ├── EXAMPLES[5] example questions                                 467–473
        ├── helpers $ el, state vars (sid/busy/pendingPlan/conversations…) 475–514
        ├── auth: getToken/setToken/api()/showAuth/hideAuth               516–556
        ├── auth: doLogin / doRegister / switchTab / afterLogin / logout  558–682
        ├── conversation list: relTime/loadConversations/renderConversations/
        │   deleteConversation/switchTo                                   684–804
        ├── message render: msgNode/setComposeEnabled/takeover sync/
        │   markSeen/renderHistory/newChat                                806–915
        ├── topbar setConn + addMsg                                       917–939
        ├── typewriter: TYPE_MS/TYPE_MIN_CHUNK/TYPE_MIN_LEN/
        │   prefersReducedMotion/typingEligible/addMsgTyped               941–1023
        ├── setStatus / clearConfirm / clearTrace                         1025–1042
        ├── workflow graph: graphData/graphPos/graphVisited/graphView,
        │   GNODE_W/H/GAP, LAYER_H/PAD, graphLayout, graphEl, graphDraw,
        │   graphSummary, graphMark/Reset/Ensure, zoom buttons            1044–1315
        ├── trace rows: addNode / finishTrace                             1317–1408
        ├── polling: WATCH_MS, startWatch/stopWatch/watchOnce/applyPoll/
        │   resyncSeen                                                    1410–1508
        ├── stream(): POST + reader SSE loop; handle() event switch        1510–1662
        ├── send()                                                        1664–1686
        ├── greeting() opening message                                    1688–1693
        ├── EXAMPLES chip build + event wiring (send/newChat/logout/tabs/
        │   forms/Enter-to-send)                                          1695–1710
        ├── boot: GET /api/health → #profilePill + conn dot               1712–1715
        └── boot(): token → GET /api/auth/me → hideAuth + afterLogin()    1717–1762
```

### Backend endpoints `chat.html` actually calls

Every one of these is a literal string in `chat.html`; `api()` (line 531) adds the
`Authorization: Bearer <zhimei.token>` header and handles 401 centrally.

| Line | Method | Path | Called from | Purpose |
|---|---|---|---|---|
| 1712 | GET | `/api/health` | IIFE boot | fill `#profilePill` ("档位 " + profile + " · " + checkpointer), set conn dot "已连接" / "离线" |
| 1730 | GET | `/api/auth/me` | IIFE `boot()` | validate stored token before rendering anything |
| 566 | POST | `/api/auth/login` | `doLogin()` | email+password → `access_token`, `user`; maps `bad_credentials` / `email_not_verified` / `user_disabled` |
| 604 | POST | `/api/auth/register` | `doRegister()` | email+password+display_name; takes `verify_token` from the response |
| 618 | POST | `/api/auth/verify-email` | `doRegister()` | auto-verifies with the returned token, then logs in |
| 702 | GET | `/api/sessions?channel=web&limit=40` | `loadConversations()` | left-column history list |
| 1676 | POST | `/api/sessions` | `send()` | lazy-create the session on the first message (`{channel:"web"}`) |
| 794 | GET | `/api/sessions/{session_id}?limit=60` | `switchTo()` | history redraw when switching conversation |
| 1441 | GET | `/api/sessions/{session_id}?limit=60` | `watchOnce()` | 3 s polling so agent replies appear without a refresh |
| 1500 | GET | `/api/sessions/{session_id}?limit=60` | `resyncSeen()` | realign "last seen message" after a stream ends |
| 763 | DELETE | `/api/sessions/{session_id}` | `deleteConversation()` | hard delete; 409 `session_has_open_ticket` is surfaced as a special tag |
| 1284 | GET | `/api/graph` | `graphEnsure()` | workflow topology that drives the SVG pane |
| 1684 → 1517 | POST | `/api/chat/{session_id}/stream` (+`?trace=1` when `#traceOn` is checked) | `send()` → `stream()` | ★ one SSE round: `node` / `status` / `final` / `awaiting_confirmation` / `handoff` / `human_takeover` / `blocked` / `error` / `done` |
| 1602, 1608 → 1517 | POST | `/api/chat/{session_id}/confirm` (+`?trace=1`) | `.confirm` bar buttons in `handle()` | resume the suspended graph with `{confirmed, plan_hash}` |

SSE query parameter is conditional and built in one place:

```js
async function stream(path, body) {
  const useTrace = $("traceOn").checked;
  const url = path + (useTrace ? "?trace=1" : "");
```

### Files it does NOT depend on

No `src/`, no bundler entry, no `.css`, `.js`, `.ts` side files, no CDN `<script src>`, no fonts, no
images, no icons — a single self-contained file. `Code/pyproject.toml` ships it as package data:

```toml
app = ["config/*.yaml", "ops/*.html", "web/*.html"]
```

---

## 2. `/ops/panel` — 坐席工作台 · 高风险回复处理

**Entry:** `Code/app/ops/panel.html` (33028 bytes, 690 lines)
**Served by:** `GET /ops/panel` → `Code/app/ops/routes.py:341-347` (`HTMLResponse`,
`Cache-Control: no-store`; 500 with `<h1>panel.html 缺失</h1>` if missing)
**Layout:** sticky `header.topbar` → auth overlay `#authLayer` → `.metrics` KPI strip → `.layout`
2-pane grid → fixed `.toast`

```
GET /ops/panel
└── Code/app/ops/panel.html
    ├── <head> meta (lang="zh-CN", viewport, title "智美医美顾问 · 坐席工作台")
    ├── <style>  lines 7–111      ← inline stylesheet, :root tokens at 8–9
    │   ├── :root + base + body              8–13
    │   ├── .topbar .brand .tools + controls 14–31
    │   ├── .metrics .metric                 32–36
    │   ├── .layout .pane .pane>h2 .pane-body 37–43
    │   ├── .row .badge .tdel                44–63
    │   ├── .section .kv .msg .hit .empty .toast 64–78
    │   ├── .auth .auth-card .auth-msg .auth-tip 80–96
    │   ├── .whoami .role-badge .raw-flag .testtoggle 97–103
    │   ├── .badge.test .row.is-test .toast.alert 104–109
    │   └── @media(max-width:1000px)         110
    ├── <body> shell markup lines 113–174
    │   ├── header.topbar (brand + #whoami + dot + #showTest + #purgeTest + #refresh + #logout) 114–138
    │   ├── #authLayer → form#loginForm     141–158
    │   ├── div#metrics                     160
    │   ├── .layout
    │   │   ├── section.pane → h2 + #queueCount + #queue      163–166
    │   │   └── section.pane → h2 + #detailTitle + #detail    168–171
    │   └── div#toast                       174
    └── <script> lines 176–688
        ├── state {tickets, current, detail}, $ helper                178–179
        ├── headers() — bearer token only, role comes from the server 181–189
        ├── me / myPerms / detailMsgCount / ACTIVE_STATUSES           191–199
        ├── api() + showAuth/hideAuth                                 201–222
        ├── doLogin / applyIdentity / logout / bootData               224–280
        ├── keepTokenBoot + event wiring + toast                      282–310
        ├── esc() / fmtWait()                                         311–313
        ├── loadQueue() / purgeTest() / renderQueue()                 315–376
        ├── openTicket() / renderDetail()                             378–478
        ├── sendReply() / act()                                       480–521
        ├── renderMetrics()                                           523–537
        ├── connect() SSE with AbortController + handleEvent()         539–602
        ├── maybeRefreshDetail()                                      604–638
        ├── #refresh handler / loadMetrics()                          640–647
        └── start(): token → GET /ops/auth/me → applyIdentity + bootData 649–687
```

### Backend endpoints `panel.html` actually calls

`headers()` (line 181) supplies `Content-Type: application/json` plus
`Authorization: Bearer <zhimei.agentToken>` from `localStorage`.

| Line | Method | Path | Called from | Purpose |
|---|---|---|---|---|
| 663 | GET | `/ops/auth/me` | IIFE `start()` | validate agent token; returns `agent` + `permissions` |
| 229 | POST | `/ops/auth/login` | `doLogin()` | email+password → `access_token`, `agent`, `permissions` |
| 319 | GET | `/ops/tickets?include_test={true\|false}` | `loadQueue()` | queue table (only pending statuses) |
| 335 | DELETE | `/ops/tickets/test` | `purgeTest()` | remove `is_test=true` tickets only (needs `admin:purge`) |
| 389 | GET | `/ops/tickets/{ticket_id}` | `openTicket()` | detail: ticket + profile summary + risk report + messages |
| 468 | POST | `/ops/tickets/{ticket_id}/accept` | `#btnAccept` → `act()` | 接单并接管 (disables the AI) |
| 469 | POST | `/ops/tickets/{ticket_id}/escalate` | `#btnEscalate` → `act()` | 转值班医师, body `{reason:"转医师评估"}` |
| 472 | POST | `/ops/tickets/{ticket_id}/close` | `#btnClose` → `act()` | close with a required `reason` (prompt) |
| 474 | POST | `/ops/tickets/{ticket_id}/misreport` | `#btnMisreport` → `act()` | mark false positive (`source/ref_id/raw_message/verdict/note`) |
| 485 | POST | `/ops/tickets/{ticket_id}/reply` | `sendReply()` | agent reply; 409 `soft_warning` → confirm → resend with `acknowledge_warnings:true` |
| 512 | POST | *(the url passed to `act()`)* | `act()` | shared sender for accept/escalate/close/misreport |
| 550 | GET | `/ops/stream?include_test={true\|false}` | `connect()` | SSE queue snapshot + SLA alerts via `fetch` + reader + `AbortController` |
| 644 | GET | `/ops/metrics` | `loadMetrics()` | KPI strip |

SSE handling and the only two event names the panel understands:

```js
function handleEvent(chunk) {
  let name = "message", data = "";
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7);
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!data) return;
  const payload = JSON.parse(data);
  if (name === "snapshot") {
    state.tickets = payload.tickets || [];
    renderQueue();
    renderMetrics(payload.metrics);
    maybeRefreshDetail();
  } else if (name === "alert") {
    toast(`⚠ ${payload.message}`, true);
  }
}
```

### Files it does NOT depend on

Same as the chat page: single self-contained file, no side `.css`/`.js`, no CDN, no images, no
fonts. Shares nothing with `chat.html` at the file level — the duplicated `:root` palette, `.topbar`,
`.auth`, `.msg`, `button` and `esc()`/`toast()` helpers are **copies**, not imports. That duplication
is what makes these two pages the natural source set for shared draft components
(see `extractable-components.md`).

---

## 3. Cross-page comparison

| Concern | `/chat` (chat.html) | `/ops/panel` (panel.html) |
|---|---|---|
| Shell | `.wrap` 3-col grid: convs / chat / trace | `.metrics` strip + `.layout` 2-col grid |
| Card class | `.card` (+ `.card>h2`) | `.pane` (+ `.pane>h2`, `.pane-body`) |
| Header | static `.topbar` | `position:sticky` `.topbar` with `.tools` |
| Auth overlay z-index | 50 | 60 |
| Auth form | Login + Register tabs, 2 forms | single login form |
| token storage key | `zhimei.token`, `zhimei.currentSession` | `zhimei.agentToken` |
| Auth endpoints | `/api/auth/*` | `/ops/auth/*` |
| Realtime inbound | `POST /api/chat/{sid}/stream` SSE via `stream()` | `GET /ops/stream` SSE via `connect()` |
| Realtime outbound | 3 s poll of `GET /api/sessions/{sid}` | `AbortController` reconnect after 3 s |
| Escape helper | none (uses `textContent` / `el()`) | `esc()` for `innerHTML` templates |
| Notifications | inline `.msg.sys` / `.msg.err` bubbles + `.statusbar` | floating `.toast` (+ `.toast.alert`) |
| Breakpoints | 1360px (hide trace), 900px (single col) | 1000px (single col) |
| Total size | 1765 lines | 690 lines |
