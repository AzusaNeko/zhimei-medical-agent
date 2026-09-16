# 智美医美顾问 · LangGraph 多 Agent 顾问系统（MVP）

> 落地文档第 1 步：**图 + CLI 演示**。主图 + 知识科普子图 + 风险审查子图 +
> 有界修订回路 + 放行凭据校验 + clarify 预算，全部可在本机跑通。
>
> 设计文档在上一级目录 `Workflow/`：架构图、伪代码讲解、规则与参数、数据模型、运营后台。

---

## 当前状态（先说清楚哪些验证过）

| 项 | 状态 |
|---|---|
| 图的接线、路由、预算、凭据、并发写冲突 | ✅ **已跑通**（`scripts/smoke.py` 75 项断言全过） |
| 四个典型场景端到端演示 | ✅ 已验证（科普含修订环 / 预约含确认执行 / 紧急 / 澄清） |
| **FastAPI + SSE 接口层** | ✅ **已实现并验证**（`scripts/smoke_api.py` 37 项 + `smoke_api_live.py` 53 项真实 HTTP） |
| **运营后台（坐席工作台）** | ✅ **已实现并验证**（`scripts/smoke_ops.py` 42 项；面板 + 队列 + 跨模块联动） |
| **C 端聊天页 + Agent 执行轨迹可视化** | ✅ **已实现**（`app/web/chat.html`；`scripts/check_ui.cjs` 静态校验） |
| 真实依赖（Postgres / Milvus / BGE / DeepSeek） | ✅ **已在本机跑通**（`check_env` 18 通过 / 0 阻塞；四场景 `--demo` 退出码 0） |
| 成本与延迟观测 | ✅ `app.llm_call_log` 已接入；`scripts/llm_stats.py` 出报表 |
| 视觉多模态 | ⬜ 按决策留桩：图片只入库不解析，转人工，且不假装看图 |

---

## 快速开始

> **本机已建好独立 venv**（不污染 Anaconda base），先激活它：
> ```powershell
> .\.venv\Scripts\Activate.ps1     # 之后 python / pip 都指向 .venv
> ```
> 没建过的话：`uv venv .venv --python 3.12` +
> `uv pip install --python .venv\Scripts\python.exe -r requirements.txt`
> （沙箱/受限环境下 uv 需要 `UV_CACHE_DIR` 指向工作区内）

### 路径 0：先体检（30 秒，推荐每次都先跑）

```bash
python scripts/check_env.py
```

一条命令查五件事并给出**下一步该做什么**：配置（档位 / API key / 镜像）、Python 依赖、
依赖服务连通性（端口 + 真实连一次 + 表建没建 + 知识库有没有数据）、模型缓存。
退出码 0 = 可以直接跑，1 = 还有阻塞项。`--profile fake` 下也能跑（会告诉你哪些 ✗ 不影响 fake 档位）。

### 路径 A：先验证图接线（不需要任何外部依赖，30 秒）

```bash
cd Code
pip install -r requirements.txt        # 或者 uv pip install -r requirements.txt
python scripts/smoke.py                # 75 项不变量断言
python -m app.cli --demo --profile fake
```

`--profile fake` 用内存版 checkpointer / 存储 + **脚本化假模型**，
不连 Postgres、Milvus，也不需要 API key。它的用途只有一个：
**把"图的接线对不对"和"依赖装没装"分开验证**。

### 路径 B：接真实依赖

**先确认走哪种情形**（本机已经有一套 Postgres + Milvus 在跑，属于别的项目）：

```bash
# 0) 看现有实例（情形 A 就用它，不要再起一套）
docker ps --format "{{.Names}} | {{.Status}} | {{.Ports}}"
```

| 情形 | 适用 | 依赖端口 | 起依赖 |
|---|---|---|---|
| A 复用现成实例 | 本机已有 Postgres / Milvus | PG 5434 / Milvus 19532 | 跳过，已在跑 |
| **B 起独立一套（本机当前采用）** | 想完全隔离 / 换机器 | PG **55432** / Milvus **19530** | `docker compose -p zhimei up -d` |

