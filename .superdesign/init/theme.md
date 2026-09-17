# Theme

Two single-file pages, two independent inline `<style>` blocks. There is **no** design-token file,
no Tailwind config, no CSS variables file, no theme provider — the `:root` block at the top of each
page's `<style>` **is** the token set. Both pages were written from the same palette, but they are
separate copies that have drifted in a few places (noted below).

- `Code/app/web/chat.html` — `<style>` at lines 7–256 (250 lines)
- `Code/app/ops/panel.html` — `<style>` at lines 7–111 (105 lines)

---

# Part 1 — Compact token summary

## 1.1 Colour tokens — `chat.html` `:root` (lines 8–12)

| Token | Value | Role |
|---|---|---|
| `--ink` | `#19344a` | Primary text / dark navy |
| `--muted` | `#586d7b` | Secondary text, meta, labels |
| `--line` | `#d7e1e7` | All borders and hairlines |
| `--accent` | `#226b62` | Brand teal — primary buttons, active states, user bubbles, focus |
| `--accent2` | `#1b554e` | `--accent` hover (darker) |
| `--bg` | `#f3f6f8` | Page background |
| `--card` | `#fff` | Card / panel surface |
| `--warn` | `#b3862a` | Warning (busy dot) |
| `--bad` | `#c8493a` | Error text |
| `--ok` | `#3f8a72` | Success / connected dot |
| `--g-main` | `#19344a` | Graph: main-graph group |
| `--g-kb` | `#2f5d8a` | Graph: knowledge subgraph group |
| `--g-risk` | `#9a5b2a` | Graph: risk-review subgraph group |

## 1.2 Colour tokens — `panel.html` `:root` (lines 8–9)

| Token | Value | Role |
|---|---|---|
| `--ink` | `#19344a` | Primary text |
| `--muted` | `#586d7b` | Secondary text |
| `--line` | `#d7e1e7` | Borders |
| `--accent` | `#226b62` | Brand teal (primary buttons, eyebrows, active row) |
| `--bg` | `#f3f6f8` | Page background |
| `--p0` | `#c8493a` | Priority P0 badge / overtaken SLA / alert toast |
| `--p1` | `#b3862a` | Priority P1 badge |
| `--p2` | `#6b7d8a` | Priority P2 badge / disconnected dot |
| `--ok` | `#3f8a72` | Connected dot / "人工已接入：是" badge |

### Drift between the two `:root` blocks

- `panel.html` has **no `--card`** (it hardcodes `background:#fff`), **no `--accent2`**
  (it hardcodes `#1b554e` for `button.primary:hover`), and **no `--warn`**.
- `panel.html`'s `.auth-msg` uses `color:var(--bad)` but `--bad` is **not declared** in its `:root`
  — the declaration is invalid at computed-value time and the text falls back to inherited colour.
  A design re-implementation should declare `--bad:#c8493a` on the ops page.
- Priority tokens `--p0/--p1/--p2` exist only on the ops page; graph tokens `--g-*` only on chat.
  `--p0`/`--p1` values equal chat's `--bad`/`--warn`.

## 1.3 Semantic colours that are NOT tokens (hardcoded hex, used repeatedly)

| Value | Meaning | Where |
|---|---|---|
| `#fbfcfd` | Card header / bar background | `.card>h2`, `.statusbar`, `.trace-foot`, `.pane>h2` |
| `#eef2f5` | Faint divider | `.conv`, `.examples`, `.pane-head`, `.legend`, `.section`, `.row` |
| `#eef5f3` / `#e8f2f0` | Hover tint (accent-10%) | `button:hover` (chat / ops) |
| `#8fbfb3` / `#75a69a` | Hover/focus border | inputs, buttons |
| `#e8f2f0` + `#eaf4f2` | Active / hover row tint | `.conv.active`, `.node.hot`, `.row.active` |
| `#e8f1fb` + `#17405f` | Info chip (blue) | `.tag`, `.cv-badge`, `.badge.st`, `.role-badge` |
| `#e6f4ee` + `#1a5044` | OK chip (green) | `.tag.ok`, `.badge.ok`, `.gchip.on` |
| `#fff4dd` + `#6d4c08` | Warn chip (amber) | `.tag.warn`, `.badge.p1`, `.node .llm`, `.msg.system` |
| `#ffe3de` + `#8a2a1e` | Danger chip (red) | `.tag.bad`, `.badge.p0`/`.warn`, `.cv-badge.off`, `.hit` |
| `#fff8e8` + `#f0e0bb` | System bubble | `.msg.sys`, `.nd-warn` |
| `#efe7f7` + `#553a78` | Test-ticket badge (purple) | `.badge.test`, `.row.is-test` |
| `#e0a94a` | "calls an LLM" dot in the SVG | `graphDraw()` inline fill |
| `#a8b4bd` / `#7b8b96` / `#2f6b58` | Graph edge label / layer title / touched layer title | `.gedge-label`, `.glayer-title` |

