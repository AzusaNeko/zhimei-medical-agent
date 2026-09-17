# Extractable Components

Catalog of UI that can become reusable Superdesign `DraftComponent` entities.

**Source of truth for extraction:** because both pages are single-file, the "source path" is the file
plus the exact line ranges of the CSS rule and the markup/JS that builds the component. Full code is
in `components.md`; this file is the **menu** — name, path, category, props, hardcoded parts only.

Two classes of component exist here and both are extractable:

- **static-markup components** — the literal HTML in the page `<body>` (topbar, panes, composer shell);
- **JS-built components** — DOM constructed by the inline `<script>` (conversation rows, message
  bubbles, trace rows, SVG graph nodes, queue rows). Their props are the JS locals they read.

Prop rule applied below: only **state / navigation / visibility / count** values are props.
Labels, icon glyphs, class names, colours and copy are hardcoded.

---

## Layout Components

### AppTopbar
- Source: `Code/app/web/chat.html` (markup 260–276; CSS 20–31, 254–255)
- Category: layout
- Description: Sticky-free white bar with brand block on the left and connection/profile/trace-toggle/identity cluster on the right.
- Extractable props: `connectionState` (`""` | `"on"` | `"busy"`), `connectionText` (string, default `"未连接"`), `profileLabel` (string, default `"档位 —"`), `traceToggleChecked` (boolean, default `true`), `isAuthenticated` (boolean, controls `#whoBox` `hidden`), `userName` (string, default `"—"`)
- Hardcoded: `LANGGRAPH MULTI-AGENT` eyebrow, `智美医美顾问 · 在线咨询` title, `显示执行轨迹` label, `退出` button text, `.topbar`/`.brand`/`.eyebrow`/`.right`/`.pill`/`.dot`/`.who` CSS, `1.6px` letter-spacing, `#fff` surface

### OpsTopbar
- Source: `Code/app/ops/panel.html` (markup 114–138; CSS 14–31, 97–103)
- Category: layout
- Description: Sticky ops header with identity cluster, live-connection dot, test-ticket toggle and refresh/logout actions.
- Extractable props: `agentName` (string, default `"—"`), `agentRole` (string, default `"—"`), `seesRawPii` (boolean, drives `#rawFlag`), `canPurge` (boolean, drives `#purgeTest`), `showTestTickets` (boolean, drives `#showTest`), `connectionState` (`""` | `"on"`), `connectionText` (string), `isAuthenticated` (boolean, drives `#logout`), `pageTitle` (string)
- Hardcoded: `ZHIMEI / OPS CONSOLE` eyebrow, `坐席工作台 · 高风险回复处理` title, `显示测试工单` label, `清理测试工单` / `刷新` / `退出` button text, `可见明文 PII` flag text, all tooltip strings, `.whoami`/`.role-badge`/`.raw-flag`/`.testtoggle` CSS

### AuthOverlay
- Source: `Code/app/web/chat.html` (markup 279–312; CSS 231–253) — chat variant
- Source: `Code/app/ops/panel.html` (markup 141–158; CSS 80–96) — ops variant
- Category: layout
- Description: Full-viewport blurred backdrop (`position:fixed;inset:0`) holding a centred auth card; chat variant has Login/Register tabs, ops variant is a single login form.
- Extractable props: `visible` (boolean, drives the `hidden` attribute), `activeTab` (`"login"` | `"reg"`, chat only), `message` (string, `#authMsg`), `messageKind` (`""` | `"err"` | `"ok"`), `submitting` (boolean)
- Hardcoded: `智美医美顾问` / `坐席登录` headings, all form labels (`邮箱`, `密码`, `昵称（可空）`, `密码（至少 10 位）`), placeholder text `you@example.com` / `••••••••` / `怎么称呼您`, submit button text `登录` / `注册`, the demo-credential `.auth-tip` contents (`demo@zhimei.test`, `service@zhimei.test`, `zhimei-demo-2026`, …), `.tabs`, `.field`, `.auth-card` CSS, `rgba(23,47,64,.45)` / `.5` backdrop, `blur(3px)`, z-index 50 vs 60

### ConversationSidebar
- Source: `Code/app/web/chat.html` (markup 316–321; CSS 45–70; builder `renderConversations()` 710–739)
- Category: layout
- Description: Left 236 px card column holding the history list plus a `＋ 新对话` action in its header.
- Extractable props: `conversations` (array), `activeConversationId` (string | null), `empty` (boolean)
- Hardcoded: `对话` header label, `＋ 新对话` button text, empty-state copy `还没有历史对话。<br>发第一条消息就会出现在这里。`, `.convs`/`.conv-list`/`.conv-empty` CSS, `height:calc(100vh - 132px)`, `min-height:560px`

### ChatColumn
- Source: `Code/app/web/chat.html` (markup 324–341; CSS 72–130)
- Category: layout
- Description: Centre card column — header with session label, scrollable message list, status bar, takeover bar and composer, stacked in one flex column.
- Extractable props: `sessionLabel` (string, default `"新对话"`), `statusText` (string | null), `takeoverActive` (boolean), `composerEnabled` (boolean), `placeholder` (string)
- Hardcoded: `对话` header label, `.chat` height rule, the stacking order (messages → statusbar → takeover-bar → composer), all inner component CSS

