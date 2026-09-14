# 智美医美顾问 · LangGraph 架构伪代码讲解

> 技术栈基线：Python 3.11 / 全异步 · FastAPI（REST + SSE）· LangChain 1.2.10 + LangGraph 1.0.9 ·
> DeepSeek-V4.1（API）· Milvus（向量）· PostgreSQL（业务 + checkpoint）·
> BGE-M3（进程内，dense + sparse）· BGE-Reranker（进程内）
>
> 本文是**伪代码讲解**：重点在"为什么这样连、State 里放什么、节点签什么契约"，
> 不是可直接运行的实现。涉及 LangGraph 1.0 的具体签名时以你实际安装的版本为准。

---

## 目录

1. [技术栈带来的三个必须做的取舍](#1-技术栈带来的三个必须做的取舍)
2. [工程分层与文件结构](#2-工程分层与文件结构)
3. [State 设计](#3-state-设计)
4. [主图：节点、边、路由函数](#4-主图节点边路由函数)
5. [子图 A：知识科普](#5-子图-a知识科普)
6. [子图 B：风险审查](#6-子图-b风险审查)
7. [关键 Prompt 设计](#7-关键-prompt-设计)
8. [FastAPI + SSE 接入](#8-fastapi--sse-接入)
9. [异步与阻塞边界](#9-异步与阻塞边界)
10. [落库清单](#10-落库清单)
11. [需要你确认的开口项](#11-需要你确认的开口项)

## 配套文档

| 文件 | 内容 |
|---|---|
| **`langgraph-pseudocode.md`** | 本文：技术栈取舍 / State / 主图 / 子图 / Prompt / SSE |
| `supervisor-intake.md` | 总控调度 Agent 与意图识别分流 Agent 的伪代码与关键 Prompt 详解 |
| `mvp-config-rules.md` | 规则资产与参数基线：紧急信号词表、高风险标签、硬规则编号表、可调参数选项 |
| `data-model.md` | PostgreSQL 完整表清单（本项目专用） |
| `ops-console.md` | 人工接管工单状态机 + 后台监控面板设计 |
| `langgraph-*.mmd` / `langgraph-v1.html` | 三张架构图与可视化页面（`node check-mermaid.cjs` 自检） |

---

## 1. 技术栈带来的三个必须做的取舍

这三件事不改代码写不出来，所以放在最前面。

### 1.1 单一 API 模型 ⇒ "独立复核"降级为同族复核【MVP 决策：方案 B】

设计里最强的合规机制是"**同源模型不自我裁决**"：多专家并行审查 + 由**另一个模型家族**做二次复核。
MVP 阶段只用一个模型（DeepSeek-V4.1 API），于是：

- 三个"专家"其实是**同一个模型的三次调用**，只是提示词与视角不同 → 相关性高，可能一起犯同一个错
- 升级复核仍在同一家族 → 复核的独立性更弱

三条路（保留作为后续升级路径）：

| 方案 | 做法 | 代价 |
|---|---|---|
| A | 升级复核 `escalation` 接到**另一家模型**，只要家族不同即可 | 多一个供应商/密钥 |
| **B（MVP 采用）** | 承认同族复核，改为**强化确定性规则 + 决策表 + 人工兜底**，并在审计里诚实标注"复核为同族" | 合规说服力下降 |
| C | 高风险场景**直接跳人工**，不假装有模型复核 | 人工成本上升 |

**MVP 采用 B，落地三条：**

1. `escalation` 角色**仍然单独存在**（独立 prompt、独立温度、独立证据集），只是复用同一个模型端点。
   三份专家意见要"视角不同"，不要写成同一段提示词的三次复制。
2. 审计表写 `escalation_independent = false`，运营面板上显示"同族复核"标识 —— **可解释比好看重要**。
3. `Settings.ESCALATION_MODEL` **留空即同族；填上别家模型立刻变成真独立复核，图代码一行不改。**

> **面试时如果被问到"你怎么保证审查独立性"**，正确回答是：
> "MVP 阶段是同一个模型的多次不同视角调用，**不构成独立复核**；我们把它标注在审计里，
> 并预留了接入第二家模型的开关。" —— 承认它，比假装它有说服力得多。

### 1.2 视觉链路：选定 Qwen-VL 系，MVP 不设计【MVP 决策：留桩】

原设计里 `Qwen3-VL-8B` + `InternVL3.5` 负责术后照片的 OCR 与非诊断性观察。
MVP 阶段**不实现多模态**，但要保证三件事，否则将来接不进来：

1. `attachments` 字段和 `p_agent` 的内部位置保留（图上视觉节点的位置已预留）
2. 图片只做**入库 + 授权校验 + 转人工**，绝不解析
3. **不假装看图**：回复里不得出现"从图片看…""照片显示…"这类表述。
   用户传图时的标准话术是"已收到您的图片，已转交值班医师查看"（`p_agent` → `human_handoff`）

将来接 VL 模型时只需在 `p_agent` 内部插一个 `vision_observe` 节点，主图不动。

### 1.3 BGE-M3 / Reranker 进程内 ⇒ 会阻塞事件循环

这是最容易踩的性能坑：`SentenceTransformer.encode()` 和 reranker 都是**同步 CPU/GPU 密集调用**，
直接在 `async def` 节点里调用会把整个事件循环卡住（其他用户的 SSE 全部停摆）。

**规矩：所有进程内推理一律走线程池，且并发用信号量限流。**

```python
# infra/inprocess_models.py
import asyncio, torch
from sentence_transformers import SentenceTransformer, CrossEncoder

_EMBED_SEM = asyncio.Semaphore(settings.EMBED_CONCURRENCY)   # 例如 4
_RERANK_SEM = asyncio.Semaphore(settings.RERANK_CONCURRENCY) # 例如 4

class InProcessEncoder:
    """BGE-M3：一次调用同时产出 dense 与 sparse。"""
    def __init__(self, path: str):
        self.model = SentenceTransformer(path, device=settings.EMBED_DEVICE)

    def _encode_sync(self, texts: list[str]) -> dict:
        out = self.model.encode(
            texts,
            normalize_embeddings=True,
            return_sparse=True,          # BGE-M3 的 learned sparse 权重
            batch_size=settings.EMBED_BATCH,
        )
        return {
            "dense": out["dense_vecs"].tolist(),
            "sparse": [self._to_milvus_sparse(v) for v in out["lexical_weights"]],
        }

    async def encode(self, texts: list[str]) -> dict:
        async with _EMBED_SEM:
            # 关键：to_thread 把同步推理挪出事件循环
            return await asyncio.to_thread(self._encode_sync, texts)

class InProcessReranker:
    def __init__(self, path: str):
        self.model = CrossEncoder(path, device=settings.RERANK_DEVICE)

    def _score_sync(self, query: str, docs: list[str]) -> list[float]:
        return self.model.predict([(query, d) for d in docs]).tolist()

    async def score(self, query: str, docs: list[str]) -> list[float]:
        async with _RERANK_SEM:
            return await asyncio.to_thread(self._score_sync, query, docs)
```

模型在 `lifespan` 里加载一次并注入依赖，**不要在节点里重新加载**：

```python
# main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.encoder  = InProcessEncoder(settings.BGE_M3_PATH)
    app.state.reranker = InProcessReranker(settings.BGE_RERANKER_PATH)
    app.state.milvus   = AsyncMilvus(settings.MILVUS_URI)
    async with AsyncPostgresSaver.from_conn_string(settings.PG_DSN) as cp:
        await cp.setup()                       # 首次建表
        app.state.graph = build_graph(cp, deps=app.state)
        yield
```

---

## 2. 工程分层与文件结构

```
app/
├─ main.py                      # FastAPI + lifespan（模型/连接池/图 只初始化一次）
├─ api/
│  ├─ chat.py                   # POST /chat/{sid}/stream（SSE）、POST /chat/{sid}/confirm
│  └─ schemas.py                # 请求/响应/SSE 事件契约
├─ graph/
│  ├─ state.py                  # ★ 主图 State + reducer
│  ├─ build.py                  # ★ 主图节点与边
│  ├─ routers.py                # 所有路由函数（条件边的返回值）
│  ├─ nodes/
│  │  ├─ intake.py              # normalize / emergency_screen / classify / dispatch
│  │  ├─ specialists.py         # k/r/c/b/p 五个专业 Agent 的包装节点
│  │  ├─ aggregate.py           # 汇总待审对象
│  │  ├─ release.py             # output_type / await_confirm / execute / receipt / recheck / final_check
│  │  ├─ revision.py            # feedback / retry_check / revise / plan_void
│  │  └─ exits.py               # send / human_handoff / audit_block
│  ├─ sub_knowledge/
│  │  ├─ state.py               # 子图私有 State 键
│  │  ├─ build.py               # ★ 知识科普子图
│  │  └─ retrieval.py           # Milvus 混合检索 + 精排
│  └─ sub_risk/
│     ├─ state.py
│     ├─ build.py               # ★ 风险审查子图
│     ├─ rules.py               # 确定性硬规则（带规则编号）
│     └─ decision.py            # 风险聚合与决策表
├─ prompts/
│  ├─ system.py                 # 通用约定
│  ├─ classify.py  kb.py  review.py  release.py
│  └─ emergency_templates.py    # 预审通过的固定话术（不走 LLM）
├─ services/
│  ├─ llm.py                    # ★ ModelGateway（角色 → 模型/温度/超时）
│  ├─ milvus_store.py           # 混合检索（dense + sparse + RRF）
│  ├─ pg.py                     # 业务库：会话/画像/预约/审计/幂等/工单
│  ├─ security.py               # release_token 签名与校验、plan_hash
│  └─ profile.py                # 画像与记忆（授权/脱敏/保留期限）
└─ settings.py
```

**一条纪律**：`graph/` 里只做编排与契约，**不直接写 SQL、不直接调模型 SDK**——
全部经 `services/`。这样图可以脱离真实依赖做单元测试。

---

## 3. State 设计

### 3.1 设计原则

1. **只放可序列化的数据**（dict / list / str / int / bool）。checkpointer 要把它写进 PostgreSQL，
   塞 Pydantic 对象、模型客户端、数据库连接会直接炸。
2. **区分"日志型"与"当前值型"字段**，这决定了 reducer 怎么写：
   - 日志型（审计、专家意见）→ `operator.add`，只追加
   - 当前值型（草稿、结论、凭据）→ "取最新"，不能被旧值污染
3. **每轮进入审查前必须重置结论类字段**（`verdict` / `release_token` / `risk_level`），
   否则会读到上一轮的陈旧值——这是这类系统最隐蔽的 bug。
4. **并行写入的字段必须带 reducer**，否则同一 super-step 内两个节点写同一个 key 会抛 `InvalidUpdateError`。

### 3.2 `graph/state.py`

```python
from typing import Annotated, Literal, TypedDict
import operator

Verdict     = Literal["pass", "revise", "need_info", "block", "human"]
ReviewKind  = Literal["content", "operation", "result_reply", "emergency"]
RiskLevel   = Literal["low", "medium", "high"]


# ── 自定义 reducer：草稿是"当前值"语义，只保留本轮 ──────────────────
def keep_latest_turn(left: list[dict], right: list[dict]) -> list[dict]:
    """
    七路产出者并行写 drafts，修订后会重新产出同一路草稿。

    规则（三条缺一不可）：
      1. 只保留【本轮】的草稿 —— 丢掉上一轮/上一句话的残留
      2. 同一轮里【同一个 agent】的旧稿被新稿替换 —— ★ 这条是跑起来才补上的：
         修订发生在同一轮内，只按 turn_id 过滤会把新旧两份草稿都留下，
         aggregate 把它们拼在一起送审，"改过的"和"没改的"一起被审，
         必然再次命中同一条规则
      3. 同一轮里【不同 agent】的草稿都要保留 —— 多意图并行扇出靠这条

    ★ 不要用 review_round 当轮次标识：它每轮从 0 重新计数，会和历史值撞车。
    """
    if not right:
        return left
    turn = right[-1]["turn_id"]
    replaced = {(d.get("turn_id"), d.get("agent")) for d in right}
    kept = [d for d in left
            if d.get("turn_id") == turn and (d.get("turn_id"), d.get("agent")) not in replaced]
    return kept + right


# ── 日志型字段的 reducer：⚠️ 不要直接用 operator.add ──────────────
def append_unique(left: list, right: list) -> list:
    """
    LangGraph 把编译好的子图当节点用时，父图会把状态传进子图，
    子图返回的最终状态【包含它继承到的累积字段】，父图再通过 reducer 追加一次 ——
    结果是审计日志按子图调用次数成倍膨胀，一次对话能刷出上百条重复事件。

    两条出路：
      A. 给子图单独定义 schema，把 audit_log 这类累积字段排除在外（生产推荐）
      B. 用内容去重的 reducer（本实现采用，改动最小）
    代价：完全相同的两条事件会被合并；真正的审计以 review_audit 表为准。
    """
    if not right:
        return left
    seen = {json.dumps(x, sort_keys=True, ensure_ascii=False, default=str) for x in left}
    out = list(left)
    for item in right:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


class Draft(TypedDict):
    agent: str                 # k_agent / clarify / emergency_draft ...
    turn_id: str               # 属于哪一轮对话（uuid）
    revision: int              # 该轮内第几次修订稿（0 起，仅用于审计与调试）
    content: str               # 待发送文本（尚未过审）
    citations: list[dict]      # [{evidence_id, doc_id, quote, version}]
    gaps: list[str]            # 已知的信息缺口（要如实告知用户）
    route_hint: str | None     # 子图建议的路由（例如"转人工"）
    risk_tags: list[str]       # 产出侧自标的风险标签（仅供参考，不作裁决）


class ZhimeiState(TypedDict, total=False):
    # ───────── 会话与入口 ─────────
    thread_id: str
    session_id: str
    user_id: str | None
    channel: str                     # wechat / app / web
    user_input: str
    attachments: list[dict]          # [{file_id, mime, authorized}]

    # ───────── 权限与授权（由业务系统给，模型无权编造）─────────
    auth: dict                       # {verified, user_id, scopes[], data_consents[]}

    # ───────── 意图与分派 ─────────
    intents: list[str]               # 可多意图 → Send 并行
    slots: dict                      # {project, store, doctor, datetime, symptom...}
    emergency: bool
    priority: Literal["normal", "emergency"]

    # ───────── 并行扇入（必须带 reducer）─────────
    drafts:        Annotated[list[Draft], keep_latest_turn]
    panel_reviews: Annotated[list[dict], operator.add]   # 专家意见，日志型
    audit_log:     Annotated[list[dict], operator.add]   # 审计，日志型

    # ───────── 待审对象 ─────────
    review_kind: ReviewKind
    review_round: int                # 第几轮送审（0 起）
    draft: dict                      # 当前待审草稿（aggregate 产出）
    operation: dict | None           # {action, params, plan_hash} 操作类才有
    evidence: list[dict]             # 送审时附带的证据快照

    # ───────── 审查结论（每轮进入 gate 前重置）─────────
    verdict: Verdict | None
    review_feedback: list[str]       # 具体的修改意见（可执行）
    hard_rule_hits: list[dict]       # [{rule_id, rule_version, span, severity}]
    risk_level: RiskLevel | None
    escalation_used: bool            # 是否走了二次复核
    escalation_independent: bool     # 复核模型是否不同家族（诚实标注）
    revision_count: int              # 只由 feedback 节点累加
    clarify_count: int               # 只由 clarify 节点累加；★ 跨轮次保留，normalize 不重置
    origin_agent: str | None         # 修订回退目标（专业 Agent 节点名）
    release_token: str | None        # 仅 issue_token 节点可签发

    # ───────── 确认与执行 ─────────
    plan_hash: str | None
    confirmed: bool | None
    idempotency_key: str | None
    execution_result: dict | None    # 业务系统返回的真实结果
    outbound: dict | None            # 最终出站内容 {kind, text, token, expires_at}

    # ───────── 人工与收尾 ─────────
    handoff_ticket: dict | None
    blocked_rule_ids: list[str]


# ── 每轮送审前的重置工具：这一步漏了就是线上事故 ──────────────
RESET_ON_NEW_REVIEW: ZhimeiState = {
    "verdict": None,
    "release_token": None,
    "review_feedback": [],
    "hard_rule_hits": [],
    "risk_level": None,
    "escalation_used": False,
}


def begin_review_round(state: ZhimeiState, kind: ReviewKind) -> dict:
    return {
        **RESET_ON_NEW_REVIEW,
        "review_kind": kind,
        "review_round": state.get("review_round", 0) + 1,
    }
```

### 3.3 子图 State

子图**不要**重定义整个 State，只声明它自己的私有键；父图与子图通过**同名键自动共享**：

```python
# graph/sub_knowledge/state.py
class KbState(TypedDict, total=False):
    # ── 从父图继承（同名即共享）──
    user_input: str
    slots: dict
    auth: dict
    revision_count: int
    review_feedback: list[str]        # 修订意见，子图据此改稿

    # ── 子图私有 ──
    kb_scope_ok: bool
    kb_queries: list[dict]            # [{sub_task, query_text, filters}]
    kb_sufficient: bool
    kb_candidates: list[dict]         # 混合检索召回 + 精排后的候选
    kb_evidence: list[dict]           # 选定证据（会回传给父图送审）
    kb_claim_checks: list[dict]       # 逐项核对结果
    kb_verify_round: int              # 子图内部自环次数（独立于 revision_count）
    kb_exit: Literal["draft", "clarify", "handoff"]
```

**为什么子图要独立的自环计数**：`kb_verified → kb_draft` 是子图内部的事实核对回路，
和父图的"审查退回修订"是两件事。混用一个计数器会导致修订预算被内部循环吃光。

### 3.4 三种"预算"必须分开，其中只有一个跨轮次

系统里有三个计数，作用域完全不同，混用会出线上问题：

| 计数 | 作用域 | 谁累加 | 上限（建议） | 超限行为 |
|---|---|---|---|---|
| `revision_count` | **单轮内**（这句话的审查→修订循环） | `feedback` 节点 | `MAX_REVISION = 2` | `human_handoff` |
| `clarify_count` | **跨轮次**（同一会话连续追问） | `clarify` 节点 | `MAX_CLARIFY = 2` | 降级兜底 |
| `kb_verify_round` | **子图内部**（事实核对自环） | `kb_verify` 节点 | `KB_MAX_VERIFY_LOOP = 3` | `kb_limit` 缩小回答范围 |

**为什么 `clarify_count` 必须跨轮次**：追问是"系统问一句、用户下一句答"，用户的每次回复都是一次新的
`ainvoke`，而 `normalize` 会把"本轮字段"清零。如果计数也在那里清零，用户反复答不上来就会被**无限追问**——
这正是你要加兜底的场景。

所以规则是：

- `normalize` **只**重置本轮字段，**不动** `clarify_count`
- `clarify` 节点累加并写回 state（state 由 checkpointer 持久化，天然跨轮次）
- `classify` 在用户这次说清楚了（intents 不含 `clarify`）时把 `clarify_count` 复位
- `dispatch` 判上限，超限走兜底

**兜底不新增节点**，就落在 `dispatch` 的路由条件里：

```
clarify_count >= MAX_CLARIFY
  ├─ 还有任何可用槽位 → k_agent（复用"缩小回答范围"路径，给一般性说明 + 面诊建议）
  └─ 完全无法推断     → human_handoff（转人工）
```

**对话示例（MAX_CLARIFY = 2）**：

| 轮次 | 用户 | 系统行为 | `clarify_count` |
|---|---|---|---|
| 1 | "这个多少钱？" | 追问是哪个项目（给项目名选项，别问开放式问题） | 1 |
| 2 | "就是那个呀" | 再追问一次，这次换成更小粒度的二选一 | 2 |
| 3 | "反正就是你们做的那个" | **不再追问**：无可用槽位 → 转人工 | 2 |

如果第 3 轮用户说的是"热玛吉那个"，`classify` 会识别出 `knowledge_edu`，
`clarify_count` 归零，正常走科普路径——**预算是"连续追问"的预算，不是"总共只能问两次"**。

---

## 4. 主图：节点、边、路由函数

### 4.1 建图

```python
# graph/build.py
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, Command
from .state import ZhimeiState
from . import routers as R
from .nodes import intake, specialists, aggregate as agg, release, revision, exits

def build_main_graph(deps, knowledge_graph, risk_graph) -> StateGraph:
    b = StateGraph(ZhimeiState)

    # ── 入口与调度层 ──
    b.add_node("normalize",        intake.normalize)          # 纯规则：会话/身份/输入清洗
    b.add_node("emergency_screen", intake.emergency_screen)   # 规则优先 + 模型兜底
    b.add_node("classify",         intake.classify)           # 意图 + 槽位（结构化输出）
    b.add_node("dispatch",         intake.dispatch)           # 纯规则：返回 Send 列表

    # ── 草稿产出层（七路并行）──
    b.add_node("k_agent",         knowledge_graph)            # 子图 A
    b.add_node("r_agent",         specialists.recommend)
    b.add_node("c_agent",         specialists.clinic_info)
    b.add_node("b_agent",         specialists.booking)
    b.add_node("p_agent",         specialists.postcare)
    b.add_node("clarify",         specialists.clarify)        # 澄清问题（也要过审）
    b.add_node("emergency_draft", specialists.emergency_draft)# 固定模板 + 接管请求

    # ── 汇聚与守门 ──
    b.add_node("aggregate",  agg.aggregate)
    b.add_node("risk_gate",  risk_graph)                      # 子图 B（第一次调用）

    # ── 输出与执行 ──
    b.add_node("output_type",   release.output_type)
    b.add_node("await_confirm", release.await_confirm)        # interrupt 挂起点
    b.add_node("execute_op",    release.execute_op)
    b.add_node("receipt",       release.receipt)
    b.add_node("set_result_kind", release.set_result_kind)    # review_kind = result_reply
    b.add_node("recheck",       risk_graph)                   # 子图 B（第二次调用，同一份图）
    b.add_node("final_check",   release.final_check)

    # ── 修订回路 ──
    b.add_node("feedback",    revision.feedback)
    b.add_node("retry_check", revision.retry_check)
    b.add_node("revise",      revision.revise)                # 返回 Command(goto=origin_agent)
    b.add_node("plan_void",   revision.plan_void)

    # ── 出口层 ──
    b.add_node("send",          exits.send)                   # 强制校验 release_token
    b.add_node("human_handoff", exits.human_handoff)
    b.add_node("audit_block",   exits.audit_block)

    # ══════════ 边 ══════════
    b.add_edge(START, "normalize")
    b.add_edge("normalize", "emergency_screen")

    b.add_conditional_edges("emergency_screen", R.after_emergency_screen, {
        "hit":  "emergency_draft",
        "miss": "classify",
    })

    b.add_edge("classify", "dispatch")

    # 分派：正常返回一个节点名；多意图返回 list[Send]（map-reduce 扇出）
    b.add_conditional_edges("dispatch", R.after_dispatch, [
        "k_agent", "r_agent", "c_agent", "b_agent", "p_agent", "clarify",
    ])

    # 七路扇入：唯一汇聚点
    for n in ("k_agent", "r_agent", "c_agent", "b_agent", "p_agent",
              "clarify", "emergency_draft"):
        b.add_edge(n, "aggregate")

    b.add_edge("aggregate", "risk_gate")

    # 五档出口：子图内部产出 verdict，父图据此分派
    b.add_conditional_edges("risk_gate", R.after_risk_gate, {
        "pass":      "output_type",
        "revise":    "feedback",
        "need_info": "send",          # 补充要求也是出站内容，同样要凭据
        "block":     "audit_block",
        "human":     "human_handoff",
    })

    # 修订回路
    b.add_edge("feedback", "retry_check")
    b.add_conditional_edges("retry_check", R.after_retry_check, {
        "retry":     "revise",
        "exhausted": "human_handoff",
    })
    # revise 自己返回 Command(goto=state["origin_agent"])，不需要静态出边

    # 输出与执行闭环
    b.add_conditional_edges("output_type", R.after_output_type, {
        "reply":     "send",
        "operation": "await_confirm",
    })
    b.add_conditional_edges("await_confirm", R.after_confirm, {
        "confirmed": "execute_op",
        "denied":    "plan_void",
        "mismatch":  "plan_void",     # 方案哈希对不上 → 凭据作废，回炉重审
    })
    b.add_edge("execute_op", "receipt")
    b.add_edge("receipt", "set_result_kind")
    b.add_edge("set_result_kind", "recheck")
    b.add_edge("recheck", "final_check")
    b.add_conditional_edges("final_check", R.after_final_check, {
        "ok":   "send",
        "fail": "human_handoff",
    })

    # 作废方案后仍要送审（保证"任何出站都有凭据"这条不破例）
    b.add_edge("plan_void", "aggregate")

    b.add_edge("send", END)
    b.add_edge("human_handoff", END)
    b.add_edge("audit_block", END)
    return b
```

### 4.2 编译与运行配置

```python
# graph/compile.py
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

def compile_graph(builder, checkpointer: AsyncPostgresSaver):
    return builder.compile(
        checkpointer=checkpointer,   # interrupt / 恢复 / 审计都靠它
        # store=...                  # 长跨会话记忆用你自己的 ProfileService 更合适
    )

# 调用方
CONFIG = {
    "configurable": {"thread_id": session_id},   # 一个会话一个 thread
    "recursion_limit": 40,                       # 必须显式设！默认 25 会被嵌套环撞穿
}

result = await graph.ainvoke(input_state, CONFIG, durability="async")
```

> **为什么 `recursion_limit` 要显式设置**：图里有两层环（子图内部的事实核对自环 + 父图的修订环）。
> 默认 25 步很容易被正常业务路径撞到，抛 `GraphRecursionError`。设 40 并把该异常**映射成转人工**，
> 而不是让用户看到 500。

### 4.3 关键节点实现

```python
# graph/nodes/intake.py
async def normalize(state: ZhimeiState, *, deps) -> dict:
    """
    总控的第 1 个内部节点：纯规则、不调模型。
    ★ 只重置"本轮"字段。clarify_count 是跨轮次预算，绝不能在这里清零（见 §3.4）。
    """
    auth = await deps.pg.load_auth(state["session_id"])
    cleaned = deps.text.clean(state["user_input"])          # 去控制字符、限长
    return {
        "auth": auth,
        "user_input": cleaned,
        "turn_id": deps.uuid(),                             # 本轮标识，drafts reducer 依赖它
        # ── 每轮重置 ──
        "review_round": 0,
        "revision_count": 0,
        "review_feedback": [],
        "confirmed": None,
        "confirm_result": None,
        "execution_result": None,
        "handoff_ticket": None,
        "outbound": None,
        # ── 刻意不重置：clarify_count ──
        "audit_log": [{"at": deps.now(), "event": "normalize",
                       "user_id": auth.get("user_id"),
                       "clarify_count": state.get("clarify_count", 0)}],
    }


async def emergency_screen(state: ZhimeiState, *, deps) -> dict:
    """规则优先的紧急信号预筛：宁可误报不可漏报。"""
    hits = deps.rules.match_emergency(state["user_input"])   # 词表带版本号
    if hits:
        return {"emergency": True, "priority": "emergency",
                "audit_log": [{"event": "emergency_hit", "rules": hits}]}
    # 规则没命中时用模型兜底（只判"要不要升级"，不做医学判断）
    verdict = await deps.llm.structured("emergency_triage", EmergencyTriage, user=state["user_input"])
    return {"emergency": verdict.escalate, "priority": "emergency" if verdict.escalate else "normal"}


async def classify(state: ZhimeiState, *, deps) -> dict:
    out = await deps.llm.structured(
        "classify", ClassifyOut,
        system=prompts.classify.SYSTEM,
        user=prompts.classify.render(state["user_input"], state["slots"]),
    )
    # ★ 用户这次能说清楚了 → 澄清预算复原；仍不明确则保留计数，交给 dispatch 判上限
    reset = {} if "clarify" in out.intents else {"clarify_count": 0}
    return {"intents": out.intents,
            "slots": {**state.get("slots", {}), **out.slots},
            **reset}


MAX_CLARIFY = settings.MAX_CLARIFY          # 建议 2，见 §3.4


def dispatch(state: ZhimeiState) -> list[Send] | str:
    """
    总控的第 2 个内部节点：纯规则路由，不调模型。
    多意图 → 并行扇出；意图不明确 → 澄清；追问超预算 → 降级兜底。
    """
    targets = [ROUTE[i] for i in state["intents"] if i in ROUTE]
    targets = list(dict.fromkeys(targets))                    # 去重且保序
    if state.get("emergency"):
        return "emergency_draft"                              # 紧急优先，不跑常规 Agent

    if not targets:                                           # 意图不明确
        if state.get("clarify_count", 0) >= MAX_CLARIFY:
            # ★ 连续追问不收敛 → 不再追问，降级兜底（见 §3.4）
            return "k_agent" if has_any_slot(state["slots"]) else "human_handoff"
        return "clarify"
    return [Send(t, {"slots": state["slots"], "auth": state["auth"],
                     "turn_id": state["turn_id"]}) for t in targets]


def has_any_slot(slots: dict) -> bool:
    """只要还有任何一个可用槽位，就还能给一般性说明，而不是硬转人工。"""
    return any(v for v in (slots or {}).values() if v)


async def clarify(state: ZhimeiState, *, deps) -> dict:
    """生成澄清问题。★ 计数只在代码里累加，且必须写回 state 才能跨轮次生效。"""
    out = await deps.llm.structured(
        "clarify", ClarifyOut,
        system=prompts.clarify.SYSTEM,
        user=prompts.clarify.render(state["user_input"], state["slots"]),
    )
    n = state.get("clarify_count", 0) + 1
    return {
        "clarify_count": n,
        "drafts": [{"agent": "clarify", "turn_id": state["turn_id"], "revision": 0,
                    "content": out.content, "citations": [], "gaps": out.gaps,
                    "route_hint": None, "risk_tags": []}],
        "audit_log": [{"event": "clarify_asked", "count": n}],
    }
```

```python
# graph/nodes/aggregate.py
async def aggregate(state: ZhimeiState, *, deps) -> dict:
    """七路扇入后的唯一汇聚点：把草稿整理成"一个待审对象"。"""
    drafts = state["drafts"]                                  # reducer 已保证只留最新轮
    if not drafts:
        return {"draft": {"content": "", "citations": [], "gaps": ["未能产出草稿"]}}

    # 多意图命中时按固定顺序拼接，并合并引用与缺口
    ordered = sorted(drafts, key=lambda d: deps.order.index(d["agent"]))
    content = "\n\n".join(d["content"] for d in ordered)

    operation = None
    if any(d["agent"] == "b_agent" for d in ordered):
        op = drafts[0].get("operation")                        # 由 b_agent 写入
        plan_hash = deps.security.hash_plan(op)                # 内容 + 参数一起哈希
        operation = {**op, "plan_hash": plan_hash}

    return {
        "draft": {
            "content": content,
            "citations": [c for d in ordered for c in d["citations"]],
            "gaps": [g for d in ordered for g in d["gaps"]],
        },
        "operation": operation,
        "plan_hash": operation["plan_hash"] if operation else None,
        "review_kind": "operation" if operation else "content",
        **begin_review_round(state, "operation" if operation else "content"),
    }
```

> ### ⚠️ 操作类方案里必须包含「改哪一条 + 该记录的版本」，否则改约根本执行不了
>
> `params` 里除了模型能决定的字段（目标门店、目标时段），还必须有**两个只有系统才知道**的字段：
>
> | 字段 | 谁提供 | 漏掉会怎样 |
> |---|---|---|
> | `appointment_id` | `b_agent` 调 `pg.find_upcoming_appointment(user_id)` 查到 | SQL 退化成 `WHERE appointment_id = NULL`，**永远不匹配** |
> | `expected_version` | 同上，取该行当前的 `version` | 乐观锁没有比对基准，只能写死（曾经写死成 `1`），于是这条记录**一生只能改一次** |
>
> 这几处叠在一起时，报错信息是「**状态已变化，请刷新后重试**」—— 而数据库里那一行明明是
> `status=booked, version=1`，从来没变过。**一个误导性的报错比一个直接的报错贵得多**：
> 它会把排查方向完全带偏到"并发冲突"上，而真正的原因是"参数压根没传下来"。
>
> 分工原则：**模型只决定「做什么动作 + 目标值」，标识符一律由系统注入。**
> 模型不可能知道"该改哪一条预约记录、这条记录的版本号是多少"，让它编只会编错。
> 顺带一个好处：这两个值进了 `params` 就会一起进 `plan_hash`，
> 于是"用户确认之后偷偷换成另一条预约去改"会让放行凭据直接失效。
>
> 还有一个必须有的**诚实降级**：查不到可改约的预约时，`operation` 要置空并如实告诉用户
> "没查到您的预约记录，请提供预约编号"，而不是给一个"看起来能执行"的方案 ——
> 否则用户确认之后必然在执行阶段失败，而失败信息还是并发冲突，用户和坐席都无从下手。

```python
# graph/nodes/release.py
async def await_confirm(state: ZhimeiState, *, deps) -> dict:
    """
    挂起等待用户确认。
    注意：interrupt 之前的代码在恢复时会被【重放】——
    所以这里绝不能有副作用（不发消息、不写业务库、不扣费）。
    出边分派放在父图的 add_conditional_edges 里，保持"菱形 = 路由表"的统一风格。
    """
    from langgraph.types import interrupt

    answer = interrupt({                                      # ← 图在这里挂起并落 checkpoint
        "type": "confirm_operation",
        "plan": state["draft"]["content"],
        "plan_hash": state["plan_hash"],
        "expires_at": deps.now_plus(minutes=30),
    })

    # 恢复后先校验"用户确认的就是被审过的那一版方案"，再放行
    if answer.get("plan_hash") != state["plan_hash"]:
        return {"confirmed": False, "confirm_result": "mismatch"}
    if not answer.get("confirmed"):
        return {"confirmed": False, "confirm_result": "denied"}
    return {"confirmed": True, "confirm_result": "confirmed"}


def after_confirm(state: ZhimeiState) -> str:
    """confirmed / denied / mismatch 三分支，后两者都作废方案。"""
    return state.get("confirm_result", "denied")


async def execute_op(state: ZhimeiState, *, deps) -> dict:
    """真实副作用：幂等 + 只认"已审 + 已确认"。"""
    token = state.get("release_token")
    if not deps.security.verify_token(token, state["draft"], state["plan_hash"]):
        raise PermissionError("执行被拒绝：凭据无效或已过期")

    key = f'{state["thread_id"]}:{state["plan_hash"]}'
    async with deps.pg.tx():                                  # 唯一约束兜底防重
        if await deps.pg.op_already_done(key):
            return {"execution_result": await deps.pg.load_op_result(key)}   # 幂等返回
        result = await deps.clinic_api.change_appointment(
            **state["operation"]["params"], request_id=key)
        await deps.pg.save_op_result(key, result)
    return {"execution_result": result, "idempotency_key": key}


async def send(state: ZhimeiState, *, deps) -> dict:
    """唯一出站口：没凭据就发不出去。"""
    text, token = state["draft"]["content"], state.get("release_token")
    if not deps.security.verify_token(token, state["draft"]):
        raise PermissionError("出站被拒绝：内容与凭据不匹配")
    await deps.gateway.push(state["session_id"], text)         # 出站网关二次校验
    return {"outbound": {"kind": "reply", "text": text, "token": token}}
```

```python
# graph/nodes/revision.py
async def feedback(state: ZhimeiState, *, deps) -> dict:
    """记录修改意见并【在代码里】累加计数——绝不让模型数数。"""
    return {
        "review_feedback": state["review_feedback"],
        "revision_count": state["revision_count"] + 1,
        "audit_log": [{"event": "revision_feedback",
                       "round": state["review_round"],
                       "feedback": state["review_feedback"]}],
    }


def retry_check(state: ZhimeiState) -> str:
    return "retry" if state["revision_count"] <= settings.MAX_REVISION else "exhausted"


def revise(state: ZhimeiState, *, deps) -> Command:
    """
    图上那条"回到草稿产出层"的箭头，在代码里就是这一行：
    定向回退到产出这份草稿的那个 Agent，并把意见带给它。
    """
    origin = state["origin_agent"] or "k_agent"
    return Command(
        goto=origin,
        update={
            "review_feedback": state["review_feedback"],
            "audit_log": [{"event": "revise_dispatch", "to": origin}],
        },
    )
```

---

## 5. 子图 A：知识科普

### 5.1 建图

```python
# graph/sub_knowledge/build.py
from langgraph.graph import StateGraph, START, END
from .state import KbState
from . import retrieval as ret

def build_knowledge_graph(deps):
    b = StateGraph(KbState)

    b.add_node("kb_intake",     intake)
    b.add_node("kb_context",    load_context)
    b.add_node("kb_decompose",  decompose)
    b.add_node("kb_clarify",    make_clarify)
    b.add_node("kb_retrieve",   ret.retrieve)      # Milvus 混合检索
    b.add_node("kb_evidence",   pick_evidence)     # 充分性 + 冲突检查
    b.add_node("kb_limit",      limit_scope)
    b.add_node("kb_draft",      draft)
    b.add_node("kb_verify",     verify)
    b.add_node("kb_return",     return_draft)

    # 入口分流：首次进入走完整流水线；被 revise 召回时【重新生成】
    b.add_conditional_edges(START, entry_router, {
        "fresh":    "kb_intake",
        "revision": "kb_revise_in",
    })
    b.add_edge("kb_revise_in", "kb_draft")     # ★ 不是 kb_verify，见下方说明

    b.add_conditional_edges("kb_intake", after_scope, {   # 菱形：边界判断
        "in_scope": "kb_context",
        "out":      "kb_return",                          # 越界 → 交回总控转交
    })
    b.add_edge("kb_context", "kb_decompose")
    b.add_conditional_edges("kb_decompose", after_sufficiency, {
        "ok":         "kb_retrieve",
        "need_info":  "kb_clarify",
    })
    b.add_edge("kb_clarify", END)        # 澄清问题回父图 aggregate 送审
    b.add_edge("kb_retrieve", "kb_evidence")
    b.add_conditional_edges("kb_evidence", after_evidence, {
        "enough": "kb_draft",
        "thin":   "kb_limit",
    })
    b.add_edge("kb_limit", "kb_draft")
    b.add_edge("kb_draft", "kb_verify")
    b.add_conditional_edges("kb_verify", after_verify, {
        "grounded":  "kb_return",
        "ungrounded": "kb_draft",        # ← 自环：缺依据就回炉重生成
        "loop_out":  "kb_limit",         # ← 自环次数超限：降级为"缩小范围"
    })
    b.add_edge("kb_return", END)
    return b
```

对应的路由函数：

```python
def entry_router(state: KbState) -> str:
    """父图 revise 召回时 revision_count > 0，走修订再入路径。"""
    return "revision" if state.get("revision_count", 0) > 0 else "fresh"


async def kb_revise_in(state: KbState) -> dict:
    """修订再入：给这一轮重开一次「生成↔核对」的内部预算。"""
    return {"kb_verify_round": 0}
```

> ⚠️ **这一处踩过坑，务必按修正后的写法实现**：最初把修订直接接到 `kb_verify`，
> 理由是"复用已通过的检索与证据"。但实际跑下来是——**只核对、不重新生成**，
> 内容一个字没变，审查必然再次命中同一条规则，把 2 次修订预算白白耗尽，最后转人工。
>
> 正确做法：`revise → kb_revise_in → kb_draft`（带 `review_feedback` 重新生成）
> `→ kb_verify → kb_return`。**复用证据这一点并没有丢** —— `kb_draft` 读的就是
> 已有的 `state["kb_evidence"]`，重生成不等于重检索。


def after_verify(state: KbState) -> str:
    """子图内部自环的收口：允许回炉，但必须有上限。"""
    return decide_verify_exit(claims=state.get("kb_claim_checks", []),
                              round_no=state.get("kb_verify_round", 0),
                              max_loop=settings.KB_MAX_VERIFY_LOOP)


def decide_verify_exit(*, claims, round_no, max_loop, soft_rounds=1) -> str:
    """抽成模块级【纯函数】是刻意的：闭包没法单测，
    而"没人测得到的反直觉规则"正是最容易被后人改回去的东西。"""
    if not claims:
        # 核对没产出任何判定（调用失败 / 模型没给）→ 绝不当作通过
        # ★ 原来的写法是 `kb_enough = not unsupported`，空 claims 时
        #   `not []` 为真 → 被当成"核对通过" → 【什么都没核对】的草稿直接放行。
        return "loop_out" if round_no >= max_loop else "ungrounded"

    bad = [c for c in claims if c["status"] != "supported"]
    if not bad:
        return "grounded"

    if all(c["status"] == "overstated" for c in bad):     # 软问题：有依据，只是说得太满
        return "ungrounded" if round_no <= soft_rounds else "grounded"

    return "loop_out" if round_no >= max_loop else "ungrounded"   # 硬问题：找不到依据
```

> ⚠️ **这一处也踩过坑，而且是个很反直觉的坑 —— 预算不能一律"超限就降级"。**
>
> 最初写成"只要还有任何非 `supported` 判定就回炉，回炉到上限就 `loop_out`"，
> 真实模型跑出来是这样：
>
> | 轮次 | 发生的事 |
> |---|---|
> | 第 1 轮 | 判定 1 句"表述过头" → 重新生成 |
> | 第 2 轮 | 模型为了不被挑错，**删掉了有争议的内容** → 又被挑出别的问题 |
> | 第 3 轮 | 继续删 → 预算耗尽 → `loop_out` → **草稿被整个丢弃** |
> | 结果 | 换成 `kb_limit` 那句"现有资料中没有可引用的说明" |
>
> 也就是说：三轮修订的净效果是**把一份基本合格的答案越改越空，最后扔掉**，
> 比第一版还差。**预算本意是保护质量，结果成了质量杀手。**
>
> 所以收口要按**问题性质**分档，而不是"有没有问题"：
>
> | 判定 | 含义 | 处置 |
> |---|---|---|
> | `unsupported` | 找不到依据 / 引错证据 / 凭常识补充 | **硬问题**：必须回炉；预算耗尽仍不达标才降级（编造内容绝不能出门） |
> | `overstated` | **有依据**，只是说得太满/绝对化 | **软问题**：最多修 1 轮，之后放行草稿 |
>
> 放行软问题的依据是：绝对化用语在父图 `risk_gate` 和规则层还有一道硬检查兜底，
> 因此"交付一份偏满但有依据的答案"明显优于"交付一份什么都没说的答案"。
>
> 配套地，`kb_verify` 的 Prompt 必须明确：**"写得不够详细 / 没提到某方面"不是
> `unsupported`**（那是缺内容，不是编内容，写进 `must_fix` 即可），否则校验模型会把
> "不够完美"当成"没有依据"，同样能把预算耗光。但**不能**为了少回炉就放过真正
> 无依据的内容 —— 给没有依据的内容放行，比多改一版稿子严重得多。

### 5.2 检索实现（Milvus 混合 + 精排）

```python
# graph/sub_knowledge/retrieval.py
async def retrieve(state: KbState, *, deps) -> dict:
    """
    三个要点：
    1) dense + sparse 双路召回，RRF 融合（BGE-M3 天然双输出）
    2) 检索过滤必须带"审核通过 / 未过期 / 版本正确"——这是合规底线
    3) 精排只对融合后的 top-N 做，别对全量做
    """
    queries = state["kb_queries"]                      # kb_decompose 拆出的子任务
    texts = [q["query_text"] for q in queries]
    vecs = await deps.encoder.encode(texts)            # 进程内，to_thread 包裹

    async def one(q, dense, sparse):
        flt = ('doc_status == "approved" and is_deleted == false '
               f'and project in {q["projects"]} and expire_at > now()')
        hits = await deps.milvus.hybrid_search(
            collection="kb_chunks",
            dense_vec=dense, sparse_vec=sparse,
            filter_expr=flt, top_k=settings.RECALL_K,      # 例如 40
            ranker="RRF",                                   # 融合两路
        )
        return {"sub_task": q["sub_task"], "hits": hits}

    fused = await asyncio.gather(*(one(q, d, s) for q, d, s in
                                   zip(queries, vecs["dense"], vecs["sparse"])))

    # 精排：用原始问题而不是改写后的 query，避免改写引入偏差
    flat = [(f["sub_task"], h) for f in fused for h in f["hits"]]
    scores = await deps.reranker.score(state["user_input"], [h["text"] for _, h in flat])
    ranked = sorted(zip(flat, scores), key=lambda x: -x[1])[: settings.RERANK_TOP_K]

    return {"kb_candidates": [
        {"evidence_id": f"E{i+1}", "doc_id": h["doc_id"], "version": h["version"],
         "text": h["text"], "score": s, "sub_task": st}
        for i, ((st, h), s) in enumerate(ranked)
    ]}
```

```python
async def pick_evidence(state: KbState, *, deps) -> dict:
    """证据筛选：排除过期/冲突/来源不明；高影响或冲突 → 升级复核。"""
    cands = [c for c in state["kb_candidates"] if c["score"] >= settings.MIN_RERANK]
    if not cands:
        return {"kb_evidence": [], "kb_evidence_enough": False}

    has_conflict = deps.rules.detect_conflict(cands)
    if has_conflict or max(c["score"] for c in cands) < settings.HIGH_IMPACT_SCORE:
        second = await deps.llm.structured("evidence_second_opinion", EvidenceOpinion,
                                           system=prompts.kb.EVIDENCE_SYSTEM,
                                           user=prompts.kb.render_evidence(cands))
        if second.verdict == "insufficient":
            return {"kb_evidence": cands, "kb_evidence_enough": False}

    return {"kb_evidence": cands[: settings.MAX_EVIDENCE], "kb_evidence_enough": True}
```

### 5.3 自环与修订再入的区别（务必分清）

| 机制 | 触发 | 回到哪 | 计数 | 预算 |
|---|---|---|---|---|
| 子图内部自环 | `kb_verify` 发现结论缺依据 | `kb_draft` | `kb_verify_round` | 硬问题 3 次，超限降级为 `kb_limit`；软问题（`overstated`）最多 1 次后放行 |
| 父图修订再入 | 风险审查判 `revise` | `kb_verify`（不是 `kb_draft`） | `revision_count` | 2 次，超限转人工 |

**为什么修订回到 `kb_verify` 而不是 `kb_draft`**：审查意见针对具体表述与引用，
回到事实核对可以复用已经通过的检索与证据，同时强制修订后每一步重新核对，避免"改一句、错一句"。

---

## 6. 子图 B：风险审查

### 6.1 建图

```python
# graph/sub_risk/build.py
def build_risk_graph(deps):
    b = StateGraph(RiskState)

    b.add_node("gate_in",        standardize)        # 审查请求标准化
    b.add_node("review_input",   check_input)        # 完整性与权限校验
    b.add_node("need_info",      need_info)          # 要求补充 / 阻止执行
    b.add_node("emergency_check", emergency_recheck) # 复查【原始消息】
    b.add_node("hard_rules",     rules.check)        # 确定性硬规则
    b.add_node("block",          block)              # 阻止并记录规则编号
    b.add_node("review_medical", panel.medical)      # ┐
    b.add_node("review_ad",      panel.ad)           # ├ 三个并行专家
    b.add_node("review_privacy", panel.privacy)      # ┘
    b.add_node("escalate_review", escalate)          # 二次复核（不同家族模型）
    b.add_node("biz_check",      biz_check)          # 敏感操作业务校验
    b.add_node("merge_verdict",  decision.merge)     # 规则 + 专家 + 业务 → 等级
    b.add_node("panel_fanin",    pass_through)       # 并行专家的扇入屏障
    b.add_node("check_op",       pass_through)       # 路由挂载点
    b.add_node("feedback",       revision_feedback)
    b.add_node("check_retry",    retry_check)
    b.add_node("resubmit",       resubmit)           # 修订后重新进 gate_in
    b.add_node("issue_token",    issue_token)        # 签发放行凭据 + 审计
    b.add_node("handoff",        handoff)            # 创建人工接管

    b.add_edge(START, "gate_in")
    b.add_edge("gate_in", "review_input")
    b.add_conditional_edges("review_input", after_input_check, {
        "ok":        "emergency_check",
        "need_info": "need_info",
    })
    b.add_conditional_edges("emergency_check", after_emergency, {
        "emergency": "issue_token",   # 放行【预审模板】的风险提示
        "normal":    "hard_rules",
    })
    b.add_edge("emergency_check", "handoff")   # 同一节点两条出边 → 下一超步并行执行
    b.add_conditional_edges("hard_rules", after_hard_rules, {
        "blocked": "block",
        "clean":   ["review_medical", "review_ad", "review_privacy"],  # 并行扇出
    })

    # ★ 扇入必须是真实节点：router 只能挂在节点上，不能挂在"空气"上
    for n in ("review_medical", "review_ad", "review_privacy"):
        b.add_edge(n, "panel_fanin")
    b.add_conditional_edges("panel_fanin", should_escalate, {
        "escalate": "escalate_review",
        "skip":     "check_op",
    })
    b.add_edge("escalate_review", "check_op")
    b.add_conditional_edges("check_op", is_operation, {
        "yes": "biz_check",
        "no":  "merge_verdict",
    })
    b.add_edge("biz_check", "merge_verdict")

    # merge_verdict 先算出 verdict，再据此分派（状态更新先于 router 生效）
    b.add_conditional_edges("merge_verdict", read_verdict, {
        "pass":   "issue_token",
        "revise": "feedback",
        "block":  "block",
        "human":  "handoff",
    })
    b.add_edge("feedback", "check_retry")
    b.add_conditional_edges("check_retry", after_retry, {
        "retry": "resubmit",
        "over":  "handoff",
    })
    b.add_edge("resubmit", "gate_in")     # ← 复审必须重走完整流程，不是只重跑专家
    for t in ("need_info", "block", "issue_token", "handoff"):
        b.add_edge(t, END)
    return b


def pass_through(state) -> dict:
    """只做扇入屏障或路由挂载点，自身不改状态。"""
    return {}


# ── 路由约定（全图统一，不要混用）──────────────────────────────
# 图上每个"菱形" = add_conditional_edges + 一个 router 函数；
# 只有【目标要运行时才能确定】时才用 Command(goto=...)，
# 本项目仅 revise 一个节点属于这种情况。
# 另外：router 只能挂在真实节点上，扇入点（如 panel_fanin）必须 add_node。
```

### 6.2 并行专家与"独立性"的实现

```python
# graph/sub_risk/panel.py
async def medical(state: RiskState, *, deps) -> dict:
    """医疗边界专家：只标"越界/不越界"，不做诊断。"""
    out = await deps.llm.structured(
        "review_medical", PanelOpinion,
        system=prompts.review.MEDICAL_SYSTEM,
        user=prompts.review.render(state["draft"], state["evidence"]),
    )
    return {"panel_reviews": [{"role": "medical", "round": state["review_round"], **out.model_dump()}]}

# review_ad / review_privacy 结构相同，只是 system prompt 与关注点不同

async def should_escalate(state: RiskState) -> str:
    """
    升级条件必须是【可解释的判据】，不是模型自由心证：
      1. 命中高风险标签清单（诊断/用药/并发症/退费争议/未成年人/孕产期...）
      2. 三位专家对同一句判定不一致 → 意见冲突
      3. 模型自报 confidence 低于阈值
    """
    tags = {t for r in state["panel_reviews"] for t in r["risk_tags"]}
    if tags & settings.HIGH_RISK_TAGS:                      return "escalate"
    if decision.has_conflict(state["panel_reviews"]):        return "escalate"
    if min(r["confidence"] for r in state["panel_reviews"]) < settings.CONF_MIN:
        return "escalate"
    return "skip"


async def escalate(state: RiskState, *, deps) -> dict:
    """
    二次复核。技术栈只有一个 API 模型时，这里有两个选择：
      - ESCALATION_MODEL 配了别家模型 → 真独立复核
      - 没配 → 同族复核，必须在审计里标注，不能假装独立
    """
    independent = deps.llm.family_of("escalation") != deps.llm.family_of("review_medical")
    out = await deps.llm.structured(
        "escalation", PanelOpinion,
        system=prompts.review.ESCALATION_SYSTEM,
        user=prompts.review.render_escalation(state),
    )
    if not independent:
        await deps.pg.note_same_family_review(state["thread_id"])
    return {
        "panel_reviews": [{"role": "escalation", "independent": independent, **out.model_dump()}],
        "escalation_used": True,
        "escalation_independent": independent,
    }
```

### 6.3 决策表与凭据

```python
# graph/sub_risk/decision.py
def merge(state: RiskState) -> dict:
    """
    风险等级 = 取最高档，顺序不能调换：
      硬规则阻断 > 紧急信号 > 复核判定 > 首次判定
    模型不参与最终裁决。
    """
    level = "low"
    if any(r["level"] == "high" for r in state["panel_reviews"]):   level = "high"
    if state.get("hard_rule_hits"):                                 level = "high"
    if state.get("emergency"):                                      level = "high"
    return {"risk_level": level, "verdict": DECISION_TABLE[level]}


DECISION_TABLE = {
    "high":   "human",    # 高风险 → 值班医师
    "medium": "revise",   # 可改写 → 退回修订
    "low":    "pass",
}


async def issue_token(state: RiskState, *, deps) -> dict:
    """放行凭据：绑定【内容 + 操作参数 + 审查类别 + 有效期】。"""
    token = deps.security.sign_release(
        content_hash=deps.security.hash_text(state["draft"]["content"]),
        plan_hash=state.get("plan_hash"),
        review_kind=state["review_kind"],
        ttl_seconds=settings.TOKEN_TTL,
    )
    await deps.pg.write_audit(                                     # 审计必须落库
        thread_id=state["thread_id"], review_round=state["review_round"],
        verdict="pass", risk_level=state["risk_level"],
        hard_rule_hits=state["hard_rule_hits"], panel=state["panel_reviews"],
        escalation_independent=state.get("escalation_independent"),
    )
    return {"verdict": "pass", "release_token": token}
```

---

## 7. 关键 Prompt 设计

### 7.1 通用约定（写进每个 system prompt 的头部）

```python
# prompts/system.py
BASE_RULES = """
【角色边界】
- 你是机构客服体系中的一个具体岗位，不是"AI 助手"。不要自我介绍，不要寒暄。
- 你不做诊断、不给用药建议、不判断病情严重程度。涉及这些一律建议面诊或转人工。

【事实纪律】
- 只能使用我提供的 <evidence> 内容作答。禁止使用你的记忆或常识补充医学事实。
- 每一句涉及原理、流程、恢复期、风险的表述，都必须绑定一个 evidence id。
- 没有依据就说不确定，并把它写进 gaps；编造比答不出来严重得多。

【表达纪律】
- 不承诺疗效（保证/根治/一次见效/永久）、不使用绝对化用语（最好/最安全/全网最低）。
- 价格、资质、档期只能引用业务系统返回值，不得估算。
- 必须保留限制条件与"具体以医生面诊为准"类提示。

【输出纪律】
- 只输出 JSON，不要输出任何解释性文字或 markdown 代码块标记。
- 不确定时使用 "uncertain": true，不要猜一个看起来合理的值。
"""
```

### 7.2 意图识别与槽位抽取（`classify`）

```python
CLASSIFY_SYSTEM = BASE_RULES + """
【任务】把用户这一句话解析为意图 + 槽位 + 是否需要紧急升级。

【意图取值】knowledge_edu | recommend | clinic_info | booking | postcare |
            clarify（无法判断）| other
- 一句话可含多个意图，全部列出。
- 拿不准时选 clarify，不要硬猜。

【槽位】project / store / doctor / datetime / symptom / postop_days / budget
- 只抽取用户明确说出的内容；没说就留空，禁止推断。
- datetime 相对时间要转成 ISO 时间（例如"下周三下午"→ 具体日期，并在 note 里写推断依据）。
"""

CLASSIFY_USER = """
用户消息：{user_input}
已知会话槽位（可沿用，可覆盖）：{slots_json}

输出 JSON：
{
  "intents": ["..."],
  "slots": {"project": null, "store": null, "doctor": null,
            "datetime": null, "symptom": null, "postop_days": null, "budget": null},
  "time_note": "相对时间的推断依据，没有则 null",
  "uncertain": false
}
"""
```

### 7.3 澄清问题生成（`clarify`）

```python
CLARIFY_SYSTEM = BASE_RULES + """
【任务】就缺失的关键信息提问，让用户能用一句话答完。

【硬性要求】
- 最多 2–3 个问题，必须是封闭式（给选项），不要让用户写作文。
- 只问"会影响回答方向"的信息；一般性科普问题不要追问。
- 不得包含任何带有答案倾向、诊断性质或疗效暗示的表述。
- 结尾必须带"具体以医生面诊评估为准"。
"""

CLARIFY_USER = """
原始问题：{user_input}
已抽取槽位：{slots_json}
判定缺失的关键信息：{missing}

输出 JSON：
{
  "content": "面向用户的澄清文本（含 2–3 个封闭式问题与面诊提示）",
  "why": ["每条追问对应影响哪部分答案"],
  "gaps": ["仍未知的信息"]
}
"""
```

**示例产出**（`kb_sufficiency` 判定"我适合做哪个"缺条件是）：

> 为了给您更贴切的说明，想先确认三点：① 您主要想改善的是面部紧致、下颌轮廓，还是其他部位？
> ② 恢复期您更希望「当天可正常上班」还是「可接受 3–7 天轻微红肿」？③ 以前做过同类项目吗（热玛吉 / 超声炮 / 线雕）？
> 这三点会影响适合的方向，具体仍以医生面诊评估为准。

### 7.4 检索子任务拆分（`kb_decompose`）

```python
KB_DECOMPOSE_SYSTEM = BASE_RULES + """
【任务】把口语化问题拆成 2–5 个检索子任务，每个子任务是一次独立的向量检索。

【要求】
- 子任务之间不要语义重叠；每个都要能独立检索。
- query_text 用"知识库文档会写的措辞"，不要照抄用户口语。
- 涉及"哪个好/适合我"这类对比问题，拆成"各自原理""各自适用与局限""恢复期差异"三条，
  而不是拆成"哪个好"（知识库里没有这种句子）。
"""

KB_DECOMPOSE_USER = """
用户问题：{user_input}
已知项目：{project}

输出 JSON：
{
  "queries": [
    {"sub_task": "原理与作用层次", "query_text": "热玛吉 射频 作用层次 原理"},
    {"sub_task": "适用与局限",     "query_text": "热玛吉 适应症 禁忌症 不适用"},
    {"sub_task": "恢复期与注意事项","query_text": "热玛吉 恢复期 术后护理 注意事项"}
  ],
  "sufficient_enough": true,
  "missing": []
}
"""
```

### 7.5 科普草稿生成（`kb_draft`）

```python
KB_DRAFT_SYSTEM = BASE_RULES + """
【任务】基于给定 evidence 生成科普草稿。

【硬性要求】
- 每一段涉及事实的句子后面用 [E1] 形式标注 evidence id；没有依据的句子直接删掉。
- 明确区分"一般知识"与"个人适用性"：涉及个人情况的，一律写"需面诊评估"。
- 保留局限与不确定性（例如"效果因人而异""维持时间有个体差异"）。
- 不比较机构、不推荐具体医生、不报价格。
- 用户问的是原理类问题时，不要写"建议您到店咨询"这种推销式结尾。
"""

KB_DRAFT_USER = """
用户问题：{user_input}
上一轮审查意见（如有，必须逐条落实）：{review_feedback}

<evidence>
{evidence_block}   <!-- 形如 [E1] doc=… version=… text=… -->
</evidence>

输出 JSON：
{
  "content": "科普正文，句末带 [E1] 标注",
  "citations": [{"evidence_id": "E1", "quote": "被引用的原文片段", "doc_id": "…", "version": "…"}],
  "gaps": ["知识库中没有覆盖、因此没有回答的部分"],
  "used_evidence_ids": ["E1", "E3"]
}
"""
```

### 7.6 事实与引用核对（`kb_verify`）

```python
KB_VERIFY_SYSTEM = BASE_RULES + """
【任务】逐句核对草稿：每一句事实性表述是否被它标注的 evidence 支持。

【判定标准】
- supported：quote 原文能直接支撑该句，且没有夸大（例如把"多数人 6–12 个月"写成"能维持一年"）。
- unsupported：找不到支撑，或标注的 evidence 与句子内容无关。
- overstated：有支撑但被夸大/绝对化（承诺效果、去掉"因人而异"）。
- 你只输出判定与证据，不负责改写。
"""

KB_VERIFY_USER = """
<draft>{draft_content}</draft>
<evidence>{evidence_block}</evidence>

输出 JSON：
{
  "claims": [
    {"sentence": "…", "cited": "E1", "status": "supported|unsupported|overstated",
     "quote": "证据原文片段", "comment": "不通过时的具体原因"}
  ],
  "all_grounded": false,
  "must_fix": ["把'能维持一年'改为'多数人 6–12 个月，因人而异'"]
}
"""
```

### 7.7 三位审查专家（`review_*`）

三个 prompt 共用同一个输出契约，**视角刻意不同**：

```python
PANEL_OUTPUT = """
输出 JSON：
{
  "level": "low|medium|high",
  "risk_tags": ["diagnosis_hint", "efficacy_promise", "absolute_wording",
                "price_claim", "privacy_leak", "missing_disclaimer", "..."],
  "findings": [
    {"span": "草稿中的原文片段", "rule_hint": "对应规则或边界",
     "reason": "为什么有风险", "suggest_fix": "最小改动的具体改法"}
  ],
  "confidence": 0.0,
  "abstain": false
}
"""

REVIEW_MEDICAL_SYSTEM = BASE_RULES + """
【你的视角】医疗边界。只看：这句话是否越过了"科普"进入"医疗建议/诊断/疗效承诺"。
- 典型越界：给出个人化的治疗方案、判断症状原因、建议用药、承诺效果与维持时间。
- 你可以标记不确定（abstain），但不要为了显得负责而把所有内容都标 high。
""" + PANEL_OUTPUT

REVIEW_AD_SYSTEM = BASE_RULES + """
【你的视角】广告与宣传合规。只看：是否存在疗效承诺、绝对化用语、诱导性表述、
比较性宣传、制造焦虑、以患者形象/案例作证明。
- 你只指出违规点与最小改法，不要重写整段。
""" + PANEL_OUTPUT

REVIEW_PRIVACY_SYSTEM = BASE_RULES + """
【你的视角】隐私与服务规则。只看：是否泄露或回显个人敏感信息、是否超出用户已授权范围、
是否包含身份验证/权限相关的越权表述、是否对服务承诺做了机构无法保证的表述。
""" + PANEL_OUTPUT

ESCALATION_SYSTEM = BASE_RULES + """
【你的视角】二次独立复核。你会看到首次审查的三份意见与原始草稿。
【要求】
- 先独立判断，再与首次意见比对；不要默认首次意见正确。
- 如果三者结论不一致，必须在 conflicts 里写明分歧点与你的依据。
- 你只输出证据与建议，不输出"是否放行"的最终裁决。
""" + PANEL_OUTPUT
```

### 7.8 执行结果文案（`receipt`）

```python
RECEIPT_SYSTEM = BASE_RULES + """
【任务】把业务系统返回的真实结果转写成给用户看的回复。

【硬性要求】
- 只能使用 <result> 中的字段。时间、金额、门店、医生、状态一律照抄，禁止改写或估算。
- 失败结果必须如实说明原因，不得用"稍后再试"掩盖已经确定的失败。
- 不得出现"已为您处理好"这类超出 result 内容的承诺。
- 结尾附上下一步指引（例如查看订单入口、联系门店电话），但电话必须来自 result。
"""

RECEIPT_USER = """
用户原话：{user_input}
操作类型：{action}
<result>{execution_result_json}</result>

输出 JSON：{"content": "面向用户的执行结果回复", "citations": [], "gaps": []}
"""
```

### 7.9 修订（`revise` → 由专业 Agent 复用同一 prompt + 意见注入）

修订**不新增一套 prompt**，而是把 `review_feedback` 注入原 Agent 的 user prompt，并要求最小改动：

```python
REVISION_CLAUSE = """
【本轮为修订】
审查意见如下，必须逐条落实：
{review_feedback}

要求：
- 做【最小必要改动】，不要重写全文、不要改变已通过的事实与引用。
- 逐条说明你如何修改；确实不该改的意见要说明理由，不要默默忽略。
- 修订后仍需满足全部事实纪律与表达纪律。
"""
```

### 7.10 紧急提示：固定模板，不走 LLM

```python
# prompts/emergency_templates.py
EMERGENCY_NOTICE = (
    "您描述的情况需要我们优先处理，请先停止自行处理。\n"
    "建议尽快前往就近医院急诊或联系您的主诊医生。\n"
    "我们已安排值班医师与您联系，请保持电话畅通。\n"
    "（本条为固定提示，不构成诊断或治疗建议。）"
)
```

> **为什么不用 LLM 生成**：紧急提示必须**零延迟、零幻觉、零歧义**，而且应当是**预先通过合规审查的固定话术**。
> 让模型现场措辞，等于把最不能出错的场景交给最不稳定的环节。

---

## 8. FastAPI + SSE 接入

### 8.1 SSE 事件契约

**铁律：绝不让未经审查的正文流式出去。** 流式只承载"安全的过程状态"。

| event | data | 说明 |
|---|---|---|
| `status` | `{"stage":"classify","text":"正在理解您的问题…"}` | 固定文案，不含模型生成内容 |
| `awaiting_confirmation` | `{"plan":"…","plan_hash":"…","expires_at":"…"}` | 需要用户确认，本次流结束 |
| `final` | `{"text":"…","token":"…","kind":"reply"}` | **唯一携带已审正文的事件** |
| `handoff` | `{"ticket":"…","text":"已转人工"}` | 已创建工单 |
| `blocked` | `{"rule_ids":["AD-001"]}` | 硬性阻断 |
| `error` | `{"code":"…","message":"…"}` | 兜底 |

### 8.2 两个端点

```python
# api/chat.py
@router.post("/chat/{session_id}/stream")
async def chat_stream(session_id: str, req: ChatRequest, request: Request):
    graph = request.app.state.graph

    async def gen():
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 40}
        try:
            async for mode, chunk in graph.astream(
                {"session_id": session_id, "user_input": req.text,
                 "attachments": req.attachments, "channel": req.channel},
                config,
                stream_mode=["custom", "updates"],     # custom = 我们自己写的进度事件
            ):
                if mode == "custom":
                    yield sse("status", chunk)          # 由节点 writer 发出
                elif mode == "updates":
                    for node, patch in chunk.items():
                        ev = map_node_to_event(node, patch)   # 见下
                        if ev:
                            yield sse(ev["event"], ev["data"])
        except GraphRecursionError:
            yield sse("handoff", {"ticket": await force_handoff(session_id)})
        except Exception as e:
            yield sse("error", {"code": "internal", "message": str(e)})

    return EventSourceResponse(gen())


@router.post("/chat/{session_id}/confirm")
async def confirm(session_id: str, req: ConfirmRequest, request: Request):
    """
    用户确认是【另一个 HTTP 请求】：
    第一次流在 awaiting_confirmation 处结束，图状态挂在 checkpoint 里；
    这里用 Command(resume=...) 把图唤醒，并开一条新的流。
    """
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 40}
    resume = {"confirmed": req.confirmed, "plan_hash": req.plan_hash}

    async def gen():
        async for mode, chunk in graph.astream(Command(resume=resume), config,
                                               stream_mode=["custom", "updates"]):
            ...   # 同样的事件映射
    return EventSourceResponse(gen())


def map_node_to_event(node: str, patch: dict) -> dict | None:
    """把图的状态更新翻译成对用户可见的事件——只有出口节点能产生 final。"""
    if node == "await_confirm" and patch.get("__interrupt__"):
        return {"event": "awaiting_confirmation", "data": patch["__interrupt__"][0].value}
    if node == "send" and patch.get("outbound"):
        return {"event": "final", "data": patch["outbound"]}
    if node == "human_handoff" and patch.get("handoff_ticket"):
        return {"event": "handoff", "data": patch["handoff_ticket"]}
    if node == "audit_block":
        return {"event": "blocked", "data": {"rule_ids": patch.get("blocked_rule_ids", [])}}
    return None      # 其他节点不产生用户可见事件
```

节点内部想推"进度"时用 `get_stream_writer()`：

```python
from langgraph.config import get_stream_writer

async def kb_retrieve(state, *, deps):
    writer = get_stream_writer()
    writer({"stage": "retrieve", "text": "正在查阅审核资料…"})   # 固定文案，非模型生成
    ...
```

> **为什么不流式正文**：设计上要求"通过才发送"。想要正文流式，就必须放弃"整段先审后发"，
> 两者不可兼得。折中办法是"过程流式 + 正文整段发送"——用户体感上仍然是秒回。

### 8.3 一次完整交互的时序

```
POST /chat/{sid}/stream
  → status(classify) → status(retrieve) → status(review)
  → [ 若是操作类 ] awaiting_confirmation(plan, plan_hash)   ← 流结束，图挂起
POST /chat/{sid}/confirm  {confirmed: true, plan_hash}
  → status(execute) → status(review_result)
  → final(已审的执行结果)
```

### 8.4 实现补充（`Code/app/api/` 已实现，两处与最初的设想不同）

接口层代码在 `Code/app/api/`，验证脚本 `Code/scripts/smoke_api.py`（26 项）。
下面两条是**实现时才暴露的细节**，看图看不出来：

1. **挂起事件在 `updates` 流里是顶层键 `"__interrupt__"`，不是某个节点的 patch。**
   最初的映射代码只处理"节点名 → patch"，结果 `awaiting_confirmation` 被静默丢掉，
   前端永远等不到确认请求（图其实已经挂起了）。
   实现里必须单独判 `if node == "__interrupt__"`。

2. **子图内部节点调用 `get_stream_writer()` 不会冒泡到父图的事件流**（writer 按图作用域绑定）。
   所以"正在查阅审核资料…"这类进度**必须由父图在进入子图前发**：
   `dispatch` 在目标是 `k_agent` 时发 `retrieve`，`aggregate` 在进入 `risk_gate` 前发 `review`。
   子图内部的 `emit()` 保留，用于单独调试子图。

另外两个必须由接口层守住的点（图本身管不了）：

- **同会话互斥**：同一 `thread_id` 并发请求回 409，而不是静默排队（排队会让用户以为卡死）；
- **人工接管优先**：`ai_enabled=false` 的会话直接 409，AI 不与坐席抢话。

---

## 9. 异步与阻塞边界

### 9.1 "把同步函数放到后台执行"——正确理解和三种做法

你说得对，但要精确一点：**不是 fire-and-forget，而是"换个线程执行，但仍然 await 它的结果"。**

图的下一步依赖编码结果（没有向量就没法检索），所以不能用 FastAPI 的 `BackgroundTasks`、
也不能 `asyncio.create_task()` 后不管——那样节点会在结果回来之前就返回，图会拿到空数据。

正确做法是**把阻塞调用挪出事件循环，然后等它**：

```python
# ✅ 正确：挪到线程池，但 await 结果
vecs = await asyncio.to_thread(encoder.encode_sync, texts)

# ❌ 错误一：直接调用 → 卡住整个事件循环，所有用户的 SSE 一起停摆
vecs = encoder.encode_sync(texts)

# ❌ 错误二：扔进后台不管 → 节点提前返回，检索拿到 None
asyncio.create_task(asyncio.to_thread(encoder.encode_sync, texts))
background_tasks.add_task(encoder.encode_sync, texts)
```

三种做法怎么选：

| 做法 | 适用 | 注意 |
|---|---|---|
| **线程池**（`asyncio.to_thread` / 显式 `ThreadPoolExecutor`） | **MVP 用这个**。PyTorch 的推理主要在 C++ 侧跑，会释放 GIL，线程池是真并行 | `to_thread` 用的是默认 executor（`max_workers = min(32, cpu+4)`），建议**自己建一个固定大小的池**，并把并发信号量设成同量级，避免排队不可控 |
| 进程池（`ProcessPoolExecutor`） | 纯 Python 计算占比高、GIL 争抢明显时 | 模型要在每个子进程各加载一份 → 显存/内存翻倍，MVP 不要 |
| 独立推理服务（Triton / TEI / vLLM-embedding） | 要多副本水平扩容、或 GPU 要给多个服务共享时 | 上线阶段的正确形态，MVP 不必 |

**一个容易被忽略的坑**：线程池并发 × torch 内部线程数会互相打架。
`torch` 默认会用满所有核心，4 个并发请求就变成 4×N 个线程在抢核，延迟反而抖动。

```python
# 启动时一次性设定，配合固定大小的线程池
torch.set_num_threads(1)        # 每个推理任务只用 1 个线程
_ENCODER_POOL = ThreadPoolExecutor(max_workers=settings.EMBED_CONCURRENCY)  # 例如 4
# 效果：4 个任务真并行、总占用 4 核、延迟稳定
```

### 9.2 依赖与调用方式

| 依赖 | 调用方式 | 处理 |
|---|---|---|
| DeepSeek-V4.1 API | 原生 async（httpx） | 直接 await；必须设超时与重试上限 |
| BGE-M3 编码 | 同步 CPU/GPU | `asyncio.to_thread` + 固定线程池 + 信号量（见 §1.3、§9.1） |
| BGE-Reranker | 同步 CPU/GPU | 同上，独立池与信号量（别和编码抢） |
| Milvus | pymilvus 客户端 | 有 async 客户端就用；否则 `to_thread` + 连接池 |
| PostgreSQL（业务） | asyncpg / SQLAlchemy async | 直接 await，用事务包住幂等写入 |
| PostgreSQL（checkpoint） | `AsyncPostgresSaver` | 直接 await；注意连接池大小要与并发匹配 |
| 后台客服系统 | HTTP / 消息队列 | 建工单是**必须 await 的**（拿不到 ticket_id 就没法告知用户） |

### 9.3 超时与降级

每个模型角色独立超时（理解类 8s、生成类 30s、审查类 20s）。
超时后的降级路径必须显式定义，不能让它冒泡成 500：

```python
async def with_fallback(coro_factory, fallback, budget_s: float):
    try:
        return await asyncio.wait_for(coro_factory(), timeout=budget_s)
    except (asyncio.TimeoutError, LLMError) as e:
        logger.warning("llm_fallback", error=str(e))
        return fallback        # 例如：审查超时 → 不 pass，而是转人工
```

> **重要**：审查环节超时的降级**绝不能是 pass**。默认降级为 `human`。

---

## 10. 落库清单

> 这里只列**四条核心不变量**。完整表清单、索引、状态机与运营面板见 **`data-model.md`** 与 **`ops-console.md`**。
> 下面四条对应四个"不能靠应用层自觉"的约束，必须由表结构承载。

```sql
-- 审计：每一次审查结论都要能回溯到规则版本与模型版本
CREATE TABLE review_audit (
  id            BIGSERIAL PRIMARY KEY,
  thread_id     TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  review_round  INT  NOT NULL,
  review_kind   TEXT NOT NULL,
  verdict       TEXT NOT NULL,
  risk_level    TEXT NOT NULL,
  hard_rule_hits JSONB NOT NULL DEFAULT '[]',
  panel_reviews  JSONB NOT NULL DEFAULT '[]',
  escalation_independent BOOLEAN,          -- 复核是否不同模型家族（诚实标注）
  content_hash  TEXT NOT NULL,
  release_token_id TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 幂等：外部副作用的唯一防线
CREATE TABLE op_execution (
  idempotency_key TEXT PRIMARY KEY,        -- thread_id:plan_hash
  action          TEXT NOT NULL,
  params          JSONB NOT NULL,
  status          TEXT NOT NULL,           -- pending | success | failed
  result          JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 放行凭据：可查询、可吊销、可过期
CREATE TABLE release_token (
  token_id      TEXT PRIMARY KEY,
  content_hash  TEXT NOT NULL,
  plan_hash     TEXT,
  review_kind   TEXT NOT NULL,
  issued_at     TIMESTAMPTZ NOT NULL,
  expires_at    TIMESTAMPTZ NOT NULL,
  revoked_at    TIMESTAMPTZ
);

-- 人工接管工单
CREATE TABLE handoff_ticket (
  ticket_id   TEXT PRIMARY KEY,
  thread_id   TEXT NOT NULL,
  reason      TEXT NOT NULL,               -- risk_high | emergency | revision_exhausted | need_info
  profile_summary TEXT,                    -- 画像摘要
  last_turns  JSONB NOT NULL,              -- 最近 5 轮对话
  risk_report JSONB NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open',-- open | accepted | closed
  accepted_at TIMESTAMPTZ,                 -- ★ 只有 accepted 后才能告诉用户"人工已接入"
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

> 最后一行那个 `accepted_at` 很重要：设计上要求"**只有人工实际接收后，才可告知用户人工已接入**"。
> 没有这个字段就会在工单还没人看的时候对用户说"客服马上联系您"。

---

## 11. 需要你确认的开口项

1. **独立复核怎么解决**（§1.1 的三选一）——这是唯一会影响合规说服力的技术决策。
2. **视觉链路**：确定用哪家多模态模型，还是 v1 明确不做图片解析。
3. **`clarify_count` 上限**：建议连续 2 次追问不收敛就降级为"一般性说明 + 建议面诊"。目前图上没有这条边。
4. **紧急信号词表与高风险标签清单**：这是规则资产，必须由你们的**医学顾问 + 法务**审定并带版本号，
   不能由开发拍脑袋定。
5. **`ESCALATION_MODEL`、`TOKEN_TTL`、`MAX_REVISION`、`KB_MAX_VERIFY_LOOP`** 这些参数需要业务侧给基线值。
6. **PostgreSQL 里"试卷"**：你写的表清单里有"试卷"，我按模板残留处理了——如果确实是另一条业务线，
   请说明，画像与会话表的设计要相应调整。
7. **人工工单系统**：是接现有客服系统，还是先落库自建？影响 `human_handoff` 与 `accepted_at` 的回写方式。

---

## 附：图 ↔ 代码对应速查

| 图上元素 | 代码位置 |
|---|---|
| 主图五档出口 | `add_conditional_edges("risk_gate", R.after_risk_gate, {...})` |
| `revise -.-> 草稿产出层` 那条箭头 | `revise()` 节点 `return Command(goto=state["origin_agent"])` |
| 七路扇出 | `dispatch()` 返回 `list[Send]` |
| 七路扇入 | 七个节点各自 `add_edge(n, "aggregate")` + `drafts` 的 reducer |
| `risk_gate` 被调用两次 | 同一个编译好的子图 `add_node` 两次（`risk_gate` / `recheck`） |
| `await_confirm · interrupt` | `interrupt({...})` + `Command(resume=...)` |
| 出口层强制校验 | `send()` / `execute_op()` 开头的 `verify_token()` |
| 子图修订再入 | `entry_router()` 判 `revision_count > 0` → 直达 `kb_verify` |
| 子图内部自环 | `add_conditional_edges("kb_verify", after_verify, {...})` |
