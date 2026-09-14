# 总控调度 Agent 与意图识别分流 Agent（伪代码 + Prompt 详解）

> 配套：`langgraph-pseudocode.md`（整体架构）、`mvp-config-rules.md`（规则与参数）。
> 本文把这两个 Agent 拆到"能照着写"的粒度。

---

## 0. 先澄清一个观念：总控 Agent 不是一个节点

蓝图里"总控调度 Agent"有 4 个内部节点，但**在 LangGraph 里它不应该被实现成一个 LLM 大节点**，
而是**主图的骨架本身**：

| 蓝图里总控的内部节点 | 代码里的落点 | 类型 |
|---|---|---|
| 输入标准化与会话关联 | `normalize` 节点 | 纯规则 |
| 多意图识别与字段抽取 | `classify` 节点（**属于意图识别 Agent**） | LLM |
| 任务规划与结果聚合 | `dispatch` + `aggregate` 两个节点 | 纯规则 |
| 重试、状态与审计 | `retry_check` + state 字段 + `audit_log` | 纯规则 |

**为什么不能做成一个大 LLM 节点**（这段面试可以直接讲）：

1. **可审计性**：一个节点内部做了什么，checkpointer 只留一个状态快照，出问题无法定位是哪一步错了
2. **可恢复性**：`interrupt` 只能挂在节点边界上；大节点内部无法挂起，用户确认流程做不了
3. **并行能力**：多意图并行扇出依赖 `Send`，而 `Send` 只能由路由函数产生
4. **确定性**：路由是**规则问题**，不是概率问题。让模型决定"这句话该谁处理"，
   就是把最不该出错的一步交给最不稳定的环节
5. **成本与延迟**：每轮多一次大模型调用，且没有任何收益

一句话：**总控是图的形状，不是图里的一个框。**

---

## 1. 总控的四个承担点

### 1.1 `normalize`：输入标准化与会话关联

```python
# graph/nodes/intake.py
async def normalize(state: ZhimeiState, *, deps) -> dict:
    """
    职责：
      1. 绑定会话与身份（谁是这个人、能不能服务）
      2. 输入清洗（去控制字符、限长、敏感串预标记）
      3. 生成本轮 turn_id
      4. 重置"本轮字段"，但保留跨轮次预算（clarify_count）
    不调模型。
    """
    session = await deps.pg.get_session(state["session_id"])
    if session["ai_enabled"] is False:
        # 已被人工接管 → 本轮不进图（由 API 层拦截，这里是双保险）
        raise HumanTakeoverActive(session["session_id"])

    auth = await deps.pg.load_auth(state["session_id"])
    cleaned = deps.text.clean(state["user_input"])          # 控制字符、零宽字符、超长截断
    flags = deps.text.prescan(cleaned)                      # 手机号/身份证/银行卡 预标记（脱敏用）

    return {
        "auth": auth,
        "user_input": cleaned,
        "slot_flags": flags,
        "turn_id": deps.uuid(),
        # ── 每轮重置 ──
        "review_round": 0, "revision_count": 0, "review_feedback": [],
        "confirmed": None, "confirm_result": None,
        "execution_result": None, "handoff_ticket": None, "outbound": None,
        # ── 刻意不重置：clarify_count ──
        "audit_log": [{"event": "normalize", "auth_ok": auth["verified"],
                       "clarify_count": state.get("clarify_count", 0)}],
    }
```

**状态契约**：读取 `session_id` / `user_input` / `clarify_count`；写入 `auth` / `turn_id` / 全部本轮字段。

**失败处理**：会话不存在 → 抛业务异常由 API 层回 404；`ai_enabled=false` → 不走图，直接进人工队列。

### 1.2 `dispatch`：任务规划与分派

这是总控里**唯一有"决策"色彩**的地方，但它仍然是纯规则。