### TraceSidebar
- Source: `Code/app/web/chat.html` (markup 344–393; CSS 132–139)
- Category: layout
- Description: Right card column that stacks two independent scroll windows — the workflow graph on top (fixed 46 %) and the execution trace below (takes the rest).
- Extractable props: `traceMeta` (string, default `"等待提问"`), `graphVisible` (boolean), `traceVisible` (boolean), `graphSummaryInline` (string, default `"等待提问"`), `footerVisible` (boolean), `hintVisible` (boolean)
- Hardcoded: `Agent 执行轨迹` header, `#paneGraph{flex:0 0 auto;height:46%}` / `#paneTrace{flex:1 1 auto}` split, `2px` divider between the two, the long explanatory HTML comments about why the two views share a screen

### PaneShell (Card / Pane)
- Source: `Code/app/web/chat.html` CSS 39–43 (`.card`, `.card>h2`) and `Code/app/ops/panel.html` CSS 39–43 (`.pane`, `.pane>h2`, `.pane-body`)
- Category: layout
- Description: The one shared surface primitive — white card, 1 px border, 12 px radius, grey header strip with an optional monospace right-hand `.sub` label.
- Extractable props: `title` (string), `sub` (string), `bodyScrolls` (boolean, `panel.html` variant uses `.pane-body`), `fixedHeight` (boolean)
- Hardcoded: `border:1px solid var(--line)`, `border-radius:12px`, `#fbfcfd` header background, `display:flex;flex-direction:column;overflow:hidden`, header font sizes (14px chat / 15px panel), header padding (12px 18px chat / 14px 18px panel)

### OpsMetricsStrip
- Source: `Code/app/ops/panel.html` (markup 160; CSS 32–36; builder `renderMetrics()` 524–537)
- Category: layout
- Description: Auto-fit KPI grid (`minmax(140px,1fr)`) above the two-pane work area; 7 fixed cells.
- Extractable props: `metrics` (object: `tickets_open`, `tickets_p0_open`, `tickets_accepted`, `first_pass_rate`, `avg_wait_seconds`, `misreport_pending`, `escalation_same_family`)
- Hardcoded: the 7 cell labels `待处理工单` / `其中 P0` / `已接单` / `一次通过率` / `平均等待` / `误报待复核` / `同族复核次数`, the `—` fallback, the `%` and `s` suffixes, `.metrics`/`.metric` CSS, `max-width:1560px`

### OpsQueuePane
- Source: `Code/app/ops/panel.html` (markup 163–166; CSS 37–43; builder `renderQueue()` 349–376)
- Category: layout
- Description: Left detail-list pane (min 320 px) whose body renders the ticket queue rows.
- Extractable props: `tickets` (array), `selectedTicketId` (string | null), `countLabel` (string, computed as `` `共 ${n} 条待处理` ``), `loading` (boolean), `empty` (boolean)
- Hardcoded: `待处理队列` header, `加载中…` initial body, empty copy `当前没有待处理工单（已结束的工单不在队列里）`, `.pane-body{overflow:auto;flex:1}`

### OpsDetailPane
- Source: `Code/app/ops/panel.html` (markup 168–171; builder `renderDetail()` 399–478)
- Category: layout
- Description: Right pane (min 420 px, `1.35fr`) rendering the opened ticket — kv blocks, risk report, transcript and the disposition action row.
- Extractable props: `selectedTicketId` (string | null), `detailTitle` (string, computed as `` `${priority} · ${reason} · ${status}` ``), `loading`, `loadError` (string | null)
- Hardcoded: `工单详情` header, `从左侧选择一个工单` placeholder, `加载失败：` prefix, `.layout` grid tracks `minmax(320px,1fr) minmax(420px,1.35fr)`, 1000 px breakpoint

---

## Basic Components

### BrandBlock
- Source: `Code/app/web/chat.html` 261–264, CSS 23–25 · `Code/app/ops/panel.html` 115–118, CSS 17–19
- Category: basic
- Description: Two-line brand lockup — monospace uppercase eyebrow over a bold Chinese title.
- Extractable props: `eyebrow` (string), `title` (string)
- Hardcoded: `.brand{display:flex;flex-direction:column}`, eyebrow `11px Consolas`, `color:var(--accent)`, letter-spacing `1.6px`/`1.5px`, title `16px`/`15px`

### StatusPill
- Source: `Code/app/web/chat.html` 266–267, CSS 27
- Category: basic
- Description: Rounded 999 px pill used for connection state and the profile tier; can host a leading dot.
- Extractable props: `label` (string)
- Hardcoded: `border:1px solid var(--line)`, `border-radius:999px`, `padding:3px 10px`, `background:#fbfcfd`

