"""
主图装配。

阅读顺序（自上而下）：
  入口与调度层 → 草稿产出层（七路并行）→ 汇聚与守门 → 输出与执行 → 出口层
  修订回路与执行链在守门层下方左右并行，最后由出口层收敛。

命名约定：节点 id 出现在 State 的 audit_log 与所有日志里，
排查问题时用 `python -m app.cli --demo` 能看到每个节点实际走过没有。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ..services.deps import Deps
from .nodes import aggregate as agg
from .nodes import exits, intake, release, revision, specialists
from .routers import make_routers
from .state import ZhimeiState
from .sub_knowledge import build_knowledge_subgraph
from .sub_risk import build_risk_subgraph

PRODUCERS = ["k_agent", "r_agent", "c_agent", "b_agent", "p_agent", "clarify", "emergency_draft"]


def build_graph(deps: Deps, checkpointer: Any = None):
    # ── 子图（各自编译一次）──
    knowledge = build_knowledge_subgraph(deps)
    risk = build_risk_subgraph(deps)

    # ── 节点工厂 ──
    N: dict[str, Any] = {}
    N.update(intake.make_intake_nodes(deps))
    N.update(specialists.make_specialist_nodes(deps))
    N.update(aggregate_nodes := {"aggregate": agg.make_aggregate_node(deps)})
    N.update(release.make_release_nodes(deps))
    N.update(revision.make_revision_nodes(deps))
    N.update(exits.make_exit_nodes(deps))
    R = make_routers(deps)
    after_dispatch = intake.make_after_dispatch(deps)

    b = StateGraph(ZhimeiState)

    # ══════════════ 注册节点 ══════════════
    b.add_node("normalize", N["normalize"])
    b.add_node("emergency_screen", N["emergency_screen"])
    b.add_node("classify", N["classify"])
    b.add_node("dispatch", N["dispatch"])

    b.add_node("k_agent", knowledge)          # 子图 A
    b.add_node("r_agent", N["r_agent"])
    b.add_node("c_agent", N["c_agent"])
    b.add_node("b_agent", N["b_agent"])
    b.add_node("p_agent", N["p_agent"])
    b.add_node("clarify", N["clarify"])
    b.add_node("emergency_draft", N["emergency_draft"])

    b.add_node("aggregate", N["aggregate"])
    b.add_node("risk_gate", risk)             # 子图 B（第一次调用）

    b.add_node("output_type", N["output_type"])
    b.add_node("await_confirm", N["await_confirm"])
    b.add_node("execute_op", N["execute_op"])
    b.add_node("receipt", N["receipt"])
    b.add_node("set_result_kind", N["set_result_kind"])
    b.add_node("recheck", risk)               # 子图 B（第二次调用，同一份图）
    b.add_node("final_check", N["final_check"])

    b.add_node("feedback", N["feedback"])
    b.add_node("retry_check", N["retry_check"])
    _add_revise_node(b, N["revise"])          # ← 唯一的动态跳转节点
    b.add_node("plan_void", N["plan_void"])

    b.add_node("send", N["send"])
    b.add_node("human_handoff", N["human_handoff"])
    b.add_node("audit_block", N["audit_block"])

    # ══════════════ 边 ══════════════
    b.add_edge(START, "normalize")
    b.add_edge("normalize", "emergency_screen")
    b.add_conditional_edges("emergency_screen", R["after_emergency_screen"],
                            {"hit": "emergency_draft", "miss": "classify"})
    b.add_edge("classify", "dispatch")
    b.add_conditional_edges("dispatch", after_dispatch,
                            PRODUCERS + ["human_handoff"])

    for n in PRODUCERS:
        b.add_edge(n, "aggregate")
    b.add_edge("plan_void", "aggregate")      # 作废回复同样要过审

    b.add_edge("aggregate", "risk_gate")
    # 不带 path_map：路由函数直接返回节点名（或节点名列表 → 并行出口）
    b.add_conditional_edges("risk_gate", R["after_risk_gate"])

    b.add_edge("feedback", "retry_check")
    b.add_conditional_edges("retry_check", R["after_retry_check"],
                            {"retry": "revise", "exhausted": "human_handoff"})

    b.add_conditional_edges("output_type", R["after_output_type"],
                            {"reply": "send", "operation": "await_confirm"})
    b.add_conditional_edges("await_confirm", R["after_confirm"], {
        "confirmed": "execute_op", "denied": "plan_void", "mismatch": "plan_void"})
    b.add_edge("execute_op", "receipt")
    b.add_edge("receipt", "set_result_kind")
    b.add_edge("set_result_kind", "recheck")
    b.add_edge("recheck", "final_check")
    b.add_conditional_edges("final_check", R["after_final_check"],
                            {"ok": "send", "fail": "human_handoff"})

    b.add_edge("send", END)
    b.add_edge("human_handoff", END)
    b.add_edge("audit_block", END)

    return b.compile(checkpointer=checkpointer, name="zhimei_main")


def _add_revise_node(b: StateGraph, fn: Any) -> None:
    """
    revise 用 Command(goto=...) 动态跳转。
    部分版本需要显式声明 destinations 才能做静态校验，这里做兼容处理。
    """
    try:
        b.add_node("revise", fn, destinations=PRODUCERS)
    except TypeError:
        b.add_node("revise", fn)