```python
# 意图 → 节点 的静态路由表（配置化，可热更新）
ROUTE = {
    "knowledge_edu": "k_agent",
    "recommend":     "r_agent",
    "clinic_info":   "c_agent",
    "booking":       "b_agent",
    "postcare":      "p_agent",
}

# 多意图并存时的优先级（数字越小越优先）
PRIORITY = ["booking", "postcare", "knowledge_edu", "clinic_info", "recommend"]
MAX_FANOUT = 3          # 一轮最多扇出几个 Agent，防止成本失控


def dispatch(state: ZhimeiState) -> list[Send] | str:
    """
    纯规则。返回：
      - list[Send]  → 多意图并行扇出
      - 节点名字符串 → 单路或兜底
    """
    if state.get("emergency"):
        return "emergency_draft"                  # 紧急优先，不跑常规 Agent

    intents = resolve_intents(state["intents"])   # 见下方冲突消解
    if not intents:
        if state.get("clarify_count", 0) >= MAX_CLARIFY:
            return "k_agent" if has_any_slot(state["slots"]) else "human_handoff"
        return "clarify"

    targets = list(dict.fromkeys(ROUTE[i] for i in intents if i in ROUTE))
    return [Send(t, {"slots": state["slots"], "auth": state["auth"],
                     "turn_id": state["turn_id"]}) for t in targets[:MAX_FANOUT]]


# ── 多意图冲突消解：规则表，不调模型 ─────────────────────────
CONFLICT_RULES = [
    # (条件, 处理, 理由)
    (lambda s: "clarify" in s and len(s) > 1,        "drop_clarify",
     "已有明确意图时不该再反问"),
    (lambda s: "postcare" in s and "knowledge_edu" in s, "drop_knowledge",
     "术后场景优先，避免科普与护理建议互相打架"),
    (lambda s: "booking" in s and ("postcare" in s or "recommend" in s), "keep_booking",
     "有明确操作意图时先完成操作，其余放到下一轮"),
    (lambda s: len(s) > MAX_FANOUT,                  "truncate_by_priority",
     "按优先级截断，防止一轮扇出过多"),
]

def resolve_intents(intents: list[str]) -> list[str]:
    """去 `clarify`、应用冲突规则、按优先级排序。确定性函数，可单测。"""
    s = [i for i in dict.fromkeys(intents) if i != "clarify"]
    if "postcare" in s and "knowledge_edu" in s:
        s.remove("knowledge_edu")
    if "booking" in s and ("postcare" in s or "recommend" in s):
        s = ["booking"] + [i for i in s if i == "knowledge_edu"]
    return sorted(s, key=lambda i: PRIORITY.index(i) if i in PRIORITY else 99)
```

**为什么 `booking` 要压过 `recommend`/`postcare`**：用户说"帮我约周五的热玛吉"时，
他此刻要的是**把事办了**。同时生成一段推荐话术和一段术后建议，会让回复变长、重点模糊，
而且推荐内容可能改动预约参数。分开两轮处理，体验更干净。

**什么时候才需要 LLM 参与规划**：只有当多意图之间的取舍**无法用规则表达**时（见 §2）。
MVP 阶段规则表已经能覆盖绝大多数情况，**默认不调 LLM**。

### 1.3 `aggregate`：结果聚合