### ConnectionDot
- Source: `Code/app/web/chat.html` CSS 28–31; set by `setConn()` 918–921 · `Code/app/ops/panel.html` CSS 30–31
- Category: basic
- Description: 8 px round indicator with idle / on / busy states; busy pulses.
- Extractable props: `state` (`""` | `"on"` | `"busy"`)
- Hardcoded: `width:8px;height:8px;border-radius:50%`, idle `#c3ced5` (chat) / `var(--p2)` (ops), on `var(--ok)`, busy `var(--warn)` + `pulse 1s infinite`, `@keyframes pulse{50%{opacity:.3}}`

### ButtonPrimary
- Source: `Code/app/web/chat.html` CSS 120–124 · `Code/app/ops/panel.html` CSS 24–29
- Category: basic
- Description: The one accent-filled action button; base `button` is the secondary style.
- Extractable props: `disabled` (boolean), `label` (string), `fullWidth` (boolean, used by `.auth-card .submit`)
- Hardcoded: `background:var(--accent)`, `border-color:var(--accent)`, `color:#fff`, hover `var(--accent2)` (chat) / `#1b554e` (ops), radius 8 px chat vs 6 px ops, padding `9px 15px` chat vs `7px 13px` ops, `opacity:.45` disabled

### ButtonDanger
- Source: `Code/app/ops/panel.html` CSS 28
- Category: basic
- Description: Destructive-action outline button (关闭工单).
- Extractable props: `disabled` (boolean), `label` (string)
- Hardcoded: `border-color:#d99a8c`, `color:#8d3024`, shares base `button` box

### PillButton
- Source: `Code/app/web/chat.html` CSS 48–49, 128
- Category: basic
- Description: Small 999 px-radius button used for `＋ 新对话` and the example-question chips.
- Extractable props: `label` (string), `title` (full text when the label is truncated)
- Hardcoded: `border-radius:999px`, `background:#eef5f3` + `border-color:#8fbfb3` + `color:var(--accent)` (new-chat variant) vs `background:#f7faf9` (chip variant), `font-size:12.5px`

### TabSwitch
- Source: `Code/app/web/chat.html` 283–286, CSS 239–242; `switchTab()` 642–649
- Category: basic
- Description: Underline tab pair for 登录 / 注册 inside the auth card.
- Extractable props: `active` (`"login"` | `"reg"`), `tabs` (array of `{id, label}`)
- Hardcoded: `登录` / `注册` labels, `border-bottom:2px solid transparent`, active `border-bottom-color:var(--accent)` + `color:var(--accent)` + `font-weight:600`, `gap:6px`

### TextField
- Source: `Code/app/web/chat.html` 289–302, CSS 243–246 · `Code/app/ops/panel.html` 146–149, CSS 21–23, 88–91
- Category: basic
- Description: Label-over-input field stack; inputs and the chat textarea share one focus ring.
- Extractable props: `label` (string), `type` (`email` | `password` | `text`), `value` (string), `placeholder` (string), `autocomplete` (string), `maxlength` (number | null), `required` (boolean)
- Hardcoded: `.field{display:flex;flex-direction:column;gap:5px;margin-bottom:12px}`, label `12.5px` `var(--muted)`, input `padding:9px 11px`, `border-radius:8px`, focus `border-color:#8fbfb3` + `box-shadow:0 0 0 3px #e8f2f0`, the Chinese labels `邮箱` / `密码` / `昵称（可空）` / `密码（至少 10 位）`

### ConversationListItem
- Source: `Code/app/web/chat.html` builder 718–738, CSS 51–69; `relTime()` 687–698
- Category: basic
- Description: History row — truncated title, monospace meta line (relative time, message count, takeover badge) and a hover-revealed delete button; active rows get an accent left bar.
- Extractable props: `title` (string, default `"（新对话 · 还没说话）"`), `active` (boolean), `unread` (boolean), `lastActiveAt` (ISO string → relative label), `messageCount` (number | null), `aiEnabled` (boolean → shows the 人工接管 badge)
- Hardcoded: `border-left:3px solid transparent` / active `border-left-color:var(--accent)`, hover `#f4f9f8`, active `#eaf4f2`, `条` suffix after the count, `人工接管` badge text, relative-time strings `刚刚` / ` 分钟前` / ` 小时前` / `昨天`, `MM-DD` fallback format, `.cv-badge` blue/red palettes

### DeleteIconButton
- Source: `Code/app/web/chat.html` builder 731–734, CSS 56–61
- Category: basic
- Description: `✕` icon button pinned to the row's top-right; `opacity:0` until row hover, and its click is `stopPropagation()`-guarded so it never triggers the row's switch handler.
- Extractable props: `visible` (boolean), `title` (string, default `"删除这个对话"`)
- Hardcoded: `✕` glyph, `position:absolute;top:7px;right:10px`, `20×20`, `border-radius:5px`, hover `#ffe3de` / `#8a2a1e` / `#f0c9c2`, `transition:opacity .12s`

