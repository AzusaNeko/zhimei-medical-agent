"""
主图 State 契约。

三条纪律（写在最前面，因为违反它们的代价都是线上事故）：

1. 只放可序列化数据。checkpointer 会把它写进 PostgreSQL，塞模型客户端 /
   数据库连接 / Pydantic 对象会直接炸。
2. 分清「日志型」与「当前值型」字段：
     · 日志型（audit_log / panel_reviews）→ operator.add，只追加
     · 当前值型（drafts）→ 只保留本轮，用 keep_latest_turn
3. 每轮进入审查前必须重置结论类字段（verdict / release_token / risk_level），
   否则会读到上一轮的陈旧结论 —— 这是这类系统最隐蔽的 bug。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, TypedDict

Verdict = Literal["pass", "revise", "need_info", "block", "human"]
ReviewKind = Literal["content", "operation", "result_reply", "emergency"]
RiskLevel = Literal["low", "medium", "high"]
ConfirmResult = Literal["confirmed", "denied", "mismatch"]


def _fingerprint(item: Any) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)


def append_unique(left: list, right: list) -> list:
    """
    累积型字段的 reducer，按内容去重。

    ★ 为什么不能直接用 operator.add（这是跑起来才发现的一个坑）：
      LangGraph 把编译好的子图当节点用时，父图会把自己的状态传进子图，
      子图返回的最终状态【包含它继承到的那些累积字段】，父图再把这些值
      通过 reducer 追加一次 —— 结果是审计日志按子图调用次数成倍膨胀，
      一次对话能刷出上百条重复事件。

      正确做法有两条：
        A. 给子图单独定义 schema，把累积型字段排除在外（生产环境推荐）
        B. 用内容去重的 reducer（MVP 采用，改动最小且不丢关键信息）
      代价：完全相同的两条事件会被合并成一条。真正的审计以
      app.review_audit / app.hard_rule_hit 表为准，state 里的日志只用于追踪。
    """
    if not right:
        return left
    seen = {_fingerprint(x) for x in left}
    out = list(left)
    for item in right:
        key = _fingerprint(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def keep_latest_turn(left: list[dict], right: list[dict]) -> list[dict]:
    """
    七路产出者并行写 drafts，修订后会重新产出同一路草稿。

    规则（三条缺一不可）：
      1. 只保留【本轮】的草稿 —— 丢掉上一轮/上一句话的残留
      2. 同一轮里【同一个 agent】的旧稿被新稿替换 —— 否则修订后会出现两份草稿，
         aggregate 把它们拼在一起，结果是"改过的内容"和"没改的内容"同时送审，
         审查必然再次命中同一条规则（这个坑是跑起来才暴露的）
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


class Draft(TypedDict, total=False):
    agent: str            # k_agent / r_agent / ... / clarify / emergency_draft
    turn_id: str          # 属于哪一轮对话（uuid）
    revision: int         # 该轮内第几次修订稿（0 起，仅用于审计与调试）
    content: str          # 待发送文本（尚未过审）
    citations: list[dict] # [{evidence_id, doc_id, version, quote}]
    gaps: list[str]       # 已知的信息缺口，要如实告知用户
    route_hint: str | None
    risk_tags: list[str]
    operation: dict | None  # 仅 b_agent：{action, params}