```python
# 拼接顺序固定，保证同样输入产出同样文本（可复现、可回归测试）
MERGE_ORDER = ["emergency_draft", "clarify", "p_agent", "k_agent",
               "c_agent", "r_agent", "b_agent"]

async def aggregate(state: ZhimeiState, *, deps) -> dict:
    """
    职责：
      1. 把七路草稿整理成【一个待审对象】
      2. 合并引用与缺口
      3. 操作类草稿 → 构造 operation 并算 plan_hash
    """
    drafts = state["drafts"]                    # reducer 已保证只留本轮
    if not drafts:
        return {"draft": {"content": "", "citations": [], "gaps": ["未能产出草稿"]},
                **begin_review_round(state, "content")}

    ordered = sorted(drafts, key=lambda d: MERGE_ORDER.index(d["agent"]))

    if len(ordered) == 1:
        content = ordered[0]["content"]
    else:
        # 多意图：加小标题分段，避免两段内容黏在一起看不出是两件事
        content = "\n\n".join(f"【{deps.labels[d['agent']]}】\n{d['content']}" for d in ordered)

    operation = None
    for d in ordered:
        if d.get("operation"):                  # b_agent 产出
            operation = {**d["operation"],
                         "plan_hash": deps.security.hash_plan(d["operation"])}
            break

    kind = "operation" if operation else "content"
    return {
        "draft": {
            "content": content,
            "citations": dedupe_citations([c for d in ordered for c in d["citations"]]),
            "gaps": list(dict.fromkeys(g for d in ordered for g in d["gaps"])),
        },
        "operation": operation,
        "plan_hash": operation["plan_hash"] if operation else None,
        **begin_review_round(state, kind),
    }
```

**三个细节**：

- `MERGE_ORDER` 是**固定列表**而不是 `dict` 顺序 —— 保证可复现，也方便写快照测试
- `dedupe_citations` 按 `(doc_id, version, quote)` 去重，避免同一证据被引用多次
- `plan_hash` 在这里算，且**只算一次** —— 下游的确认、执行、凭据校验都用它

### 1.4 `retry_check`：重试、状态与审计

```python
async def retry_check(state: ZhimeiState, *, deps) -> dict:
    """
    总控的收口节点：三种预算在这里统一检查（见 langgraph-pseudocode.md §3.4）。
    它不改内容，只做"还能不能继续"的判定与记录。
    """
    over = []
    if state.get("revision_count", 0) > settings.MAX_REVISION:
        over.append("revision_exhausted")
    if state.get("clarify_count", 0) > settings.MAX_CLARIFY:
        over.append("clarify_exhausted")
    if state.get("kb_verify_round", 0) > settings.KB_MAX_VERIFY_LOOP:
        over.append("kb_loop_exhausted")

    return {"audit_log": [{"event": "retry_check",
                           "revision": state.get("revision_count", 0),
                           "clarify": state.get("clarify_count", 0),
                           "over_budget": over}]}


def after_retry_check(state: ZhimeiState) -> str:
    return "retry" if state["revision_count"] <= settings.MAX_REVISION else "exhausted"
```

---

## 2. 总控唯一需要的 Prompt：`supervisor_plan`

默认情况下总控**不调模型**。只有一种场景需要：**多意图之间出现规则表无法表达的取舍**，例如：

> "我上次做的那个项目效果不太好，你们浦东店还有别的医生吗？顺便这次能不能便宜点"

这里有 `postcare`（效果不满）+ `clinic_info`（换医生）+ 隐含 `recommend`（换项目）+ 价格诉求。
规则表能排序，但"这一轮到底先解决哪个"需要理解语义。

```python
SUPERVISOR_PLAN_SYSTEM = """
你是医美咨询系统的任务规划模块。**你不是客服，不要生成任何面向用户的话术。**

输入是用户这一句话解析出的多个意图与槽位。你的唯一任务是决定：
  1. 这一轮应该交给哪些专业模块处理，以及顺序
  2. 哪些意图应当留到下一轮（避免一轮做太多事，回复变长、重点模糊）
  3. 是否存在必须先向用户澄清、否则无法推进的情况

严格约束：
- 只能从给定的意图列表里挑选，禁止新增意图。
- 不得改写、不得补充业务内容，不得输出任何医疗判断。
- 涉及操作类意图（预约/改约/取消）时，它必须排在最前——用户此刻要的是把事办了。
- 涉及术后异常、争议、投诉时，必须标注 need_human=true。
- 不确定就把该意图放进 defer，不要硬排。

只输出 JSON。
"""

SUPERVISOR_PLAN_USER = """
用户原话：{user_input}
已识别意图：{intents}
已抽取槽位：{slots_json}
会话已进行轮数：{turn_no}
上一轮已处理的意图（避免重复）：{last_intents}

输出 JSON：
{
  "ordered": ["booking", "clinic_info"],
  "defer": ["recommend"],
  "reason": "用户有明确换医生诉求，先解决可执行的查询；推荐留到下一轮",
  "need_clarify": false,
  "clarify_question": null,
  "need_human": false,
  "human_reason": null
}
"""
```