### ChatBubble
- Source: `Code/app/web/chat.html` builder `addMsg()` 924–939 + `addMsgTyped()` 981–1023, CSS 75–92
- Category: basic
- Description: Message bubble with four roles — user (right, accent), bot (left, pale green), sys (centred amber), err (centred red) — plus a `.typing` typewriter state.
- Extractable props: `kind` (`"user"` | `"bot"` | `"sys"` | `"err"`), `text` (string), `meta` (array of string | `{text, cls}`), `typing` (boolean)
- Hardcoded: `max-width:88%` (sys/err `96%`), `padding:10px 14px`, `border-radius:12px` with a 3 px tail on the speaker's corner, `white-space:pre-wrap`, `word-break:break-word`, `animation:rise .18s ease-out`, role palettes (`var(--accent)`/`#fff`; `#f1f6f5`/`#dbe8e5`; `#fff8e8`/`#f0e0bb`/`#6d4c08`; `#ffeceb`/`#f3cfcb`/`#8a2a1e`), the `▍` caret, `@keyframes rise`, `@keyframes caret`

### MessageMeta
- Source: `Code/app/web/chat.html` built inside `addMsg()` 927–935 and `addMsgTyped()` 990–997, CSS 84–87
- Category: basic
- Description: Block-level monospace footer inside a bubble carrying tag chips and plain string fragments separated by two spaces.
- Extractable props: `items` (array of string | `{text, cls}`), `role` (drives meta colour: user `#cfe6e2`, bot `var(--muted)`)
- Hardcoded: `display:block;margin-top:7px`, `font:11px Consolas,monospace`, `letter-spacing:.2px`, the separator (two `document.createTextNode("  ")`)

### MetaTag
- Source: `Code/app/web/chat.html` CSS 93–97
- Category: basic
- Description: Inline monospace chip used for message provenance labels (`已审正文`, `坐席`, `人工接管`, `转人工`, `等待确认`, `阻断`, `空会话`, `开场白`, `AI 已恢复`).
- Extractable props: `text` (string), `variant` (`""` | `"ok"` | `"warn"` | `"bad"`)
- Hardcoded: `padding:1px 7px`, `border-radius:4px`, `margin-right:6px`, `font:11px Consolas,monospace`, palettes blue `#e8f1fb`/`#17405f`, ok `#e6f4ee`/`#1a5044`, warn `#fff4dd`/`#6d4c08`, bad `#ffe3de`/`#8a2a1e`

### OpsBadge
- Source: `Code/app/ops/panel.html` CSS 48–54, 105; used in `renderQueue()` 360–364 and `renderDetail()` 427–442
- Category: basic
- Description: Non-wrapping monospace badge for priority, status, SLA breach, test flag and permission verdicts.
- Extractable props: `text` (string), `variant` (`"p0"` | `"p1"` | `"p2"` | `"st"` | `"warn"` | `"ok"` | `"test"`)
- Hardcoded: `font:11px/1.4 Consolas,monospace`, `padding:2px 7px`, `border-radius:4px`, `white-space:nowrap`, the seven palettes, label texts `测试` and `已超时`

### TypingCursor
- Source: `Code/app/web/chat.html` CSS 88–92, gated by `typingEligible()` 976–978
- Category: basic
- Description: Blinking `▍` after the last character while the reviewed body types itself out; the bubble is clickable to finish immediately.
- Extractable props: `active` (boolean), `clickToFinish` (boolean)
- Hardcoded: `content:"▍"`, `color:var(--accent)`, `animation:caret .9s steps(1) infinite`, `cursor:pointer`, the timing constants `TYPE_MS=900`, `TYPE_MIN_CHUNK=2`, `TYPE_MIN_LEN=24` and the `prefers-reduced-motion` bypass

### StatusBar
- Source: `Code/app/web/chat.html` markup 327, CSS 100–102, `setStatus()` 1025–1028
- Category: basic
- Description: Hidden-by-default progress strip above the composer showing the current server `status` text next to a spinner.
- Extractable props: `visible` (boolean), `text` (string)
- Hardcoded: `display:none` → `.show{display:flex}`, `border-top:1px solid var(--line)`, `background:#fbfcfd`, `padding:9px 18px`, `font-size:13px`, `gap:9px`

### Spinner
- Source: `Code/app/web/chat.html` CSS 103–105
- Category: basic
- Description: 13 px accent-topped ring spinner.
- Extractable props: none
- Hardcoded: `width:13px;height:13px`, `border:2px solid #cfdde3`, `border-top-color:var(--accent)`, `animation:spin .7s linear infinite`, `@keyframes spin{to{transform:rotate(360deg)}}`

### TakeoverBar
- Source: `Code/app/web/chat.html` markup 329–331, CSS 108–112, `syncTakeover()` 851–875
- Category: basic
- Description: Amber strip telling the customer that the AI is paused and messages are being routed to a human — the composer deliberately stays enabled.
- Extractable props: `visible` (boolean), `text` (string, default `人工客服处理中：AI 已暂停自动回复，你发的消息会直接转给客服。`)
- Hardcoded: `人工接管` badge text, `background:#fdf7ec`, `color:#7a5a1e`, `.tk-badge` `#f6e2b8`/`#6b4a12`, the takeover placeholder `继续补充情况，客服会看到…`, the two system-bubble strings emitted on state flip

