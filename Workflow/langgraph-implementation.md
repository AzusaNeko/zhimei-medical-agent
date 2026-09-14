# 智美医美顾问 · LangGraph 落地包 v1

配套文件（同目录）：

| 文件 | 内容 |
|---|---|
| `langgraph-main.mmd` | 主图：入口调度 → 七路草稿产出 → 聚合 → 风险审查 → 输出与执行闭环 → 出口层 |
| `langgraph-knowledge.mmd` | 知识科普 Agent 子图（`k_agent`）内部结构 |
| `langgraph-risk-gate.mmd` | 风险审查子图内部结构（同一份被主图调用三次） |
| `langgraph-implementation.md` | 本文档：P0 对照、节点清单、条件边清单、state 契约、硬约束 |
| `check-mermaid.cjs` | 图表自检：`node check-mermaid.cjs` 校验语法与引用完整性 |

命名约定：**节点 id 就是 `add_node` 的名称**；菱形节点就是 `add_conditional_edges` 的路由点，出边标签就是路由函数的返回值。

---

## 1. P0 补齐对照：你原来的版本 → 本版

| 你原版缺的 P0 项 | 本版落点 | 说明 |
|---|---|---|
| 澄清 fallback | `dispatch --"意图不明确"--> clarify`，且 `clarify --> aggregate` | 澄清问题同样是出站内容，必须过审 |
| 聚合层缺失 | `aggregate` 节点 = 唯一扇入点 | 七个来源（5 个 Agent + 澄清 + 紧急）统一汇聚，产出统一待审对象 |
| 修订无上限、无定向 | `feedback → retry_check → revise`，再由 `revise` 定向回到草稿产出层 | 计数器在代码里累加；超限走 `human_handoff`；回退目标由 `state.origin_agent` 决定 |
| 缺输出类型分支与执行闭环 | `output_type → await_confirm → execute_op → receipt → recheck → final_check → send` | 先审 → 明确确认 → 幂等执行 → **结果回复再审** |
| 缺硬性阻断 / 补件出口 | `risk_gate` 五档出口：`pass / revise / need_info / block / human` | 阻断优先级高于风险分数；资料不全不等于低风险 |
| 缺紧急优先路径 | `emergency_screen`（前置预筛）+ `emergency_check`（审查原始消息） | 双层：入口抢时间，关卡内复查，防"只审草稿"漏判 |
| 缺放行凭据与审计 | `issue_token` + `send` 强制校验 + `audit_block` | 内容或参数变化必须重新审查 |
| 缺知识库依赖 | 收在 `k_agent` / `p_agent` 子图内部 | 检索是节点内部动作，不画成图节点（避免把资源层当 Agent） |

---

## 2. 主图节点清单

| node id | 中文职责 | 类型 | 依赖 |
|---|---|---|---|
| `normalize` | 输入标准化、会话绑定与授权校验 | 确定性 | 会话 / 权限服务 |
| `emergency_screen` | 紧急风险信号预筛 | 规则引擎 | 紧急词表（带版本号） |
| `classify` | 意图识别与槽位抽取 | LLM | Qwen3-8B |
| `dispatch` | 按意图分派（支持多意图 `Send` 并行） | 规则配置 | — |
| `clarify` | 生成澄清问题草稿 | LLM | Qwen3-8B |
| `emergency_draft` | 紧急风险提示草稿 + 接管请求 | 模板 + LLM | 预审通过的话术模板 |
| `k_agent` | 知识科普（内部 9 节点） | 子图 | 8B / 30B / 32B / BGE-M3 |
| `r_agent` | 项目推荐 | 子图 | 8B / 30B + 业务系统 |
| `c_agent` | 资质与门店查询 | 子图 | 8B / 30B + 机构数据库 |
| `b_agent` | 预约管理 | 子图 | 8B / 30B + 预约系统 |
| `p_agent` | 术后护理与随访 | 子图 | 8B / 30B / VL / InternVL / BGE |
| `aggregate` | 聚合待审对象：草稿 / 操作请求 | 确定性 | — |
| `risk_gate` | 风险审查子图 | 子图 | 见 `langgraph-risk-gate.mmd` |
| `feedback` | 输出修改意见并记复审次数 | 确定性 + 30B | — |
| `retry_check` | 复审次数 < 2 | 确定性 | — |
| `revise` | 定向修订，并返回 `Command(goto=state["origin_agent"])` | LLM | 原 Agent 的模型 |
| `output_type` | 输出类型判定：回复 / 操作请求 | 确定性 | — |
| `send` | 发送已审查回复 | 确定性 | **强制校验 `release_token`** |
| `await_confirm` | 等待用户明确确认 | `interrupt` | checkpointer |
| `execute_op` | 幂等执行已审操作 | 确定性 | 业务系统 + 幂等键 |
| `receipt` | 生成真实执行结果回复 | LLM | Qwen3-30B |
| `recheck` | 执行结果回复复审 | 子图 | **复用 `risk_gate`** |
| `final_check` | 结果回复是否通过 | 确定性 | — |
| `human` | 创建人工接管工单 | 确定性 | 工单系统 |
| `plan_void` | 作废方案并生成通用回复 | 规则 + 模板 | — |
| `audit_block` | 记录规则编号并终止 | 确定性 | 审计服务 |