**调用条件**（不是每轮都调）：

```python
async def maybe_plan(state, *, deps):
    intents = resolve_intents(state["intents"])
    if len(intents) <= 1:
        return {}                                  # 单意图：规则表足够
    if not deps.rules.needs_llm_planning(intents): # 规则表能覆盖的常见组合
        return {}                                  # 直接返回，省一次调用
    plan = await deps.llm.structured("supervisor_plan", SupervisorPlan,
                                      system=SUPERVISOR_PLAN_SYSTEM,
                                      user=SUPERVISOR_PLAN_USER.format(...))
    return {"plan": plan.model_dump()}
```

> **设计要点**：LLM 规划是**兜底**，不是主路径。规则能覆盖的组合直接走规则，
> 这既省成本，也让绝大多数请求的编排是**确定性**的。

---

## 3. 意图识别分流 Agent

### 3.1 职责边界

| 做 | 不做 |
|---|---|
| 识别一句话里的一个或多个意图 | ❌ 生成任何面向用户的文本 |
| 抽取槽位（项目/门店/医生/时间/症状/术后天数/预算） | ❌ 判断内容能不能发给用户（那是风险审查的事） |
| 相对时间归一化为绝对时间，并给出推断依据 | ❌ 做医学判断（"这是不是正常"） |
| 输出置信度与"不确定"标记 | ❌ 决定路由（路由是 `dispatch` 的规则表） |
| 预标记紧急信号（供规则层复核） | ❌ 决定是否转人工 |

> **一句话**：它只做"把自然语言变成结构化字段"，不做任何决策。

### 3.2 输入契约

```python
{
  "user_input": str,               # 本轮用户原话（已清洗）
  "history_slots": dict,           # 会话内已累积的槽位（用于指代消解："那个项目"）
  "recent_turns": list[dict],      # 最近 3 轮 (role, content)，只用于消解指代，不用于抽取
  "entity_dict": {                 # ★ 业务实体字典：把口语映射到标准名
      "projects": ["热玛吉", "超声炮", "水光针", "线雕", ...],
      "stores":   ["浦东店", "静安店", ...],
      "doctors":  ["张医生", "李医生", ...],
  },
  "now": "2025-03-12T14:30:00+08:00"   # ★ 时间归一化必须有基准时刻
}
```

**三个关键点**：

- `entity_dict` 必须注入。否则"热玛吉"可能被识别成"热马吉"，下游查库全部落空
- `now` 必须传，且由服务端给（不能让模型自己猜今天几号）
- `recent_turns` **只给最近 3 轮**且**明确标注"仅用于理解指代"**，否则模型会去抽取历史信息造成重复

### 3.3 输出契约

```python
class ClassifyOut(BaseModel):
    intents: list[Literal["knowledge_edu", "recommend", "clinic_info",
                          "booking", "postcare", "clarify", "other"]]
    slots: Slots                      # project/store/doctor/datetime/symptom/postop_days/budget
    time_note: str | None             # 相对时间的推断依据，例如"下周三 = 2025-03-19"
    entity_note: str | None           # 实体归一化说明，例如"'热马吉'→'热玛吉'"
    emergency_hint: bool = False      # ★ 仅供规则层复核，不直接触发紧急路径
    emergency_terms: list[str] = []   # 触发提示的原词
    uncertain: bool = False
    confidence: float = 1.0
```