### Composer
- Source: `Code/app/web/chat.html` markup 332–340, CSS 115–119, `send()` 1665–1686
- Category: basic
- Description: Bottom input area — auto-flexing textarea, primary send button, and Enter-to-send / Shift+Enter-to-newline wiring.
- Extractable props: `value` (string), `placeholder` (string, default `说说你想了解的项目，或要办的事…（Enter 发送，Shift+Enter 换行）`), `enabled` (boolean), `sending` (boolean)
- Hardcoded: `发送` button label, `.composer{border-top:1px solid var(--line);padding:12px 18px;background:#fff}`, `textarea{flex:1;min-height:52px;max-height:150px;resize:vertical}`, `border-radius:9px`, the focus ring, the `Enter`/`Shift+Enter` keydown handler

### ExampleChipRow
- Source: `Code/app/web/chat.html` markup 337–339, CSS 125–128, builder 1695–1700
- Category: basic
- Description: Row of pill buttons that prefill the composer and send, built from the `EXAMPLES` array.
- Extractable props: `examples` (array of strings), `prefillOnly` (boolean)
- Hardcoded: the `试试：` lead-in, the 5 example questions (`热玛吉和超声炮有什么区别？`, `帮我把热玛吉的预约改到浦东店周五下午`, `我做完水光第三天，现在脸发白还特别疼，眼睛也有点看不清`, `那个怎么样`, `你们的医生有执业资质吗？`), the `>18 chars → slice(0,17) + "…"` truncation rule, `border-top:1px solid #eef2f5`, `margin-top:12px`

### ConfirmActionBar
- Source: `Code/app/web/chat.html` built in `handle()` 1596–1613, CSS 129–130, cleared by `clearConfirm()` 1029–1032
- Category: basic
- Description: Inline two-button bar attached to an `awaiting_confirmation` system bubble — 确认执行 / 取消; the confirmed plan hash is echoed back.
- Extractable props: `planText` (string), `planHash` (string), `visible` (boolean)
- Hardcoded: `确认执行` / `取消` button labels, the `以下操作需要你确认后才会执行：` prompt, `等待确认` tag, `方案哈希 ` + first 12 chars meta, `.confirm{display:flex;gap:9px;margin-top:10px}`, the `document.querySelectorAll(".confirm").forEach(n => n.remove())` teardown

### TraceNodeRow
- Source: `Code/app/web/chat.html` builder `addNode()` 1317–1377, CSS 192–210
- Category: basic
- Description: Grid row per executed node — index, caret, Chinese node name, raw node id, LLM chip, duration — colour-barred by graph group and highlighted while it is the newest.
- Extractable props: `nodeId` (string), `seq` (number), `ms` (number), `group` (`"main"` | `"kb"` | `"risk"`), `callsLlm` (boolean), `displayName` (string), `hasDetail` (boolean, adds `.empty`), `hot` (boolean), `open` (boolean)
- Hardcoded: `grid-template-columns:26px 1fr auto`, `padding:6px 18px`, `border-left:3px solid transparent`, group bar colours (`var(--g-kb)` / `var(--g-risk)` / `#e3eaee`) and row tints (`#f7fafd` / `#fdfaf6`), hot `#eaf4f2`, `▶` caret with `rotate(90deg)` when open, `LLM` chip `#fff4dd`/`#6d4c08`, `ms` suffix `ms`, `.empty .nm b{color:#9fb0bb}`, the `animation:rise .16s ease-out`

### NodeDetailPanel
- Source: `Code/app/web/chat.html` built in `addNode()` 1338–1360, CSS 213–224
- Category: basic
- Description: Expandable panel under a trace row that dumps the node's raw state patch as key/value rows, headed by an amber "unreviewed intermediate output" warning.
- Extractable props: `description` (string), `detail` (object), `empty` (boolean), `visible` (boolean)
- Hardcoded: the warning copy `调试信息：以下为该节点写回的原始状态，可能包含未经审查的中间产物，勿对顾客展示`, the empty copy `（该节点没有写出状态 —— 它只做判断或只调用模型）`, `margin:0 0 4px 26px`, `border-left:3px solid var(--line)`, `border-radius:0 8px 8px 0`, `grid-template-columns:132px 1fr`, `<pre class="nd-val">` with `JSON.stringify(v, null, 1)` for objects, `#2b4256` value colour

### GraphSummaryChips
- Source: `Code/app/web/chat.html` builder `graphSummary()` 1218–1255, CSS 152–159
- Category: basic
- Description: "本次对话经过：" block with one chip per architecture layer and one per subgraph, ticked when traversed, plus a note line stating the executed node total.
- Extractable props: `layers` (array of `{id, title, nodes}`), `subgraphs` (array of `{id, title, nodes}`), `visited` (map nodeId → `{seq, ms}`), `warnings` (array of strings), `inlineSummary` (string, e.g. `本次 12 个节点 · 1 个子图`), `waiting` (boolean → `等待提问`)
- Hardcoded: the `本次对话经过：` heading, `✓` / `·` glyphs, `共执行 N 个节点` note, `（架构与代码一致）` / `⚠ ` warning prefixes, `［本次走过 N 个节点］` layer suffix, `（${n} 步：…）` subgraph suffix with the first-4-nodes + `…` rule, the `L1` special case, `.gchip` palettes