class ZhimeiState(TypedDict, total=False):
    # ───────── 会话与入口 ─────────
    session_id: str
    thread_id: str
    turn_id: str
    user_id: str | None
    channel: str
    user_input: str
    attachments: list[dict]

    # ───────── 权限与授权（由业务系统给，模型无权编造）─────────
    auth: dict

    # ───────── 意图与分派 ─────────
    intents: list[str]
    slots: dict
    emergency: bool
    emergency_hits: list[dict]
    emergency_hint: bool
    priority: Literal["normal", "emergency"]
    plan: dict | None

    # ───────── 并行扇入（必须带 reducer）─────────
    drafts: Annotated[list[Draft], keep_latest_turn]
    panel_reviews: Annotated[list[dict], append_unique]
    audit_log: Annotated[list[dict], append_unique]

    # ───────── 待审对象 ─────────
    review_kind: ReviewKind
    review_round: int
    draft: dict
    operation: dict | None
    evidence: list[dict]

    # ───────── 审查结论（每轮进入 gate 前重置）─────────
    verdict: Verdict | None
    review_feedback: list[str]
    hard_rule_hits: list[dict]
    risk_level: RiskLevel | None
    escalation_used: bool
    escalation_independent: bool
    revision_count: int
    clarify_count: int
    origin_agent: str | None
    release_token: str | None

    # ───────── 确认与执行 ─────────
    plan_hash: str | None
    confirmed: bool | None
    confirm_result: ConfirmResult | None
    idempotency_key: str | None
    execution_result: dict | None
    outbound: dict | None

    # ───────── 人工与收尾 ─────────
    handoff_ticket: dict | None
    # ★ 转人工提示单独一个 key：紧急场景会与 send 并行执行，
    #   两个节点都写 outbound 会触发 InvalidUpdateError（同一超步同一 key 只能写一次）
    handoff_notice: str | None
    blocked_rule_ids: list[str]

    # ───────── 子图共享字段 ─────────
    # 说明：两个子图直接复用主图 State（而不是各自定义私有 State），
    # 好处是不会有"子图写了但父图 schema 里没有 → 更新被静默丢弃"的坑；
    # 代价是所有键集中在一处 —— 而集中在一处仍然会漏（见下方 kb_evidence 的教训）。
    kb_scope_ok: bool
    kb_queries: list[dict]
    kb_sufficient: bool
    kb_candidates: list[dict]
    # ★ kb_evidence 曾经被我漏掉过：kb_evidence 节点算出了 6 条证据、审计里
    #   enough=true / top_score=0.77，但下一个节点读到的却是空列表 ——
    #   因为键不在 schema 里，LangGraph **静默丢弃**了这次写入，
    #   表现为"检索明明搜到了，草稿却说没有可用依据"。
    #   加子图字段时务必对照：写了哪些键，schema 里就有哪些键。
    kb_evidence: list[dict]
    kb_enough: bool
    kb_draft_content: str
    kb_citations: list[dict]
    kb_gaps: list[str]
    kb_claim_checks: list[dict]
    kb_verify_round: int
    kb_exit: str | None

    # ───────── 控制位（由节点写入、由路由函数读取）─────────
    slot_flags: dict
    recent_turns: list[dict]
    plan: dict | None
    output_kind: str
    force_human: bool
    #: 作废方案（用户否认 / 方案哈希不符）：本轮只保留作废回复，之前那份操作方案一并作废。
    #: 没有这个开关的话，keep_latest_turn 会按 (turn_id, agent) 保留旧的操作方案草稿，
    #: aggregate 把"作废说明"和"旧方案"拼在一起 → operation 还在 → 又挂起一次等确认。
    supersede_drafts: bool
    final_ok: bool
    retry_ok: bool
    review_input_ok: bool
    hard_rule_hits_history: Annotated[list[dict], append_unique]


# 每轮送审前的重置内容 —— 漏了就是线上事故
RESET_ON_NEW_REVIEW: dict[str, Any] = {
    "verdict": None,
    "release_token": None,
    "review_feedback": [],
    "hard_rule_hits": [],
    "risk_level": None,
    "escalation_used": False,
    "escalation_independent": False,
    "blocked_rule_ids": [],
}


def begin_review_round(state: ZhimeiState, kind: ReviewKind) -> dict[str, Any]:
    """
    进入审查轮次：重置结论类字段并递增轮次号。aggregate 与 set_result_kind 都要调它。

    ★ 顺手把本轮已产生的硬规则命中转存进 history（带 turn_id 标记）——
      因为 hard_rule_hits 会被清空，而"同一规则在复审中重复命中 → 升级为 block"
      这条规则需要看到上一轮的命中记录。history 是 operator.add 追加字段，
      无法被清空，所以用 turn_id 区分轮次。
    """
    return {
        **RESET_ON_NEW_REVIEW,
        "review_kind": kind,
        "review_round": int(state.get("review_round", 0)) + 1,
        "hard_rule_hits_history": [
            {**h, "turn_id": state.get("turn_id")}
            for h in (state.get("hard_rule_hits") or [])
        ],
    }