> `emergency_hint` 只是**提示**：真正的紧急判定在 `emergency_screen` 的规则层
> （见 `mvp-config-rules.md` §1）。模型说"可能是紧急"不能直接触发转人工，
> 但模型说"不是紧急"**也不能**阻止规则层触发 —— 这是单向的：

```python
def emergency_screen_final(rule_hits, model_hint) -> bool:
    return bool(rule_hits) or model_hint      # 规则优先，模型只能加不能减
```

### 3.4 时间归一化规则

| 用户说法 | 归一化结果 | 说明 |
|---|---|---|
| "下周三下午" | `2025-03-19T14:00:00+08:00`（并写 `time_note`） | 默认下午 = 14:00，需在 note 里说明 |
| "后天上午" | 相对于 `now` + 2 天，09:00–12:00 → 取 09:00 | —— |
| "这周末" | `2025-03-15` 或 `2025-03-16` → **不猜**，写入 `slots.datetime=null` 并标记待澄清 | ★ 有歧义的时间不猜 |
| "越快越好" | `datetime=null`，`slots.urgency="asap"` | 不猜具体时间 |

**原则**：**能确定的归一化，不能确定的留空并交给澄清流程**。
猜一个看起来合理的时间，会导致用户确认了一个他从没说过的时间——这是操作类场景的严重事故。

### 3.5 关键 Prompt 全文

```python
CLASSIFY_SYSTEM = """
【角色边界】
你是医美咨询系统的意图理解模块，不是客服。不要寒暄、不要自我介绍、不要生成给用户看的话术。
你不做医学判断，不给建议，不判断内容是否合规。

【任务】
把用户这一句话解析为：意图 + 槽位 + 时间归一化 + 紧急信号提示。

【意图取值】只能从下面选，可多选：
  knowledge_edu  —— 问原理、区别、流程、恢复期等一般科普
  recommend      —— 问"我适合什么""推荐什么"
  clinic_info    —— 问门店、医生、资质、地址、营业时间
  booking        —— 预约、改约、取消
  postcare       —— 术后护理、术后症状、恢复情况
  clarify        —— 无法判断用户想干什么
  other          —— 与业务无关

【槽位抽取规则】
- 只抽取用户【明确说出】的内容。没说就留 null，禁止推断、禁止用常识补全。
- 指代词（"那个项目""上次那个"）只有在【已知会话槽位】里有对应值时才可填充，并写进 entity_note。
- 项目名、门店名、医生名必须归一化为【业务字典】里的标准写法；近似音/错别字也要归一化，
  并在 entity_note 里说明原始说法。
- 症状只记录用户原话描述，不要改写成医学术语，不要判断严重程度。

【时间归一化】
- 以给定的 now 为基准，把相对时间转成 ISO 8601（含时区）。
- 必须在 time_note 里写清推断过程（例如"'下周三'按 now=3/12 推算为 3/19"）。
- 有歧义时不要猜：写入 null，并把歧义写进 time_note。

【紧急信号提示】
- 只在用户以【第一人称自述当前症状】时才置 emergency_hint=true，
  并在 emergency_terms 里列出触发的原词。
- 以下情况【不算】紧急信号，不要置 true：
  · 询问风险（"会不会失明"）、听说/网上说（"网上说会烂脸"）
  · 否定（"没有不舒服"）
  · 第三人称（"我朋友做完眼睛看不清"）
  · 假设（"如果失明了怎么办"）

【输出】
只输出 JSON，不要 markdown 代码块标记，不要任何解释文字。
不确定的字段用 null，并把 uncertain 置 true —— 猜一个看起来合理的值比留空有害得多。
"""

CLASSIFY_USER = """
now: {now}
业务字典：
  项目：{projects}
  门店：{stores}
  医生：{doctors}

已知会话槽位（可沿用，可被本轮覆盖）：{history_slots_json}
最近对话（仅用于理解指代，不要从中抽取新信息）：
{recent_turns_block}

用户这句话：{user_input}

输出 JSON：
{{
  "intents": ["..."],
  "slots": {{"project": null, "store": null, "doctor": null, "datetime": null,
             "symptom": null, "postop_days": null, "budget": null}},
  "time_note": null,
  "entity_note": null,
  "emergency_hint": false,
  "emergency_terms": [],
  "uncertain": false,
  "confidence": 0.0
}}
"""
```