### GraphLegend
- Source: `Code/app/web/chat.html` markup 377–382, CSS 187–190 · `Code/app/ops/panel.html` has no legend
- Category: basic
- Description: Swatch key for the three graph groups plus the "burns tokens" amber swatch.
- Extractable props: `items` (array of `{label, color, borderColor?}`)
- Hardcoded: `主图` / `科普子图` / `风险审查子图` / `会调用大模型（烧 token）` labels, `var(--g-main)` / `var(--g-kb)` / `var(--g-risk)` / `#fff4dd` + `#e6d3a8` swatch values, `width:9px;height:9px;border-radius:3px`

### TraceSummaryFooter
- Source: `Code/app/web/chat.html` built in `finishTrace()` 1378–1408, CSS 226–228
- Category: basic
- Description: Footer strip after a round completes: node count, LLM calls, subgraph counts, accumulated inter-event time, and an error/handoff note.
- Extractable props: `nodeCount` (number), `llmCount` (number), `kbCount` (number), `riskCount` (number), `totalMs` (number), `ok` (boolean), `visible` (boolean)
- Hardcoded: labels `节点数` / `调用大模型` / `科普子图` / `风险审查` / `事件间隔累计`, the `ms` suffix, the failure note `（本轮出现错误或转人工）` in `var(--bad)`, `display:flex;flex-wrap:wrap;gap:6px 16px`, `.trace-foot b{color:var(--ink);font-weight:600}`

### DebugHintNote
- Source: `Code/app/web/chat.html` markup 386–392, CSS 229
- Category: basic
- Description: Standing warning under the trace list explaining that this is a debug view and must be switched off for real customers.
- Extractable props: `visible` (boolean)
- Hardcoded: the full Chinese copy (实时事件说明 + `<b style="color:#8a5a1e">★ 这是调试视图</b>` …), `font-size:12px`, `color:var(--muted)`, `padding:8px 18px 0`, `line-height:1.6`

### MetricCard
- Source: `Code/app/ops/panel.html` CSS 34–36, builder 535–536
- Category: basic
- Description: KPI tile — large numeric value over a small muted label.
- Extractable props: `value` (string | number), `label` (string)
- Hardcoded: `.metric{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px}`, `b{display:block;font-size:20px;letter-spacing:-.5px}`, `span{color:var(--muted);font-size:12px}`

### TicketQueueRow
- Source: `Code/app/ops/panel.html` builder `renderQueue()` 356–373, CSS 44–63, 106–107
- Category: basic
- Description: Clickable queue row — priority/status/test/SLA badges, reason, initiator with wait time vs SLA, context line and shortened ticket/session ids; the selected row and test rows get distinct tints.
- Extractable props: `ticketId` (string), `sessionId` (string), `priority` (`"P0"`|`"P1"`|`"P2"`), `reason` (string), `status` (string), `isTest` (boolean), `slaBreached` (boolean), `waitSeconds` (number), `slaSeconds` (number), `userName` (string | null), `context` (string), `active` (boolean)
- Hardcoded: `.row{padding:12px 18px;border-bottom:1px solid #eef2f5;display:grid;gap:4px}`, hover `#f7fbfa`, active `#e8f2f0`, test tint `#faf8fd`, the `测试` badge, the `已超时` badge, the `（未登记）` fallback name, `已等待 `/` / SLA ` label fragments, `工单 ` / ` · 会话 ` / `…` id formatting, the `fmtWait()` s/m/h thresholds, the "first character only" tooltip, badge-to-priority ternary mapping

### TicketDetailSection
- Source: `Code/app/ops/panel.html` builder `renderDetail()` 415–466, CSS 64–68
- Category: basic
- Description: Bordered section block with an `<h3>` and a definition-list grid; used 5× (工单 / 画像摘要 / 风险报告 / 对话记录 / 处置).
- Extractable props: `title` (string), `note` (string, e.g. the `（敏感信息已脱敏）` suffix for the `service` role)
- Hardcoded: `.section{padding:14px 18px;border-bottom:1px solid #eef2f5}`, `h3{margin:0 0 8px;font-size:13px;color:var(--muted);letter-spacing:.5px}`, `.kv{display:grid;grid-template-columns:88px 1fr;gap:4px 10px;font-size:13px}`, `dt{color:var(--muted)}`, `dd{margin:0;word-break:break-word}`

### RuleHitChip
- Source: `Code/app/ops/panel.html` builder 405–406, CSS 74–75
- Category: basic
- Description: Red monospace chip listing one hard-rule hit as `rule_id title：span`.
- Extractable props: `ruleId` (string), `title` (string), `span` (string)
- Hardcoded: `font:12px/1.6 Consolas,monospace`, `background:#ffe3de`, `color:#8a2a1e`, `padding:2px 6px`, `border-radius:4px`, `margin-right:6px`, `display:inline-block`, the `：` separator