> 本机现在跑的是**情形 B**：`zhimei-pg` / `zhimei-etcd` / `zhimei-minio` / `zhimei-milvus`
> 四个容器，端口 55432 / 19530，业务表已建好（29 张）。
> 别的项目的两套栈（5433/5434、19531/19532）保持运行，互不影响。
> compose 里的镜像 tag 特意选成本机已有的版本，起栈时**不需要联网拉取**。

```bash
# 1) 情形 A：先在现成 Postgres 里建本项目专用的库（别用别人的库）
docker exec bid_agent_postgres psql -U bidagent -c "CREATE DATABASE zhimei OWNER bidagent;"
#    情形 B：docker compose -p zhimei up -d

# 2) 配置（仓库里已按情形 A 预填好 .env，只需补 DEEPSEEK_API_KEY）
copy .env.example .env      # 已有 .env 就跳过；两种情形的写法都在文件里注释着

# 3) 建业务表（PG_DSN 取 .env 里的值）
psql "<你的 PG_DSN>" -f sql/schema.sql

# 4) 下模型到本地（★ 国内必须走这一步，别让 FlagEmbedding 自动下 —— 会失败）
python scripts/download_models.py

# 5) 灌演示数据（会打印演示用户 user_id，抄下来）
python scripts/seed_kb.py

# 6) 验证检索链条真的通
python scripts/check_retrieval.py

# 7) 跑（真实档位记得带 --user，否则会话没绑用户，预约类请求会被判 need_info）
python -m app.cli --demo --user <user_id>
python -m app.cli -t "热玛吉和超声炮有什么区别" --user <user_id>
python -m app.cli --interactive --user <user_id>
```

> `lg`（LangGraph checkpoint）schema 由 `AsyncPostgresSaver.setup()` 自动建表，
> **不要**写进 `sql/schema.sql`，也不要手改。
>
> BGE-M3 与 Reranker 是**进程内**加载（首次运行会下载模型，约 2GB），
> 国内建议在 `.env` 里开 `HF_ENDPOINT=https://hf-mirror.com`；想先跳过可以用 `--profile fake`。
>
> ⚠️ **共享 Milvus 实例时集合名带项目前缀**（`.env` 已设为 `zhimei_kb_chunks`）——
> Milvus 的集合是实例级共享的，通用名有撞名风险。

> 📋 **接真实依赖时请照着 [`SELF-TEST.md`](SELF-TEST.md) 逐项走。**
> 那份清单写明了每一步的期望结果、出错先看哪里，以及**只有真实模型才能验的质量项**
> （引用是否可溯源、三位专家意见是否真的视角不同、审查超时是否降级为转人工、
> 模型会不会把"会不会失明"误判成紧急等）。接线已经用 fake 档位验过 112 项断言
> （`smoke.py` 75 + `smoke_api.py` 37），清单验的是行为与质量。

> ⚠️ 真实档位跑 CLI 时**记得带 `--user <uuid>`**（uuid 在 `seed_kb.py` 的输出里）。
> 不绑用户 → 会话 `auth.verified=false` → 所有预约类请求会被判 `need_info`。

---

## 接口层（FastAPI + SSE）

```bash
python -m app.api --profile fake --port 8077             # 起服务（fake 档位不需要外部依赖）
python scripts/demo_api.py --base http://127.0.0.1:8077  # 打印真实的 SSE 事件流
python scripts/smoke_api.py                              # 接口层冒烟（37 项，进程内跑 ASGI）
```

| 端点 | 说明 |
|---|---|
| `GET /api/health` | 健康检查，返回档位与 checkpointer 类型 |
| `POST /api/sessions` | 新建会话，返回 `session_id` / `thread_id` |
| `GET /api/sessions/{sid}` | 会话信息 + 最近消息（出站内容已脱敏） |
| `POST /api/chat/{sid}/stream` | **SSE**：一次对话 |
| `POST /api/chat/{sid}/confirm` | **SSE**：用户确认后恢复挂起的图 |

### 事件契约

| event | 何时发 | 载荷 |
|---|---|---|
| `status` | 节点推的**固定文案**进度 | `{stage, text}` |
| `awaiting_confirmation` | 操作类需要用户确认，**本次流到此结束** | `{plan, plan_hash, expires_at}` |
| `final` | **唯一携带已审正文的事件** | `{text, token, kind}` |
| `handoff` | 创建人工工单（可与 `final` 并行出现） | `{ticket_id, priority, reason, text}` |
| `blocked` | 硬性阻断 | `{rule_ids}` |
| `done` / `error` | 收尾 / 兜底 | `{session_id, turn_id}` / `{code, message}` |

