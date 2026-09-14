# 人工接管与运营后台（MVP v0.1）

> 目标：让"转人工"不是一个黑盒终点，而是**有人接得住、接得及时、有记录、能回流调优**的闭环。
> 表结构见 `data-model.md` 第 9 节；图侧接口是 `human_handoff` 节点。

---

## 1. 角色与权限

| 角色 | 看得到什么 | 能做什么 | 不能做什么 |
|---|---|---|---|
| `service`（客服坐席） | 工单队列、会话详情、画像摘要、风险报告 | 接管、回复、关闭、标记误报、升级给医师 | 看不到未脱敏的身份证/完整手机号；不能放行被 `block` 的内容 |
| `doctor`（值班医师） | 同上 + 症状时间线、图片原件 | 处置医疗类工单、给出结论性回复、关闭 | 不能改规则、不能看其他门店数据 |
| `compliance`（合规） | 全部审计、规则命中统计、误报样本 | 审阅审计、调整规则与词表（走发布流程） | 不能直接给用户发消息 |
| `admin`（管理员） | 全部 | 值班表、权限、参数配置 | —— |

**两条硬规则**（写进 API 层，不能只写在文档里）：

1. `service` 角色的接口**必须**对手机号/身份证做脱敏后再返回，脱敏在服务端做，不在前端做。
2. **放行权限与回复权限分离**：被 `block` 的内容任何人都不能直接发给用户，
   要发只能走"合规复核 → 修改 → 重新送审"。

---

## 2. 工单状态机

```
                    ┌──────────────────────────────────────┐
                    │                                      │
  [图] human_handoff │                                      │
        │            ▼                                      │
        └──────►  open ──accept──► accepted ──► in_progress ─┴─► closed
                   │                 │            │
                   │                 │            └─escalate──► escalated ──► in_progress
                   │                 │                              │
                   └──auto_close─────┴──────────────────────────────┘
                     （超时未接）
```

| 状态 | 含义 | 可转移至 |
|---|---|---|
| `open` | 工单已创建，等待坐席接单 | `accepted` / `closed`（超时自动关闭） |
| `accepted` | **坐席已接单**（`accepted_at` 落库） | `in_progress` / `escalated` / `closed` |
| `in_progress` | 正在与用户对话 | `escalated` / `closed` |
| `escalated` | 已升级给值班医师 | `in_progress` / `closed` |
| `closed` | 已结束（`close_reason` 必填） | 可 `reopen`（用户再次追问时） |

### 2.1 关键不变量

> **`accepted_at` 为空时，任何接口都不得向用户返回"人工已接入 / 客服马上联系您"。**

对应实现：`human_handoff` 节点发出的文案只能是"**已为您转接人工，请稍候**"；
只有工单 `accepted` 之后，坐席侧（或系统）才能发"客服 xxx 已接入"。

MVP 的一条简化：`human_handoff` 节点只发"已转接，请稍候"这一句（已审模板），
后续所有消息都由坐席通过运营后台发出，**不再经过图**。

### 2.2 接管后 AI 是否继续回复

**不继续。** 工单 `accepted` 时，同步把 `app.chat_session.ai_enabled` 置为 `false`；
此后用户消息直接进人工队列，不再触发图运行。

理由（也是面试时的加分点）：

- 图已经 `END`，让它长期挂起等待人工会占着状态不放，没有收益
- 人工接管后的多轮对话是**人的对话**，不需要 AI 再插话
- 避免"AI 和人工同时回复用户"这种最糟糕的体验

恢复 AI：坐席关闭工单时置回 `true`（或用户开启新会话）。

---

## 3. 后端接口

```python
# api/ops.py —— 全部要求坐席身份，且按角色做字段脱敏

GET  /ops/tickets                 # 队列：?status=open&priority=P0&page=
                                  #   默认排序：priority(P0优先) → created_at ASC
GET  /ops/tickets/{ticket_id}     # 详情：画像摘要 + 最近5轮 + 风险报告 + 命中规则 + 证据
POST /ops/tickets/{ticket_id}/accept    # 接单 → 写 accepted_at、置 ai_enabled=false
POST /ops/tickets/{ticket_id}/reply     # 坐席回复 → 走 outbox 发出（见 §5）
POST /ops/tickets/{ticket_id}/escalate  # 升级给值班医师（必填理由）
POST /ops/tickets/{ticket_id}/close     # 关闭（close_reason 必填）
POST /ops/tickets/{ticket_id}/reopen    # 重新打开
POST /ops/tickets/{ticket_id}/misreport # 标记误报 → ops.misreport_feedback

GET  /ops/stream                   # SSE：新工单 / 超时告警 / 状态变更，用于队列实时刷新
GET  /ops/metrics                  # 指标看板数据（见 §6）
```

**列表接口的排序即产品**：P0 必须永远在最上面，且 P0 工单进入队列时触发前端提示音 + 弹窗。
一条工单从 `open` 变 `P0` 却没有坐席看到，这个功能就等于没做。

### 3.1 超时告警