#### Few-shot 示例（建议做成可配置的示例库，按场景检索注入 2–3 条）

| # | 输入 | 期望输出要点 |
|---|---|---|
| 1 | "下周三下午能约浦东店的热玛吉吗" | `intents=["booking"]`；`project="热玛吉"`；`store="浦东店"`；`datetime=2025-03-19T14:00+08:00`；`time_note` 写推算过程 |
| 2 | "热玛吉和超声炮啥区别，哪个适合我，顺便看看浦东店有没有号" | `intents=["knowledge_edu","recommend","clinic_info"]`（**三个都要，不能只选一个**） |
| 3 | "我做完水光第三天了，脸还是红，正常吗" | `intents=["postcare"]`；`postop_days=3`；`symptom="脸还是红"`；`emergency_hint=false`（不判断严重程度） |
| 4 | "热玛吉会不会导致失明啊" | `intents=["knowledge_edu"]`；`emergency_hint=**false**`（这是询问风险，不是自述症状） |
| 5 | "我现在脸发白还特别疼，是不是栓塞了" | `intents=["postcare"]`；`emergency_hint=**true**`；`emergency_terms=["脸发白","特别疼"]` |
| 6 | "那个怎么样" | `intents=["clarify"]`；`uncertain=true`（指代无法消解） |
| 7 | "我朋友做完眼睛看不清" | `emergency_hint=**false**`（第三人称）；`intents=["knowledge_edu"]` |

> 示例 4 与示例 5 是这组 few-shot 里最有价值的两条：
> **它们教模型区分"问风险"和"报症状"**，这是紧急词表误报率的最大来源。

### 3.6 失败与降级

```python
async def classify(state, *, deps) -> dict:
    payload = build_payload(state)
    for attempt in (1, 2):
        try:
            out = await deps.llm.structured("classify", ClassifyOut,
                                             system=CLASSIFY_SYSTEM, user=payload,
                                             timeout=settings.T_UNDERSTAND)
            return apply(out, state)
        except (ValidationError, JSONDecodeError):
            payload += "\n\n注意：上次输出不是合法 JSON，请只输出 JSON 对象本身。"
        except asyncio.TimeoutError:
            break

    # 两级降级：先规则关键词，再澄清
    guess = deps.rules.keyword_route(state["user_input"])     # 极简规则表
    if guess:
        return {"intents": guess, "slots": state.get("slots", {}),
                "audit_log": [{"event": "classify_degraded", "mode": "keyword"}]}
    return {"intents": ["clarify"], "uncertain": True,
            "audit_log": [{"event": "classify_degraded", "mode": "clarify"}]}


def apply(out: ClassifyOut, state) -> dict:
    """把模型输出并回状态，并处理跨轮次预算。"""
    reset = {} if "clarify" in out.intents else {"clarify_count": 0}
    return {
        "intents": out.intents,
        "slots": {**state.get("slots", {}), **{k: v for k, v in out.slots.items() if v}},
        "emergency_hint": out.emergency_hint,
        **reset,
    }
```

**两条纪律**：

1. **槽位合并用"非空覆盖"**，不要用 `dict.update` 整体覆盖 —— 否则本轮没提到的槽位会被清空
2. **低置信不硬猜**：`uncertain=true` 或 `confidence < 0.5` 时，即使有意图也应优先走澄清

### 3.7 评测指标与测试集