三条设计约束（代码注释里都写了原因）：

1. **正文不流式**。过程用 `status`（固定文案，不含模型输出）流式推送，正文整段审后由 `final` 发出。
   "先审后发"和"正文流式"物理上不可兼得 —— 想要秒回体感，就只能让过程流式。
2. **挂起不是结束**。`interrupt` 后本次流以 `awaiting_confirmation` 收尾并落 checkpoint；
   用户确认走另一个 HTTP 请求（`Command(resume=...)`），线程不必长期占用。
3. **心跳与断连**。15 秒无事件发一行 SSE 注释避免代理断连；客户端断开时取消底层任务，
   不让图在后台继续跑（白烧 token）。

接口层守住的三个守卫：

- **人工接管优先**：`ai_enabled=false` 的会话直接 409 `human_takeover`，AI 不与坐席抢话
- **同会话互斥**：同一 thread 并发请求回 409 `busy`（而不是静默排队——排队会让用户以为卡死）
- **步数超限转人工**：`GraphRecursionError` 映射成 `handoff` 事件，不是 500

---

## 登录与鉴权（JWT）

两端都要登录：C 端顾客、坐席工作台。

```bash
python scripts/seed_kb.py        # 创建演示账号（幂等，可重复跑）
```

| 入口 | 账号 | 口令 |
|---|---|---|
| 顾客端 `/chat` | `demo@zhimei.test` | `zhimei-demo-2026` |
| 坐席端 `/ops/panel` | `service@zhimei.test` | 同上 |
| | `doctor@zhimei.test` | 同上 |
| | `compliance@zhimei.test` | 同上 |
| | `admin@zhimei.test` | 同上 |

> ⚠️ 演示口令，**上线前必须删除这些账号或改成随机生成**。

### 设计要点

**token 说"你是谁"，数据库说"你能干什么"。** 令牌里带 `role`，但判权限时
**一律回数据库读当前角色**。理由：令牌签发后无法撤回 —— 坐席被降级、停用之后，
他手里那张还没过期的令牌仍然声称自己是 `compliance`。多一次查询，
换来"停用即失效"和"改权限立即生效"。

**补上的一个真实安全洞。** 在此之前，坐席身份是**客户端在请求头里自己声明的**：

```
X-Agent-Role: compliance      ← 请求方自己说自己是合规岗
```

改一个请求头就能拿到未脱敏的手机号。对一个卖点是"合规"的系统来说这是最不该有的洞。
现在角色来自 JWT + 数据库，请求头完全不再参与身份判定（有静态检查守着，
防止后续改动把它加回来）。

**其他几条**：

| 项 | 做法 |
|---|---|
| 口令存储 | `hashlib.scrypt`（标准库，n=2^14/r=8/p=1，实测约 50ms/次）。参数写在哈希串里，将来调强度时老密码仍可校验 |
| 令牌 | HS256，密钥 ≥32 字节，`algorithms=["HS256"]` **显式白名单**（防 `alg=none` 绕过），强制校验 `exp`/`iss` |
| 防账号枚举 | 口令错误与账号不存在返回**同一个错误码**，且账号不存在时也跑一次哈希校验（防时序枚举） |
| 会话隔离 | 会话归属由令牌决定，不接受请求体传入的 `user_id`；跨用户访问返回 **404 而非 403**（403 等于确认"这个会话存在"） |
| 邮箱验证 | 注册 → 验证 → 登录。**没有邮件服务**，所以 `EXPOSE_VERIFY_TOKEN=true` 时验证令牌直接在响应里返回 —— 这是演示期的临时妥协，接上邮件服务后必须置 `false` |

**邮箱验证 ≠ 身份核验**，两者不能互相替代：
- `email_verified` —— 邮箱是不是你的 → 决定**能不能登录**
- `user_identity.verified`（实名/手机号）→ 决定**能不能办操作类业务**（改约、取消）

```bash
python scripts/smoke_auth.py --base http://127.0.0.1:8090    # 40 项
python scripts/check_store_parity.py                          # Pg 与 Fake 接口一致性
python scripts/check_schema.py                                # 新建库 + 升级旧库两条路径（34 项）
```

