"""
汇聚节点：把七路草稿整理成【一个待审对象】。

三条设计要点：
  · 拼接顺序固定（MERGE_ORDER），保证同样输入产出同样文本 —— 可复现、可回归测试
  · plan_hash 在这里算，且只算一次；下游的确认、执行、凭据校验都用它
  · route_hint 里带 human_* 的草稿（紧急提示 / 上传图片）会置 force_human，
    由 after_risk_gate 路由成"放行提示 + 立即转人工"两条并行出口
"""

from __future__ import annotations

from typing import Callable

from ...services import text as T
from ...services.deps import Deps
from .. import progress
from ..state import begin_review_round

MERGE_ORDER = ["emergency_draft", "clarify", "p_agent", "k_agent",
               "c_agent", "r_agent", "b_agent"]

AGENT_LABELS = {
    "k_agent": "科普说明", "r_agent": "项目方向", "c_agent": "门店与资质",
    "b_agent": "预约方案", "p_agent": "术后护理",
}


def make_aggregate_node(deps: Deps) -> Callable:
    async def aggregate(state: dict) -> dict:
        # 子图内部的进度事件不会冒泡到父图流，所以在进入守门子图前由父图自己发一条
        progress.emit("review")
        drafts = state.get("drafts") or []
        if state.get("supersede_drafts"):
            # 作废方案：本轮只保留作废回复，之前那份操作方案一并作废
            drafts = [d for d in drafts if d.get("agent") == "plan_void"] or drafts
        if not drafts:
            return {
                "draft": {"content": "", "citations": [], "gaps": ["未能产出草稿"]},
                **begin_review_round(state, "content"),
            }

        ordered = sorted(drafts, key=lambda d: MERGE_ORDER.index(d["agent"])
                         if d.get("agent") in MERGE_ORDER else 99)

        if len(ordered) == 1:
            content = ordered[0].get("content", "")
        else:
            # 多意图：分段 + 小标题，避免两段内容黏在一起看不出是两件事
            content = "\n\n".join(
                f"【{AGENT_LABELS.get(d['agent'], d['agent'])}】\n{d.get('content', '')}"
                for d in ordered)

        # 操作类：取第一条带 operation 的草稿，并绑定方案哈希
        operation = None
        if not state.get("supersede_drafts"):
            for d in ordered:
                if d.get("operation"):
                    operation = {**d["operation"],
                                 "plan_hash": deps.security.hash_plan(d["operation"])}
                    break

        # 需要并行人工作业的场景（紧急提示 / 上传图片）
        force_human = any(str(d.get("route_hint") or "").startswith("human") for d in ordered)

        kind = "operation" if operation else ("emergency" if state.get("emergency") else "content")

        return {
            "draft": {
                "content": content,
                "citations": _dedupe_citations([c for d in ordered for c in (d.get("citations") or [])]),
                "gaps": T.dedupe([g for d in ordered for g in (d.get("gaps") or [])]),
            },
            "operation": operation,
            "plan_hash": operation["plan_hash"] if operation else None,
            "force_human": force_human,
            "evidence": list(state.get("kb_evidence") or []),
            **begin_review_round(state, kind),
            "audit_log": [{"event": "aggregate",
                           "agents": [d.get("agent") for d in ordered],
                           "review_kind": kind,
                           "has_operation": bool(operation),
                           "force_human": force_human}],
        }

    return aggregate


def _dedupe_citations(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in items:
        key = (c.get("doc_id"), c.get("version"), c.get("quote"))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