---

## 3. 条件边清单

| 路由点 | 返回值 | 目标节点 |
|---|---|---|
| `emergency_screen` | `hit` / `miss` | `emergency_draft` / `classify` |
| `dispatch` | `knowledge` / `recommend` / `clinic` / `booking` / `postcare` / `clarify` | 5 个专业 Agent / `clarify` |
| `risk_gate` | `pass` / `revise` / `need_info` / `block` / `human` | `output_type` / `feedback` / `send` / `audit_block` / `human_handoff` |
| `retry_check` | `retry` / `exhausted` | `revise` / `human_handoff` |
| `output_type` | `reply` / `operation` | `send` / `await_confirm` |
| `await_confirm` | `confirmed` / `denied` | `execute_op` / `plan_void` |
| `final_check` | `ok` / `fail` | `send` / `human_handoff` |

风险审查子图内部的条件边见另一张图，返回值与主图 `risk_gate` 的五个出口一一对应。

---

## 附一、知识科普子图（`k_agent`）节点与出口

对应 `langgraph-knowledge.mmd`。该子图只产出带证据的待审草稿，从不直接对用户说话。

**边界契约**

- 入口 1：主图 `dispatch` 按「科普咨询」意图分派进入 `kb_intake`
- 入口 2：主图风险审查判「需修改」时，`revise` 定向进入 `kb_verify`（不是回到 `kb_draft`）
- 出口：`kb_return` / `kb_clarify` / `kb_handoff` 三个出口都回到主图 `aggregate`，统一送风险审查

| node id | 职责 | 模型 / 依赖 |
|---|---|---|
| `kb_intake` | 1. 任务接收与边界判断 | Qwen3-8B |
| `kb_scope` | 属于科普范围？否则转 `kb_handoff` | 路由规则 |
| `kb_handoff` | 返回转交建议，交回主图送审 | — |
| `kb_context` | 2. 读取必要且经授权的上下文 | 权限 / 记忆服务 |
| `kb_decompose` | 3. 解析问题并拆分检索子任务 | Qwen3-8B |
| `kb_sufficiency` | 信息是否充分？否则转 `kb_clarify` | Qwen3-8B + 规则 |
| `kb_clarify` | 生成简短澄清问题，同样需要过审 | Qwen3-8B |
| `kb_retrieve` | 4. 混合检索审核知识库，并按项目 / 状态 / 版本过滤 | BGE-M3 + Reranker |
| `kb_evidence` | 5. 筛选证据与充分性检查 | Qwen3-32B → R1 |
| `kb_evidence_ok` | 证据足以回答？否则转 `kb_limit` | 确定性 |
| `kb_limit` | 缩小回答范围、说明未知 | 规则 + 30B |
| `kb_draft` | 6. 生成科普草稿 | Qwen3-30B-A3B |
| `kb_verify` | 7. 事实与引用逐项核对 | Qwen3-32B → R1 |
| `kb_verified` | 重要结论均有依据？否则回到 `kb_draft` | 确定性 |
| `kb_return` | 8. 返回结构化待审草稿：正文 / 引用 / 缺口 / 路由建议 | Qwen3-30B-A3B |
| `revise_in` | 跨子图入口：定向进入 `kb_verify` | 主图 `revise` |
| `to_aggregate` | 跨子图出口：三个出口都回到主图 `aggregate` | 主图 `aggregate` |

**修订为什么回到 `kb_verify` 而不是 `kb_draft`**：审查意见通常针对具体表述与引用，回到事实核对可以复用已通过的检索与证据，同时强制修订后的每一步重新核对，避免「改一句、错一句」。

---

## 4. state 契约（初稿）