---

## C 端聊天页（含 Agent 执行轨迹可视化）

```bash
python -m app.api --port 8090            # real 档位；加 --profile fake 则不需要任何外部依赖
# 浏览器打开 http://127.0.0.1:8090/chat
node scripts/check_ui.cjs                # 前端静态校验（语法 / 节点表同步 / DOM id / 测试工单隔离，17 项）
```

同样是**单文件 HTML + 原生 JS**（`app/web/chat.html`，无构建步骤、无 CDN），由 FastAPI 托管。

三栏布局：**对话列表 | 对话 | Agent 执行轨迹**。

**左栏是历史对话**——标题自动取自**第一条用户消息**（对话类产品里手动命名几乎没人用，
而第一句话天然就是这一轮的意图），带消息条数与相对时间。
- **＋ 新对话**：不预先建会话，发第一条消息时才建（免得库里堆一堆空会话）
- 点任意一条**切回历史对话**，完整消息记录会重新加载
- 刷新页面**回到上次那个会话**（localStorage）
- 会话被坐席接管后，输入框直接禁用并提示"人工接管"，而不是让你发出去再收 409

| 端点 | 作用 |
|---|---|
| `GET /api/sessions` | 会话列表（标题 / 消息数 / 最近活跃 / ai_enabled），支持 `channel` 与 `limit` |
| `POST /api/sessions` | 新建会话 |
| `GET /api/sessions/{sid}` | 单个会话的消息记录 |

> ⚠️ 会话列表现在**按 `channel` 过滤，但没有按用户过滤** —— 因为还没有鉴权层。
> 生产环境必须加 `WHERE user_id = $x`，否则等于把所有人的对话列表摊开。

**点任意节点可以展开它的输出**——看到那个节点到底往状态里写了什么
（识别出的意图与槽位、检索到的候选、证据筛选结果、逐句核对判定、裁决表……）。
这是把"图在干什么"从黑盒变成可读的最直接方式。

> ⚠️ **展开的内容是调试信息**，里面可能含**未经审查的中间产物**（草稿、审查意见、
> 检索到的原文）。顾客只应看到对话区里那份「已审正文」。
> **面向真实顾客时必须把「显示执行轨迹」勾掉** —— 后端默认也不发（见下）。

### 轨迹是怎么来的

后端在 `updates` 流里本来就能拿到节点名，但**默认不发**——它是执行元数据，不是给顾客看的：

| | 内容 | 能不能给用户看 |
|---|---|---|
| `status` | 固定话术（"正在查阅审核资料…"） | ✅ 可以，就那十句 |
| `node` | 图的执行结构 + 节点输出摘要 | ⚠️ **仅演示与排障**，线上对顾客必须关闭 |

所以它由查询参数控制：`POST /api/chat/{sid}/stream?trace=1`。
**不带参数时后端一个 node 事件都不发**，生产契约完全不变（有断言守着）。
前端顶栏的「显示执行轨迹」勾选框就是切换这个参数。

节点输出摘要经过压缩与限长（单节点 1800 字符上限，实测最长 1708），
否则某些节点的 patch（证据列表、三份面板意见）会让一帧几万字符。

要看子图内部节点（`kb_verify`、`gate_in` 这些），需要 `subgraphs=True`——它同样**只在 trace 路径开启**，
因为开启后事件流的元组结构会从 `(mode, chunk)` 变成 `(namespace, mode, chunk)`。

> 一个诚实的说明：`node` 事件里**没有**"是否并行"这个字段。
> 本来想用超步序号标注并行分支（三个审查面板确实是同一超步里被 `Send` 分派出去的），
> 但实测推翻了——48 个节点恰好占 48 个超步，连那三个并行面板也各自单独成块。
> 从事件流里推不出并行关系，所以只发能证实的东西：顺序、耗时、子图归属。

---

## 运营后台（坐席工作台）

```bash
python -m app.api --profile fake --port 8077
# 浏览器打开 http://127.0.0.1:8077/ops/panel
python scripts/smoke_ops.py        # 42 项（不变量、跨模块联动、权限与脱敏）
```