```python
# 定时任务（每 10 秒扫一次）
if ticket.status == "open":
    if ticket.priority == "P0" and now - ticket.created_at > 60s:
        notify_duty_doctor(ticket); ticket.event("sla_breach")
    elif ticket.priority == "P1" and now - ticket.created_at > 10min:
        notify_shift_lead(ticket); ticket.event("sla_breach")
    elif now - ticket.created_at > 48h:
        auto_close(ticket, reason="no_response")
```

---

## 4. 前端页面结构

四个视图，不要做成一个大杂烩。

### 4.1 视图一：实时队列（坐席默认页）

```
┌───────────────────────────────────────────────────────────────┐
│ [P0 急诊 ●3] [P1 优先 ●7] [全部] [我的]        值班：医师A ●在线 │
├───────────────────────────────────────────────────────────────┤
│ 🔴 P0  02:13  术后异常·视力模糊   浦东店·热玛吉·术后3天   [接单] │
│ 🔴 P0  01:47  过敏·呼吸困难       静安店·水光针·术后当天   [接单] │
│ 🟠 P1  08:20  退费争议            已沟通2轮              [接单] │
│ 🟠 P1  12:05  审查超限转人工      修订2次仍未通过         [接单] │
│ ⚪ P2  35:10  追问超限转人工      连续2次无法定位意图      [接单] │
└───────────────────────────────────────────────────────────────┘
```

每行必须有的信息：**优先级、等待时长、转人工原因、关键业务上下文（项目 + 门店 + 术后天数）**。
少任何一项，坐席都得点进去才知道要不要先接——那就失去了队列的意义。

### 4.2 视图二：会话详情

三栏布局：

| 栏 | 内容 |
|---|---|
| 左（用户侧） | 画像摘要（≤400 字）+ 授权状态 + 历史项目/预约 + 敏感字段脱敏展示 |
| 中（对话） | 最近 5 轮完整对话（含 AI 未发出的草稿与审查意见，用灰底标注"未发送"） |
| 右（风险报告） | 风险等级 + 命中规则编号与原文片段 + 三位专家意见 + 是否同族复核 + 证据列表 |

**"未发送的草稿"很重要**：坐席需要看到 AI 想说什么、为什么被拦下来，这决定了他怎么接话。

### 4.3 视图三：处置面板

动作按钮：`接管` / `回复` / `转医师` / `关闭` / `标记误报` / `查看完整审计`。

回复框需要：

- 快捷话术（按原因分类：急诊引导、退费流程、追问澄清）
- 回复前的**轻校验提示**（见 §5）
- 发送后记录 `agent_action_log`

### 4.4 视图四：指标与规则调优

```
[今日概览]
转人工率 8.3%    P0 平均响应 42s    P0 超时 1 次
审查一次通过率 76%  平均修订次数 0.31  block 命中 12 次

[Top 命中规则]              [误报样本队列]
AD-001 疗效承诺      31     待复核 14 条   [逐条看] [批量确认误报]
MED-001 诊断结论      9
OPS-001 重复执行      3
```

**误报样本队列是这个面板真正的价值所在** —— 没有它，紧急词表永远是拍脑袋定的。

---

## 5. 坐席回复要不要过审？

这是一个必须明确取舍的点。蓝图的要求是"**人工不能绕过权限和业务限制**"，
但如果坐席每句话都等 LLM 审查 20 秒，工单就没法干活了。

**MVP 建议：规则层轻校验 + 审计留痕，不调 LLM。**

```python
async def agent_reply(ticket_id: str, text: str, agent: AgentUser):
    # 1) 确定性规则快筛（毫秒级）：仅查禁发词与敏感数据
    hits = rules.quick_check(text)          # AD-001/002、PRIV-001
    if hits:
        if any(h.action == "block" for h in hits):
            raise Forbidden("命中禁止发送项，请修改后再发")   # 硬拦
        return {"warning": hits}            # 软提示：前端高亮 + 需二次确认
    # 2) 权限校验：坐席不能替用户执行操作
    if looks_like_operation(text):
        raise Forbidden("坐席不能代为执行预约/退费操作，请引导用户走系统流程")
    # 3) 直接发送 + 审计
    await outbox.send(ticket_id, text, actor=f"agent:{agent.agent_id}")
    await log_agent_action(agent, ticket_id, "reply", {"text_hash": sha256(text), "rule_hits": hits})
```

为什么这样取舍（面试可以讲）：

- 人工回复的**风险性质不同**：坐席是有资质、有责任制的人，不是概率模型
- 但**硬性阻断项仍必须拦**（泄露隐私、篡改记录）——这类错误人是会犯的
- 全程留痕，事后可追溯到人

---

## 6. 指标定义（先定义清楚，再谈优化）

| 指标 | 定义 | 用途 |
|---|---|---|
| 转人工率 | 产生工单的会话 / 总会话 | 衡量 AI 覆盖率 |
| P0 响应时长 | `accepted_at - created_at`，看 P50/P95 | 急诊场景的核心指标，**P95 比 P50 重要** |
| SLA 违约次数 | 超时未接的工单数 | 值班排班依据 |
| 一次通过率 | `review_round = 1` 且 `verdict = pass` 的占比 | 衡量生成侧质量 |
| 平均修订次数 | `revision_count` 均值 | 同上 |
| block 命中 Top | 按 `rule_id` 聚合 | 定位生成侧的系统性偏差 |
| 误报率 | `false_positive` / 总命中 | **调词表的唯一依据** |
| 同族复核占比 | `escalation_used and not escalation_independent` | 提醒这是 MVP 的已知缺口 |