| 指标 | 目标（MVP） | 说明 |
|---|---|---|
| 意图多标签 micro-F1 | ≥ 0.90 | 多意图必须全中才算对 |
| **漏路由率** | **< 2%** | 本该路由却没路由（最严重的错误类型） |
| 槽位 F1（项目/门店/时间） | ≥ 0.90 | 分类字段按精确匹配 |
| 时间归一化准确率 | ≥ 0.95 | 允许 ±1 天误差外的算错 |
| 紧急信号**召回率** | **≥ 0.98** | 规则层 + 模型层合计；宁可误报 |
| 紧急信号误报率 | ≤ 15% | 超过就要调词表（走误报回流） |
| clarify 误判率 | < 5% | 把本可回答的问题判成 clarify |

**测试集构成建议**（≥ 300 条人工标注）：

```
8 类意图 × {单意图, 多意图}            = 16 组
× {槽位完整, 槽位缺失, 指代}            = 48 组
× {陈述句, 疑问句, 否定句}              = 144 组
+ 紧急信号正例 30 条 + 易误报负例（问风险/听说/否定/第三人称/假设）50 条
+ 错别字与口语变体（"热马吉""打个水光"）30 条
```

> 最后两组是这个测试集的重点。**绝大多数真实事故来自"看起来像但不是"的样本**，
> 而不是来自没见过的新意图。

---

## 4. 一次完整交互的协作时序

用户说：**"下周三下午能约浦东店的热玛吉吗，另外这玩意儿跟超声炮啥区别"**

```
normalize      会话绑定、turn_id、重置本轮字段（clarify_count 保留）
   ↓
emergency_screen  规则层未命中 → 模型兜底也未提示 → 继续
   ↓
classify       识别出 ["booking", "knowledge_edu"]
               slots: project=热玛吉, store=浦东店, datetime=2025-03-19T14:00+08:00
               time_note: "下周三 = 03-19"
   ↓
dispatch       规则冲突消解：booking 优先，knowledge_edu 保留（可并存）
               → Send("b_agent"), Send("k_agent")   ← 并行
   ↓
b_agent        查档期/规则 → 操作方案 + 待确认文案（含 plan_hash）
k_agent        子图：检索 → 证据 → 草稿 → 事实核对 → 待审草稿
   ↓
aggregate      合并成一段待审内容 + 构造 operation + 算 plan_hash
   ↓
risk_gate      审查（content + operation 两种 kind）
   ↓
pass → output_type → 操作类 → await_confirm（挂起等用户确认）
```

如果这一轮 `booking` 通过了但 `knowledge_edu` 被判 `revise`，**两者会一起退回**——
因为它们是同一个待审对象。这是有意的：宁可整段重审，也不要出现"一半内容审过、一半没审过"。

---

## 5. 反面清单（这些做法一定会出问题）

| # | 错误做法 | 后果 |
|---|---|---|
| 1 | 把总控做成一个"大 LLM 节点"，让它决定一切 | 不可审计、不可挂起、丢失并行、成本翻倍 |
| 2 | 让意图识别 Agent 生成给用户看的话 | 绕过风险审查直接对用户说话 |
| 3 | 让模型输出"要不要放行 / 是否合规" | 把裁决权交给概率模型，合规上无法解释 |
| 4 | 用模型做路由（"你觉得该谁处理"） | 路由不确定，无法回归测试，多意图丢意图 |
| 5 | 多意图只选"最可能的一个" | 用户的真实需求被丢掉一半，体验最差 |
| 6 | 槽位用 `dict.update` 合并 | 后续轮次把之前说的项目/门店清空 |
| 7 | 相对时间让模型自己猜 | 用户确认了一个他从没说过的时间 → 操作事故 |
| 8 | 模型说"不是紧急"就不再走规则层 | 漏报急症，这是最不可接受的失败模式 |
| 9 | 紧急提示交给模型现场生成措辞 | 最不能出错的场景用了最不稳定的环节 |
| 10 | 把 `clarify_count` 和 `revision_count` 混用一个计数 | 修订预算被追问吃掉，或反过来无限追问 |