> 两个页面配合起来演示最完整：`/chat` 是顾客侧（提问 → 看轨迹 → 确认操作），
> `/ops/panel` 是坐席侧（工单进队列 → 接单接管 → 回复 → 关单）。
> 接单之后回到 `/chat` 再发消息，会看到 AI 已经被停用（聊天接口返回 409）。

监控面板是**单文件 HTML + 原生 JS**（`app/ops/panel.html`，无构建步骤），直接由 FastAPI 托管。
演示流程：先在聊天里制造一次转人工（例如"我做完水光第三天，现在脸发白还特别疼"），
再到面板上看工单进队列 → 接单 → 回复 → 关单。

| 端点 | 作用 |
|---|---|
| `GET /ops/tickets` | 队列（优先级排序、等待时长、SLA 超时标记）。默认**不含测试工单**，要看得传 `?include_test=true` |
| `GET /ops/tickets/{id}` | 详情：画像摘要 / 最近对话 / 风险报告（含 AI 未发出的草稿） |
| `POST /ops/tickets/{id}/accept` | 接单（**写入 `accepted_at`，同时关掉 AI**） |
| `POST /ops/tickets/{id}/reply` | 回复（规则层轻校验：block 硬拦、revise 软提示） |
| `POST /ops/tickets/{id}/escalate` \| `close` \| `reopen` | 转医师 / 关单（恢复 AI） / 重开 |
| `POST /ops/tickets/{id}/misreport` | 标记误报 —— 紧急词表调优的唯一数据来源 |
| `DELETE /ops/tickets/test` | 清掉**全部测试工单**（`WHERE is_test = true` 写死在代码里）。需要 `admin:purge` 权限 |
| `GET /ops/metrics` | 指标看板 |
| `GET /ops/stream` | SSE 队列快照 + SLA 告警（`?once=true` 只推一帧） |
| `GET /ops/panel` | 监控面板 |

### 测试工单为什么要单开一列

自动化脚本（冒烟、压测、现场演示）造的工单和真实顾客的工单**结构上一模一样** ——
同样 P0、同样紧急流程、同样进队列。混在一起有三个后果：坐席打开面板看到几十张，
分不清哪张是眼前这位顾客的；SLA 指标（超时率、平均等待）被历史测试数据污染；
演示时"紧急工单"这个卖点直接被淹掉。

所以判定来源是**会话的 `channel`**：测试脚本用 `channel='test'` 建会话，真实入口用
`web` / `wechat` / `app`。建工单时顺手读一次会话的 channel 打上 `is_test` ——
让调用方自己声明，比事后按时间或按 `reason` 猜可靠得多（两个接管入口
`human_handoff` 节点与 API 层 `_force_handoff` 都不用手工传参，少一处漏一处）。

清库这条路径刻意做成 `WHERE is_test = true` 的**硬约束**而不是可选参数：
这个方法在设计上就不可能删到真实工单。之前清库靠手写 SQL，那条 SQL 不区分
测试与真实，在真实环境执行一次顾客的工单就没了 —— 把"只删测试"写进代码，
比写在运维手册里可靠。

### 这一层守住的五条不变量（都有冒烟断言）

1. **未接单不得告知用户"人工已接入"**：`accepted_at` 为空时，详情里 `human_joined=false`，
   且未接单直接回复会被 409 `not_accepted` 拒绝（避免没人负责的工单被随手回掉）
2. **接单 = 关掉 AI**：接单后聊天接口立刻 409 `human_takeover`；关单或重开时同步切换 ——
   这条是"AI 不与坐席抢话"的唯一开关，跨模块联动已断言
3. **坐席回复走规则层轻校验，不调 LLM**：`block` 级硬拦（隐私泄露）、
   `revise` 级软提示（疗效承诺、绝对化用语，需勾选确认后重发）；
   另外拦住"已为您取消/已帮您退"这类代执行表述 —— 坐席不能替用户办事
4. **脱敏在服务端做**：`service` 角色看到 `138****8000`，`doctor`/`compliance` 才见原文；
   权限按角色表判定，`compliance` 无权回复
5. **队列实时性**：MVP 用服务端轮询推送（3 秒一帧），生产换 Redis pub/sub 或
   Postgres LISTEN/NOTIFY —— 前端契约（`snapshot` / `alert`）不用动；
   单条 SSE 连接有 30 分钟寿命上限，避免僵尸连接