---

## 7. 值班与告警

| 事件 | 通知对象 | 渠道 | 时限 |
|---|---|---|---|
| P0 工单创建 | 当日值班医师 + 客服主管 | 面板弹窗 + 提示音 + IM | 立即 |
| P0 超过 60 秒未接 | 客服主管 + 医师 | IM + 电话（可选） | 60 秒 |
| P1 超过 10 分钟未接 | 客服主管 | IM | 10 分钟 |
| `block` 命中突增（1 小时内 > 20 次） | 合规 | IM | 1 小时 |
| 模型调用错误率 > 5% | 开发 | IM | 5 分钟 |

---

## 8. 与图的接口边界（谁来写哪些字段）

| 字段 | 写入方 | 时机 |
|---|---|---|
| `handoff_ticket.*`（创建） | **图** → `human_handoff` 节点 | 转人工时 |
| `handoff_ticket.accepted_at` / `assigned_to` | **运营后台** | 坐席接单时 |
| `chat_session.ai_enabled` | **运营后台** | 接单置 false、关单置 true |
| `chat_session.emergency` | **图** → `emergency_screen` | 命中紧急信号时 |
| `handoff_event.*` | **运营后台** | 每次处置动作 |
| `ops.misreport_feedback.*` | **运营后台** | 坐席标记误报时 |
| `app.chat_message`（人工回复） | **运营后台**（`role='agent'`） | 坐席发送时 |

**规矩**：图只负责"创建工单并说一句已转接"，**接管之后的一切都在图外**。
这条边界如果模糊，就会出现"AI 与人同时回复"的经典事故。

---

## 9. MVP 可以砍掉的部分

| 功能 | MVP | 理由 |
|---|---|---|
| 坐席 IM / 电话告警 | 砍 | 面板弹窗 + 提示音够演示 |
| 工单分配算法（按技能/负载） | 砍，用抢单 | 工单量小，抢单更简单 |
| 值班排班表 | 砍，写死一个值班医师 | 演示用 |
| 误报自动调优 | 砍，只做人工确认队列 | 数据量不够，谈不上调优 |
| 完整审计检索界面 | 保留只读列表 | 面试时这是加分项，别砍 |
| 工单与现有客服系统对接 | 砍，先用本地表 | 接口对接成本高，演示价值低 |

---

## 实现补充（`Code/app/ops/` 已实现并验证）

代码在 `Code/app/ops/`：`deps.py`（角色权限与脱敏）、`service.py`（状态机与不变量）、
`routes.py`（REST + SSE）、`panel.html`（单文件面板）。
验证脚本 `Code/scripts/smoke_ops.py`（36 项断言，含跨模块联动）。

### 落地情况

| 设计 | 实现 |
|---|---|
| 四个视图 | 合并为一个单文件面板（队列 / 详情 / 处置 / 指标同页），无构建步骤 |
| 工单状态机与五条不变量 | `service.py`，全部有断言 |
| 角色权限矩阵 | `deps.py` 的 `PERMISSIONS`，路由层只查表 |
| 误报回流 | `POST /ops/tickets/{id}/misreport` → `ops.misreport_feedback` |
| SLA 告警 | `/ops/stream` 推 `alert` 事件（同一工单只推一次，不重复刷屏） |

### 两处实测与设想不同

1. **中文坐席名不能放进请求头**。HTTP 头按规范是 latin-1，`X-Agent-Name: 测试坐席`
   会被 httpx / 浏览器直接拒绝（`UnicodeEncodeError`）。
   正确做法：请求头只传 ASCII 的 `agent_id` + `role`，**显示名由服务端按 id 查 `ops.agent_user`**
   （生产）或前端本地维护（MVP）。这一点在设计阶段完全没意识到。
2. **队列实时性用服务端轮询推送**（3 秒一帧），不是真正的 pub/sub。
   生产换 Redis pub/sub 或 Postgres LISTEN/NOTIFY 时**只替换流本身**，前端契约
   （`snapshot` / `alert`）不用动。另外单条 SSE 连接设了 30 分钟寿命上限，
   否则客户端悄悄断开后会留下僵尸连接占着连接池。

### 报错的顺序也是设计的一部分

`accept` 的判断顺序是：**已关闭 → 已被接单 → 状态机**。
按"代码检查顺序"写的话，重复接单会得到"状态 accepted 不能变为 accepted"
——对坐席毫无解释力。**错误信息要按"对使用者最有解释力"的顺序选，而不是按实现顺序。**

同理，关闭工单"缺原因"由领域层给 `reason_required`（400），
而不是让 Pydantic 的 `min_length` 拦成通用 422 —— 前端需要能区分
"参数格式错"与"业务上必须填原因"。