```python
from typing import Annotated, Literal, TypedDict
import operator


class ZhimeiState(TypedDict, total=False):
    # ---- 会话与入口 ----
    thread_id: str
    user_input: str
    attachments: list[dict]          # 已授权、已脱敏的图片 / 文档
    auth_context: dict               # 身份、归属关系、授权范围

    # ---- 意图与分派 ----
    intents: list[str]               # 多意图 → Send 并行扇出
    slots: dict
    emergency: bool
    priority: Literal["normal", "emergency"]

    # ---- 扇入收集：并行写入必须带 reducer ----
    agent_outputs: Annotated[list[dict], operator.add]   # {agent, content, citations, gaps, route_hint}
    panel_reviews: Annotated[list[dict], operator.add]   # 多专家意见，含命中原文与规则编号
    audit_log: Annotated[list[dict], operator.add]

    # ---- 待审对象 ----
    review_kind: Literal["content", "operation", "result_reply", "emergency"]
    draft: dict                      # 当前待审草稿 / 操作请求
    evidence: list[dict]
    operation: dict | None           # {action, params, plan_hash}

    # ---- 审查结论 ----
    hard_rule_hits: list[dict]
    verdict: Literal["pass", "revise", "need_info", "block", "human"] | None
    review_feedback: list[str]
    revision_count: int              # 只由 feedback 节点累加
    origin_agent: str                # 修订回退目标（专业 Agent 节点名）
    release_token: str | None        # 仅 issue_token 可签发

    # ---- 确认与执行 ----
    plan_hash: str
    confirmed: bool
    idempotency_key: str
    execution_result: dict | None

    # ---- 人工与审计 ----
    handoff_ticket: dict | None
```

---

## 5. 必须写进代码的硬约束

1. **凭据绑定内容。** `release_token` = 签名(内容哈希 + `plan_hash` + `review_kind` + 过期时间)；`send` 与 `execute_op` 入口强制校验；出站网关再校验一次（图内 + 图外双保险）。
2. **计数器只在代码里。** `revision_count` 仅由 `feedback` 累加；每轮进入 `risk_gate` 前清空 `verdict` 与 `release_token`，避免读到上一轮的陈旧值。
3. **`interrupt` 之前无副作用。** `await_confirm` 之前的节点会被重放，所有节点必须幂等。
4. **确认绑定方案。** resume 时校验 `plan_hash` 与挂起时一致，且方案有有效期；不接收泛泛的"好的"。
5. **外部执行恰好一次靠业务，不靠框架。** `idempotency_key = f"{thread_id}:{plan_hash}"`，执行前查真实业务状态。
6. **并行写入必须用 reducer。** `aggregate` 是唯一扇入点，多意图 `Send` 的结果只能经它汇总。
7. **显式设置 `recursion_limit`（建议 30–40）**，并把 `GraphRecursionError` 映射为转人工，而不是报 500。
8. **checkpointer 用 Postgres，`durability` 至少 `async`**；同一 `thread_id` 串行执行（加锁），避免并发 run 冲突。
9. **模型客户端分离。** DeepSeek-R1 与 InternVL 走独立客户端并带家族标记，禁止同家族模型互相"确认"。

---

## 6. 我故意偏离蓝图的四处（说明理由）

1. **紧急预筛前置**：蓝图在 Intake 内与风险审查内各查一次；本版在 `L0` 前置抢时间，同时保留关卡内的 `emergency_check` 复查**原始消息**，形成"快路径 + 完整校验"双层。
2. **`need_info` 与 `plan_void` 也走 `send`**：蓝图里"返回补充要求"没有明确过审；本版强制任何出站内容都带凭据，不开口子。
3. **定向回退目标显式化**：蓝图只说"退回专业 Agent"，没画目标选择；本版在 `revise` 节点内返回 `Command(goto=state["origin_agent"])`，图上用一条回到「草稿产出层」的箭头表示，避免五条长虚线造成的视觉噪音。
4. **多意图并行只在注释与文档体现**：图上保留互斥分支以保可读性，`Send` 扇出在实现时展开。

---

## 7. v1 明确不做（避免范围蔓延）

- 人工坐席侧的回复出站（走工单系统自己的通道，仅允许预审通用话术）
- 知识库写入与审核流水线
- 模型服务部署与路由（vLLM）—— 图内只通过 client 注入
- 跨轮次澄清的状态收敛（v1 先做单轮澄清）
- 视觉链路（`Qwen3-VL` / `InternVL`）先在 `p_agent` 内留接口，不接真实图片

---

## 8. 落地顺序

**第 1 步（沙箱，不接业务系统）**
`normalize → emergency_screen → classify → dispatch → k_agent → aggregate → risk_gate(content) → feedback → retry_check → revise → output_type → send`
跑通"有界修订环 + 五档出口 + 凭据校验"，用假数据验证循环不会失控。

**第 2 步（接预约系统，只读 / 预发）**
`await_confirm · interrupt → execute_op → receipt → recheck → final_check`
重点验证：挂起跨小时恢复、方案哈希校验、幂等键、结果回复复审。

**第 3 步（补齐合规出口）**
`block / need_info / human` 三个出口 + 审计落库 + `release_token` 签名与过期。

**P1**：多专家并行 + R1 升级 + `biz_check` + 多意图 `Send`
**P2**：评测集 + 模型路由与降级 + 是否引入 LoRA（未达指标不训）