> **踩过的坑**：HTTP 头按规范只能是 latin-1，**中文坐席名塞不进请求头**
> （httpx 与浏览器都会直接报错）。所以只传 ASCII 的 `agent_id` + `role`，
> 显示名由服务端按 id 查 `ops.agent_user`（生产）或前端本地维护（MVP）。

---

## 目录结构

```
Code/
├─ app/
│  ├─ settings.py              # 环境变量 → 配置对象，启动即校验
│  ├─ cli.py                   # CLI：--demo / -t / --interactive
│  ├─ config/rules.yaml        # ★ 规则资产：紧急词表 / 风险标签 / 硬规则编号 / 澄清模板
│  ├─ prompts/                 # 所有 Prompt 模板（节点只做变量填充）
│  │  ├─ system.py  understand.py  kb.py  review.py  release.py  emergency.py
│  ├─ graph/
│  │  ├─ state.py              # ★ State + 三个 reducer（含两个关键坑的注释）
│  │  ├─ schemas.py            # 所有结构化输出契约（Pydantic）
│  │  ├─ nodes/                # intake / specialists / aggregate / release / revision / exits
│  │  ├─ sub_knowledge.py      # 子图 A：知识科普（含检索与精排）
│  │  ├─ sub_risk.py           # 子图 B：风险审查（含并行专家与决策表）
│  │  ├─ routers.py            # 主图路由函数（图上每个菱形对应一个）
│  │  └─ build.py              # ★ 主图装配
│  └─ services/                # 模型网关 / 规则引擎 / 检索 / 存储 / 凭据 / 依赖容器
├─ scripts/
│  ├─ smoke.py                 # 无需 pytest 的冒烟验证（75 项）
│  ├─ check_store_parity.py    # PgStore 与 FakePg 的接口必须逐字一致
│  ├─ check_schema.py          # 新建库 + 升级旧库两条路径都要能跑通（34 项）
│  ├─ check_ui.cjs             # 前端静态校验：语法 / 节点表同步 / 测试工单隔离（17 项）
│  └─ seed_kb.py               # 演示数据播种
├─ sql/schema.sql              # app / ops schema 表结构 + 增量变更（加列必读文末第 10 节）
├─ docker-compose.yml          # Postgres + Milvus（etcd + minio）
└─ tests/test_smoke.py         # 同样的断言，pytest 形态（需 pip install pytest-asyncio）
```

---

## 图 ↔ 代码对应

| 设计图元素 | 代码位置 |
|---|---|
| 五档出口 | `routers.after_risk_gate`（返回节点名或**节点名列表** → 并行出口） |
| `revise` 回到草稿产出层那条箭头 | `nodes/revision.revise` 返回 `Command(goto=state["origin_agent"])` |
| 七路扇出 | `intake.dispatch` 规划 + `intake.make_after_dispatch` 返回 `list[Send]` |
| 七路扇入 | 七个节点 → `aggregate`，靠 `keep_latest_turn` reducer 合并 |
| `risk_gate` 被调用两次 | `build.py` 里同一个编译好的子图 `add_node` 两次（`risk_gate` / `recheck`） |
| `await_confirm · interrupt` | `nodes/release.await_confirm` + CLI 里的 `Command(resume=...)` |
| 出口层强制校验 | `send` / `execute_op` 开头的 `verify_token` |
| 子图内部自环 | `sub_knowledge.after_verify`（上限 `KB_MAX_VERIFY_LOOP`） |
| clarify 预算 | `intake.dispatch` 里的 `clarify_count >= MAX_CLARIFY` 分支 |

---

## 实现过程中发现并修正的 4 处设计问题

这些**不是笔误，是跑起来才会暴露的逻辑缺陷**，设计文档已同步更正：

### 1. 修订再入点错了：回到「核对」不等于「重新生成」
原设计让父图 `revise` 回到子图的 `kb_verify`（理由是复用已通过的检索与证据）。
实际跑下来：内容没变 → 核对照样通过 → 审查照样命中同一条规则 →
**修订预算被白白耗尽，最后转人工**。
正确做法：`revise → kb_revise_in → kb_draft`（带审查意见重新生成）`→ kb_verify`。
复用证据这一点没有丢 —— `kb_draft` 读的就是已有的 `kb_evidence`。