## 1.4 Typography

Base body (both pages — note chat adds `-apple-system`):

```css
/* chat.html:15-16 */
body{margin:0;background:var(--bg);color:var(--ink);
     font:14px/1.7 "Microsoft YaHei","PingFang SC",-apple-system,sans-serif}
/* panel.html:11-12 */
body{margin:0;background:var(--bg);color:var(--ink);
     font:14px/1.7 "Microsoft YaHei","PingFang SC",sans-serif}
```

| Family | Stack | Used for |
|---|---|---|
| Sans (UI) | `"Microsoft YaHei","PingFang SC",-apple-system,sans-serif` (chat) / `"Microsoft YaHei","PingFang SC",sans-serif` (panel) | everything by default |
| Monospace | `Consolas,"SF Mono",Menlo,monospace` (`.mono`, `code` in chat) / `Consolas,monospace` (all other mono uses) | eyebrows, ids, timings, tags, badges, meta |
| SVG text | `"Microsoft YaHei",system-ui,sans-serif` | `.glayer-title`, `.gnode text`, `.gedge-label` |

Type scale actually in use (px):

| Size | Usage |
|---|---|
| `20px` | `.metric b` (KPI value) |
| `19px` | `.auth-card h1` |
| `16px` | `.brand b` (chat) |
| `15px` | `.brand b` (panel), `.pane>h2` (panel) |
| `14.5px` | `.auth-card .submit`, `.auth-card button` (panel) |
| `14px` | body, `.card>h2`, `.pane>h2` (chat) |
| `13.5px` | `.tabs button` |
| `13px` | `.cv-title`, `.node .nm`, `.statusbar`, `.metric`/`.toast` body, `.msg` (panel), `.section h3`, `.kv` |
| `12.5px` | topbar right, `.conv-empty`, `.msg.sys/.err`, `.auth-card .sub`, `.field label`, `.auth-msg`, `.trace-foot`, `.row .sub` |
| `12px` | `.card>h2 .sub`, `.examples span`, `.pane-head`, `.legend`, `.nd-desc`, `.nd-none`, `.nd-row`, `.hint`, `.metric span` |
| `11.5px` | `.glegend`, `.gsum-title`, `.nd-key`, `.nd-val`, `.tk-badge`, `.auth-tip` (panel), `.role-badge`, `.raw-flag` |
| `11px` | `.brand .eyebrow`, `.pill`-level mono, `.cv-meta`, `.node .idx`, `.node .ms`, `.gchip`, `.gsum-note`, `.nd-warn`, `.pane-tools button`, `.badge`, `.glayer-title` |
| `10.5px` | `.node .id` |
| `10px` | `.cv-badge`, `.node .llm`, `.node .caret` |
| `9.5px` | `.gedge-label` |

Letter-spacing: `.brand .eyebrow` `1.6px` (chat) / `1.5px` (panel); `.brand b` (chat) `.3px`;
`.msg .meta` `.2px`; `.section h3` `.5px`; `.metric b` `-.5px`.

Line heights: `1.7` body; `1.25`/`1.3` brand; `1.6` textarea & auth sub; `1.8` auth tip (panel);
`1.65` `.nd-val`; `1.45` `.cv-title`; `1.4` `.badge`; `1.6` `.hit`.

## 1.5 Spacing

No spacing scale/tokens — all literal px. Recurring rhythm:

| Value | Usage |
|---|---|
| `2px` | micro padding (`.pane-tools button`, `.gchip`, `.badge`, `.hit`, `.tk-badge`, `.role-badge`) |
| `3px` | `.pill` vertical, `.cv-meta` margin-top |
| `4px` | `.pane-tools`/`.tools` gap, `.tabs`-adjacent, `.cv-title` ellipsis padding |
| `5–6px` | `.field` gap, `.conv-list` padding, `.msg`-adjacent, `.glegend` row gap, `.tabs` gap |
| `7–8px` | `.examples` gap, `.pane-head` padding, `.composer`-adjacent, `.tools` gap (panel) |
| `9–10px` | `.composer .row` gap, `.statusbar` gap, `.confirm` gap, `.node` grid gap |
| `11–12px` | `.card>h2` / `.composer` vertical padding, `.topbar`/`.pane` outer padding |
| `14px` | `.pane>h2` padding (panel), `.legend`/`.glegend`/`.gsummary` horizontal |
| `16px` | `.wrap` grid gap; `.messages` padding; `.conv` horizontal; `.metrics` margin-top |
| `18px` | `.layout` grid gap (panel); `.card>h2`, `.composer`, `.node`, `.section` horizontal |
| `20–22px` | `.topbar` horizontal padding (chat 22px / panel 24px), `.toast` offsets |
| `24px` | panel page-level horizontal padding, `.empty` padding |
| `26–28px` | `.auth-card` padding |