### OpsMessageBubble
- Source: `Code/app/ops/panel.html` builder 412–413, CSS 69–73
- Category: basic
- Description: Transcript bubble on the ops side, keyed by the raw message role.
- Extractable props: `role` (`"user"` | `"assistant"` | `"agent"` | `"system"`), `content` (string)
- Hardcoded: `padding:8px 10px`, `border-radius:8px`, `margin:6px 0`, `font-size:13px`, `white-space:pre-wrap`, palettes user `#f3f6f8`, assistant `#e6f4ee`, agent `#e8f1fb`, system `#fff4dd`

### DeletedTranscriptNotice
- Source: `Code/app/ops/panel.html` builder 416–420, CSS 62–63
- Category: basic
- Description: Amber notice shown when the customer deleted the transcript, explaining that the empty conversation area is expected and not a fault.
- Extractable props: `visible` (boolean)
- Hardcoded: the full copy (`对话原文已被顾客删除。` … `这是**预期行为**，不是系统故障。`), `margin:0 0 10px`, `padding:9px 12px`, `border-radius:7px`, `background:#fff4dd`, `border:1px solid #e6d3a8`, `color:#6d4c08`

### RoleBadge
- Source: `Code/app/ops/panel.html` markup 125, CSS 99–100, filled by `applyIdentity()` 254
- Category: basic
- Description: Blue monospace chip showing the logged-in agent's server-issued role.
- Extractable props: `role` (string, default `"—"`)
- Hardcoded: `font:11px Consolas,monospace`, `padding:2px 8px`, `border-radius:4px`, `background:#e8f1fb`, `color:#17405f`

### RawPiiFlag
- Source: `Code/app/ops/panel.html` markup 126, CSS 101–102, toggled in `applyIdentity()` 256
- Category: basic
- Description: Red flag shown only for roles that can read unmasked PII.
- Extractable props: `visible` (boolean)
- Hardcoded: `可见明文 PII` text, `background:#ffe3de`, `color:#8a2a1e`, `border-radius:4px`, the role allow-list `["doctor", "compliance", "admin"]`

### AuthTipBlock
- Source: `Code/app/web/chat.html` 307–310, CSS 251–253 · `Code/app/ops/panel.html` 152–156, CSS 94–96
- Category: basic
- Description: Muted helper card inside the auth card; holds inline `<code>` spans for demo credentials.
- Extractable props: `lines` (array of strings/HTML)
- Hardcoded: `background:#f7faf9`, `border:1px solid #e3ecea`, `border-radius:8px`, `padding:9px 11px`, `color:var(--muted)`, `code{color:var(--accent)}`, all demo account strings, line-heights 1.7 (chat) / 1.8 (ops)

### EmptyState
- Source: `Code/app/web/chat.html` CSS 70 (`.conv-empty`), 225 (`.trace-empty`) · `Code/app/ops/panel.html` CSS 76 (`.empty`)
- Category: basic
- Description: Centred/left muted placeholder with Chinese copy, used for empty conversations, empty trace and unselected ticket.
- Extractable props: `message` (string), `hint` (string | null)
- Hardcoded: chat `.conv-empty` `padding:18px 16px`, `.trace-empty` `padding:22px 18px;text-align:center`; ops `.empty{padding:24px 18px;font-size:13px}`; the copy `还没有历史对话。` / `从左侧选择一个工单` / `加载中…` / `当前没有待处理工单（已结束的工单不在队列里）`

### Toast
- Source: `Code/app/ops/panel.html` markup 174, CSS 77–78, 108–109, `toast()` 303–310
- Category: basic
- Description: Fixed bottom-right status/alert message that auto-clears after 5 s and hides entirely when empty.
- Extractable props: `text` (string), `alert` (boolean; SLA breaches use it)
- Hardcoded: `position:fixed;right:20px;bottom:20px;max-width:380px`, `background:var(--ink)`, `color:#fff`, `padding:12px 16px`, `border-radius:8px`, `font-size:13px`, `z-index:20`, alert `background:var(--p0)`, `:empty{display:none}`, the 5000 ms timeout

### PaneHead
- Source: `Code/app/web/chat.html` markup 359–367, CSS 140–146
- Category: basic
- Description: Small sub-header inside the right column's graph window, with a title, inline mini summary and a compact zoom/reset button group.
- Extractable props: `title` (string), `inlineSummary` (string), `tools` (array of `{id, label, title}`)
- Hardcoded: `工作流架构` title, `适应` / `＋` / `－` / `清空` button labels and their tooltips, `background:#f7fafc`, `padding:6px 14px`, `font-size:12px`, `font-weight:600`, `.pane-tools button{padding:2px 7px;font-size:11px;border-radius:5px}`, hover `border-color:var(--accent)`