### 2. 起草草稿的 reducer 会把修订前的旧稿也留下
`keep_latest_turn` 原来只按 `turn_id` 过滤，修订发生在同一轮内 → 新旧两份草稿同时存在，
`aggregate` 把它们拼在一起送审 → "改过的"和"没改的"一起被审 → 必然再次命中。
修正为**按 `(turn_id, agent)` 替换**：同一轮同一 Agent 的旧稿被替换，不同 Agent 的草稿保留（多意图靠这条）。

### 3. 累积型 reducer + 子图 = 审计日志成倍膨胀
LangGraph 把编译好的子图当节点用时，父图把状态传进子图，
子图返回的最终状态**包含它继承到的累积字段**，父图再通过 reducer 追加一次。
一次对话能刷出上百条重复事件。
MVP 采用内容去重的 reducer（`append_unique`）；生产建议给子图单独定义 schema，
把 `audit_log` 这类累积字段排除在外。

### 4. 紧急路径两个并行节点抢写同一个 key
紧急场景要"放行提示"和"转人工"并行，两个节点都写 `outbound` →
`InvalidUpdateError: Can receive only one value per step`。
修正为转人工写独立的 `handoff_notice`。

**另外两个真实报错**（属于环境/数据问题，也记一下便于排查）：
- `rules.yaml` 里未加引号的 `120` / `315` 会被 YAML 解析成整数，导致 `len()` 报错 → 已加引号，并在 `find_terms` 里做了 `str()` 兜底；
- LangGraph **不接受 path_map 的值是列表**（校验报 `unhashable type: 'list'`）→ 改为让路由函数直接返回节点名列表。

---

## 与设计文档的已知差异（有意为之，别当成漏实现）

| 差异 | 原因 |
|---|---|
| 风险审查子图**内部没有** feedback → retry_check → resubmit 自环 | 修订环只保留在父图一层。两套修订预算会互相吃掉次数，而且"退回谁修订"只有父图知道 |
| 子图复用主图 State，不各自定义私有 State | 避免"子图写了但父图 schema 没有 → 更新被静默丢弃"的坑；代价是键集中在一处 |
| 「转人工提示」和「紧急提示」不走 per-message 审查 | 预审通过的固定模板。这是**唯一被允许的例外**，代码注释里写明了，且同样落库与审计 |
| 多意图并行在 CLI 里看不到 | fake 档位的意图识别是关键词模拟；真实模型下 `dispatch` 会返回 `list[Send]` |

---

## 面试时可以主动讲的三个点

1. **"必经关卡"不是靠约定，是靠拓扑收口**：所有出站只能从 `send` / `execute_op` 出去，
   两者入口强制校验 `release_token`（绑定内容哈希 + 方案哈希 + 审查类别 + 有效期），
   出站前还会再脱敏一次。图内校验 + 业务侧二次校验。
2. **三种预算作用域不同**：`revision_count`（单轮内）/ `clarify_count`（**跨轮次**）/
   `kb_verify_round`（子图内）。混用一个计数器会让修订预算被追问吃掉。
3. **诚实标注不独立性**：MVP 只有一个模型，三位"专家"其实是同一模型的三次调用，
   **不构成独立复核**。审计表写 `escalation_independent = false`，
   `Settings.ESCALATION_MODEL` 一旦填了别家模型立刻变成真独立复核，图代码一行不改。

---

## 下一步（按落地顺序）

1. **运营后台**：工单队列、会话详情（含"AI 未发出的草稿 + 审查意见"）、处置面板、
   误报回流。完整设计见 `Workflow/ops-console.md`，表结构已在 `sql/schema.sql` 的 `ops` schema 里。
2. **真实依赖自测**：起 docker compose → 建表 → seed → 用真实 DeepSeek key 跑 `--demo`，
   把 fake 档位的断言在真实档位再跑一遍（重点看检索召回质量与审查结论是否合理）。
3. **规则资产审定**：`app/config/rules.yaml` 里的紧急词表与高风险标签
   **必须经医学顾问与法务审定**后才能上线，当前是草稿 v0.1。
4. **接视觉模型**：按决策在 `p_agent` 内部插 `vision_observe` 节点，主图不动。