Container widths: chat `.wrap{max-width:1660px}`; panel `.metrics`/`.layout{max-width:1560px}`.
Card min-heights: chat columns `min-height:560px`; panel panes `min-height:520px`.
Chat column heights: `calc(100vh - 132px)` (= viewport minus the topbar and the 16px top/bottom margins).

## 1.6 Border-radius

| Value | Usage |
|---|---|
| `999px` | pills, compact round buttons (`.pill`, `.convs>h2 button`, `.examples button`, `.topbar .who button`) |
| `14px` | `.auth-card` |
| `12px` | `.card` (chat), `.pane` (panel), `.msg` |
| `10px` | `.metric` (panel), `.gchip` |
| `9px` | `textarea` (chat) |
| `8px` | `button` (chat), `.field input`, `.auth-tip`, `.toast`, `.msg.sys`/`.msg.err`, `.msg` (panel), `.glayer rect` (`rx:8`) |
| `7px` | `.tdel` |
| `6px` | `button`/`select`/`input` (panel), `.gnode rect` (`rx:6`) |
| `5px` | `.cv-del`, `.pane-tools button`, `.nd-warn` |
| `4px` | `.tag`, `.badge`, `.role-badge`, `.raw-flag`, `.hit` |
| `3px` | bubble tails (`.msg.user` bottom-right, `.msg.bot` bottom-left), `.cv-badge`, `.dot`-scale squares (`.legend i`, `.glegend i`), `.node .llm` |
| `50%` | `.dot`, `.spin` |

## 1.7 Shadows / focus rings / overlay

| Value | Usage |
|---|---|
| `0 0 0 3px #e8f2f0` | focus ring on `textarea:focus`, `.field input:focus` (both pages) |
| `0 18px 60px rgba(12,30,42,.28)` | `.auth-card` (chat) |
| `0 18px 60px rgba(12,30,42,.3)` | `.auth-card` (panel) |
| `rgba(23,47,64,.45)` | `.auth` overlay backdrop (chat) |
| `rgba(23,47,64,.5)` | `.auth` overlay backdrop (panel) |
| `backdrop-filter:blur(3px)` | `.auth` overlay (both) |

No `box-shadow` anywhere else — depth comes from the single `1px solid var(--line)` border plus
`#fbfcfd` header strips.

## 1.8 Breakpoints

| Query | File | Effect |
|---|---|---|
| `@media(max-width:1360px)` | chat | `.wrap` → 2 columns (`236px minmax(360px,1fr)`); **`.trace{display:none}`** — the whole right column disappears |
| `@media(max-width:900px)` | chat | `.wrap` → 1 column; `.convs{display:none}` — conversation list disappears, chat only |
| `@media(max-width:1000px)` | panel | `.layout` → 1 column |

## 1.9 Animation tokens

| Keyframes | Definition | Used by |
|---|---|---|
| `pulse` | `50%{opacity:.3}` , `1s infinite` | `.dot.busy` |
| `rise` | `from{opacity:0;transform:translateY(6px)}` , `.18s` / `.16s` / `.14s ease-out` | `.msg`, `.node`, `.nd` |
| `caret` | `50%{opacity:0}` , `.9s steps(1) infinite` | `.msg.typing::after` (`content:"▍"`) |
| `spin` | `to{transform:rotate(360deg)}` , `.7s linear infinite` | `.spin` |
| `flow` | `to{stroke-dashoffset:-12}` , `.7s linear infinite` | `.gedge.flowed` (travelled edges) |

Other transitions: `.cv-del{transition:opacity .12s}`, `.node .caret{transition:transform .12s}`.
`prefers-reduced-motion` is honoured in JS, not CSS (`typingEligible()` returns false and the text is
rendered whole).

## 1.10 Graph / legend colour tokens (`chat.html` only)

| Token / value | Meaning |
|---|---|
| `var(--g-main)` = `#19344a` | Main graph — legend swatch, `.node.g-main` left bar, visited node fill `#eef4f9` |
| `var(--g-kb)` = `#2f5d8a` | 科普子图 (knowledge subgraph) — legend swatch, `.node.g-kb` left bar at `#f7fafd`, visited fill `#e9f4f0` |
| `var(--g-risk)` = `#9a5b2a` | 风险审查子图 (risk subgraph) — legend swatch, `.node.g-risk` left bar at `#fdfaf6`, visited fill `#fbeeeb` |
| `#e3eaee` | `.node.g-main` left bar (idle) |
| `#f7f9fb` / `#e6ecf1` | `.glayer rect` idle layer background / stroke |
| `#f2f8f6` / `#cfe3da` | `.glayer.touched rect` visited layer background / stroke |
| `#fff` / `#cfd8de` | `.gnode rect, .gnode polygon` idle node fill / stroke |
| `#fcfdff` | `.gnode.decision` node fill (routing diamonds) |
| `#dde5ea` | `.gedge` stroke; `#cfd8de` arrow-marker fill |
| `#b9c9d6` | `.gedge.subgraph` (dashed `2 3`) |
| `#e0a94a` | LLM-call dot on a visited/any node (`r:3`, top-right) |
| `#f0f3f6` / `#8b98a3` / `#e3e9ee` | `.gchip` idle chip background / text / border |
| `#e9f4f0` / `#1a5044` / `#bcdccf` | `.gchip.on` active chip |
| `#eef4f9` / `#17405f` / `#c3d6e6` | `.gchip.sub.on` active subgraph chip |