### GraphNodeSvg
- Source: `Code/app/web/chat.html` built in `graphDraw()` 1170–1206, CSS 170–179, `graphLayout()` 1079–1098
- Category: basic
- Description: One SVG node — rounded rect for a normal node, diamond polygon for a routing decision — with a truncated Chinese label, a visited-state fill by group, and an amber dot when it calls an LLM.
- Extractable props: `nodeId` (string), `label` (string, clipped to 11 chars / 9 for decisions), `group` (`"main"`|`"kb"`|`"risk"`), `visited` (boolean), `seq` (number | null), `ms` (number | null), `callsLlm` (boolean), `isDecision` (boolean), `x` / `y` (numbers)
- Hardcoded: `GNODE_W=118`, `GNODE_H=30`, `GNODE_GAP=12`, `LAYER_H=92`, `LAYER_PAD=34`, `rx:6`, text offsets `x:8,y:19` (rect) and `x:59,y:19` + `text-anchor:middle` (diamond), visited fills `#eef4f9` / `#e9f4f0` / `#fbeeeb`, `.gnode text` `11px "Microsoft YaHei",system-ui,sans-serif`, tooltip copy `第 N 个执行 · 用时 Nms` / `本次没有走到` / ` · 调用大模型`, LLM dot `#e0a94a` at `cx:GNODE_W-9, cy:9, r:3`, the `（未登记的节点）` fallback label

### GraphLayerBand
- Source: `Code/app/web/chat.html` built in `graphDraw()` 1135–1145, CSS 161–164
- Category: basic
- Description: Pale rounded background band behind one architecture layer, titled with the layer name, its hint, and a "visited this round" count.
- Extractable props: `title` (string), `hint` (string), `touched` (boolean), `visitedCount` (number), `y` (number), `width` (number)
- Hardcoded: `x:8`, `width+16`, `height:GNODE_H + LAYER_H - 12`, `rx:8`, idle `fill:#f7f9fb;stroke:#e6ecf1`, touched `fill:#f2f8f6;stroke:#cfe3da`, title `11px "Microsoft YaHei",system-ui,sans-serif;fill:#7b8b96`, touched title `fill:#2f6b58;font-weight:600`, the `　·　` separator and `［本次走过 N 个节点］` suffix

### GraphEdgeSvg
- Source: `Code/app/web/chat.html` built in `graphDraw()` 1147–1167, CSS 180–186
- Category: basic
- Description: Cubic-Bézier connector between two graph nodes, with an optional branch label and an animated dash-flow state when both endpoints were traversed.
- Extractable props: `from` (string), `to` (string), `label` (string, often empty), `flowed` (boolean), `conditional` (boolean), `subgraph` (boolean)
- Hardcoded: control-point rule `dy = Math.max(18, Math.abs(y2 - y1) / 2)`, `stroke:#dde5ea`, `stroke-width:1.2`, `fill:none`, `marker-end:url(#arrow)`, arrow marker def (`viewBox 0 0 10 10`, `refX 9`, `refY 5`, `markerWidth/Height 6`, path `M0,0 L10,5 L0,10 z` filled `#cfd8de`), conditional dash `4 3`, subgraph `stroke:#b9c9d6;stroke-dasharray:2 3`, flowed `stroke:var(--accent);stroke-width:2;stroke-dasharray:7 5` + `animation:flow .7s linear infinite`, `.gedge-label` `9.5px` `fill:#a8b4bd;text-anchor:middle`, the edge data source `GET /api/graph` → `architecture.edges`

---

## Extraction priority

Highest reuse value, in order — these are the ones that appear on both pages or dominate a page:

1. `PaneShell` — the only real surface primitive; both pages need it (`chat` calls it `.card`, `ops` calls it `.pane`).
2. `AppTopbar` / `OpsTopbar` — two genuinely different shells over one shared brand+status+identity pattern; extract `BrandBlock`, `StatusPill`, `ConnectionDot`, `RoleBadge`, `RawPiiFlag` as children.
3. `AuthOverlay` — near-duplicate across pages (same field CSS, same focus ring, same tip block); the chat variant is a superset of the ops variant.
4. `ChatBubble` + `MessageMeta` + `MetaTag` — the highest-frequency element in the chat page and the carrier of every provenance label.
5. `ConversationListItem` — the only list-item pattern on the customer side; exercises active/empty/badge states.
6. `TicketQueueRow` + `OpsBadge` — the ops counterpart, exercising the P0/P1/P2/SLA/test state matrix.
7. `TraceNodeRow` + `NodeDetailPanel` — the most stateful component set (open/hot/empty/group/LLM) and the one that ties the trace list to the SVG graph.
8. `GraphNodeSvg` / `GraphLayerBand` / `GraphEdgeSvg` / `GraphSummaryChips` / `GraphLegend` — the SVG architecture view, a distinctive and reusable "system diagram" component family.
9. `MetricCard`, `Toast`, `EmptyState`, `StatusBar`, `Spinner` — small, cheap, appear in both design directions.

Not worth extracting: `TypingCursor` (three CSS lines, inseparable from `ChatBubble`),
`DeleteIconButton` (inseparable from `ConversationListItem`), `DebugHintNote` and
`DeletedTranscriptNotice` (copy-only blocks tied to this product's compliance story).