## 1.11 Priority / status tokens (`panel.html` only)

Elevated by name, not by class family:

| Token | Value | Rendered as |
|---|---|---|
| `--p0` | `#c8493a` | `.badge.p0` (`背景 #ffe3de` / `文字 #8a2a1e`), `.row .sub b.over`, `.toast.alert` background |
| `--p1` | `#b3862a` | `.badge.p1` (`#fff4dd` / `#6d4c08`) |
| `--p2` | `#6b7d8a` | `.badge.p2` (`#eef1f4` / `#384a55`), `.dot` idle background |
| `--ok` | `#3f8a72` | `.badge.ok` (`#e6f4ee` / `#1a5044`), `.dot.on` |

Status text is **not** colour-mapped — `t.status` is printed raw into `.badge.st`.
Ticket statuses in play (`ACTIVE_STATUSES`): `open`, `accepted`, `in_progress`, `escalated`, `closed`.

---

# Part 2 — Raw `<style>` source

## 2.1 `Code/app/web/chat.html` — full inline `<style>` (lines 7–256)

```html
<style>
:root{
  --ink:#19344a; --muted:#586d7b; --line:#d7e1e7; --accent:#226b62; --accent2:#1b554e;
  --bg:#f3f6f8; --card:#fff; --warn:#b3862a; --bad:#c8493a; --ok:#3f8a72;
  --g-main:#19344a; --g-kb:#2f5d8a; --g-risk:#9a5b2a;
}
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

/* 状态条 */
.statusbar{display:none;align-items:center;gap:9px;padding:9px 18px;border-top:1px solid var(--line);
           background:#fbfcfd;font-size:13px;color:var(--muted)}
.statusbar.show{display:flex}
.spin{width:13px;height:13px;border:2px solid #cfdde3;border-top-color:var(--accent);
      border-radius:50%;animation:spin .7s linear infinite;flex:none}
@keyframes spin{to{transform:rotate(360deg)}}

/* 人工接管提示条：输入框不禁用，只是明确告诉用户"AI 暂停、话会转给客服" */
.takeover-bar{display:flex;align-items:center;gap:8px;padding:8px 18px;
              border-top:1px solid var(--line);background:#fdf7ec;
              font-size:12.5px;color:#7a5a1e}
.tk-badge{flex:none;padding:2px 7px;border-radius:4px;background:#f6e2b8;color:#6b4a12;
          font-size:11.5px}

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
.topbar .who{display:flex;align-items:center;gap:8px}
.topbar .who button{font-size:12.5px;padding:4px 10px;border-radius:999px}
</style>
```

## 2.2 `Code/app/ops/panel.html` — full inline `<style>` (lines 7–111)

```html
<style>
:root{--ink:#19344a;--muted:#586d7b;--line:#d7e1e7;--accent:#226b62;--bg:#f3f6f8;
      --p0:#c8493a;--p1:#b3862a;--p2:#6b7d8a;--ok:#3f8a72}
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
.toast{position:fixed;right:20px;bottom:20px;max-width:380px;background:var(--ink);color:#fff;
       padding:12px 16px;border-radius:8px;font-size:13px;z-index:20}

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
/* 顶栏身份显示 */
.whoami{display:flex;align-items:center;gap:7px;font-size:12.5px}
.role-badge{font:11px Consolas,monospace;padding:2px 8px;border-radius:4px;
            background:#e8f1fb;color:#17405f}
.raw-flag{font:11px Consolas,monospace;padding:2px 8px;border-radius:4px;
          background:#ffe3de;color:#8a2a1e}
.testtoggle{display:flex;align-items:center;gap:5px;font-size:12.5px;color:var(--muted)}
/* 测试工单行：整行降饱和 + 徽章，一眼能和真实工单区分开 */
.badge.test{background:#efe7f7;color:#553a78}
.row.is-test{background:#faf8fd}
.row.is-test .line1 strong{color:#553a78}
.toast.alert{background:var(--p0)}
.toast:empty{display:none}
@media(max-width:1000px){.layout{grid-template-columns:1fr}}
</style>
```
