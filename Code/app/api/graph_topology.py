"""
工作流**架构**视图：把 `Workflow/langgraph-main.mmd` 那张设计图搬到界面上。

    GET /api/graph  →  {architecture, graph, warnings, ...}

═══════════ 为什么要分"架构"和"拓扑"两层 ═══════════

它们回答的是两个不同的问题：

  · **架构（本文件的 ARCHITECTURE）** —— "这个系统是**怎么设计的**"。
    分层（入口 → 七路草稿 → 必经关卡 → 输出与执行 → 出口）、
    子图挂在哪个节点上、五档出口各是什么。这是**人画出来的设计**，
    所以它必须由人写下来（照 `.mmd` 写），机器推导不出来。

  · **拓扑（`_export`，从编译好的图读）** —— "代码里**实际**长什么样"。
    节点和边直接从 `compiled.get_graph()` 读，不是我抄的。

★ 关键在于**两者必须对得上**，所以这里做一次交叉校验（`validate`）：
  架构里提到的节点如果代码里没有 → 说明设计图过期了；
  代码里有、架构里没提 → 说明有人加了节点却没更新设计图。
  两种情况都会出现在返回值的 `warnings` 里，并由 `check_ui.cjs` 断言为空。

  没有这层校验的话，这张图迟早会变成"看着很专业但和代码不符"的装饰品 ——
  而一张**画错的**架构图比没有图更糟：它会让人对执行过程产生错误判断。
"""

from __future__ import annotations

from typing import Any

#: 端点（虚拟节点）不画
_HIDDEN = {"__start__", "__end__", "START", "END"}


# ══════════════════════════════════════════════════════════════
#  架构定义（照 Workflow/langgraph-main.mmd）
# ══════════════════════════════════════════════════════════════
#: 每一层：自上而下的阅读顺序。`decisions` 里的节点画成菱形（路由点）。
ARCHITECTURE: dict[str, Any] = {
    "layers": [
        {"id": "L0", "title": "入口与调度层",
         "hint": "标准化 → 紧急筛查 → 意图与槽位 → 分派",
         "nodes": ["normalize", "emergency_screen", "classify", "dispatch"]},
        {"id": "L1", "title": "草稿产出层 · 七路并行",
         "hint": "每个 Agent 只产出草稿，都不直接对用户说话",
         "nodes": ["k_agent", "r_agent", "c_agent", "b_agent", "p_agent",
                   "clarify", "emergency_draft"]},
        {"id": "GATE", "title": "聚合与必经关卡",
         "hint": "唯一汇聚点；风险审查子图是**必经**的",
         "nodes": ["aggregate", "risk_gate"]},
        {"id": "OUT", "title": "输出与执行闭环",
         "hint": "操作类请求要用户明确确认后才执行，执行结果还要再送审一次",
         "nodes": ["output_type", "await_confirm", "execute_op", "receipt",
                   "set_result_kind", "recheck", "final_check"]},
        {"id": "LOOP", "title": "修订回路与作废",
         "hint": "按审查意见回到原专业 Agent 重写；预算用尽则转人工",
         "nodes": ["feedback", "retry_check", "revise", "plan_void"]},
        {"id": "EXITS", "title": "出口层 · 所有出站内容都必须持有放行凭据",
         "hint": "send 会校验 release_token，校验不过一律不出站",
         "nodes": ["send", "human_handoff", "audit_block"]},
    ],
    #: 画成菱形的路由点（对应 .mmd 里的 { } 节点）
    "decisions": ["emergency_screen", "dispatch", "risk_gate", "retry_check",
                  "output_type", "final_check"],
    #: 子图：挂在主图的哪个节点上（`hosts`），内部有哪些节点
    "subgraphs": [
        {"id": "risk", "title": "风险审查子图",
         "hint": "硬性规则 + 三份面板意见 → 裁决 → 放行凭据",
         "hosts": ["risk_gate", "recheck"], "entry": "gate_in",
         "nodes": ["gate_in", "review_input", "emergency_check", "hard_rules",
                   "review_medical", "review_ad", "review_privacy", "panel_fanin",
                   "check_op", "biz_check", "merge_verdict", "issue_token",
                   "need_info", "block", "handoff", "escalate_review"]},
        {"id": "kb", "title": "知识科普子图",
         "hint": "检索 → 证据筛选 → 草稿 / 降级 / 追问 → 逐句核对",
         "hosts": ["k_agent"], "entry": "kb_intake",
         "nodes": ["kb_revise_in", "kb_intake", "kb_context", "kb_decompose",
                   "kb_clarify", "kb_ctx_reply", "kb_retrieve", "kb_evidence",
                   "kb_limit", "kb_draft", "kb_verify", "kb_return"]},
    ],
    #: 主图连线（照 .mmd；标签就是路由函数的返回值/分支含义）
    "edges": [
        ("normalize", "emergency_screen", ""),
        ("emergency_screen", "emergency_draft", "命中紧急"),
        ("emergency_screen", "classify", "未命中"),
        ("classify", "dispatch", ""),
        ("dispatch", "k_agent", "科普咨询"),
        ("dispatch", "r_agent", "项目推荐"),
        ("dispatch", "c_agent", "门店资质"),
        ("dispatch", "b_agent", "预约改约"),
        ("dispatch", "p_agent", "术后护理"),
        ("dispatch", "clarify", "意图不明确"),
        ("k_agent", "aggregate", ""),
        ("r_agent", "aggregate", ""),
        ("c_agent", "aggregate", ""),
        ("b_agent", "aggregate", ""),
        ("p_agent", "aggregate", ""),
        ("clarify", "aggregate", ""),
        ("emergency_draft", "aggregate", ""),
        ("aggregate", "risk_gate", ""),
        ("risk_gate", "output_type", "pass 通过"),
        ("risk_gate", "feedback", "revise 需修改"),
        ("risk_gate", "send", "need_info 资料不全"),
        ("risk_gate", "audit_block", "block 硬性阻断"),
        ("risk_gate", "human_handoff", "human 高风险"),
        ("feedback", "retry_check", ""),
        ("retry_check", "revise", "是：计数 +1"),
        ("retry_check", "human_handoff", "否：复审超限"),
        ("revise", "aggregate", "回到原 Agent 重写"),
        ("output_type", "send", "回复"),
        ("output_type", "await_confirm", "操作请求"),
        ("await_confirm", "execute_op", "用户确认"),
        ("await_confirm", "plan_void", "否认 / 过期"),
        ("execute_op", "receipt", ""),
        ("receipt", "set_result_kind", ""),
        ("set_result_kind", "recheck", ""),
        ("recheck", "final_check", ""),
        ("final_check", "send", "是"),
        ("final_check", "human_handoff", "否"),
        ("plan_void", "aggregate", "重新送审"),
    ],
    #: 子图内部关键连线（用于展开子图时画出来；不必穷尽）
    "subgraph_edges": {
        "kb": [("kb_intake", "kb_context", ""), ("kb_context", "kb_decompose", ""),
               ("kb_decompose", "kb_retrieve", "足够"), ("kb_decompose", "kb_clarify", "缺信息"),
               ("kb_retrieve", "kb_evidence", ""), ("kb_evidence", "kb_draft", "证据够"),
               ("kb_evidence", "kb_limit", "证据薄"), ("kb_draft", "kb_verify", ""),
               ("kb_verify", "kb_return", "有依据"), ("kb_verify", "kb_draft", "需改"),
               ("kb_verify", "kb_limit", "预算尽"), ("kb_limit", "kb_return", ""),
               ("kb_clarify", "kb_return", ""), ("kb_ctx_reply", "kb_return", "越界但能答")],
        "risk": [("gate_in", "review_input", ""), ("review_input", "emergency_check", ""),
                 ("review_input", "need_info", "输入不合规"), ("need_info", "kb_return", "直接收口"),
                 ("emergency_check", "hard_rules", ""), ("hard_rules", "review_medical", ""),
                 ("review_medical", "review_ad", ""), ("review_ad", "review_privacy", ""),
                 ("review_privacy", "panel_fanin", ""), ("panel_fanin", "merge_verdict", ""),
                 ("merge_verdict", "check_op", ""), ("check_op", "biz_check", ""),
                 ("biz_check", "issue_token", "放行")],
    },
}


def validate(real_nodes: set[str]) -> list[str]:
    """交叉校验架构与真实图。返回不一致清单（空 = 一致）。"""
    declared: set[str] = set()
    for layer in ARCHITECTURE["layers"]:
        declared |= set(layer["nodes"])
    for sub in ARCHITECTURE["subgraphs"]:
        declared |= set(sub["nodes"]) | set(sub["hosts"])

    warnings: list[str] = []
    missing = sorted(declared - real_nodes)          # 架构里写了、代码里没有
    if missing:
        warnings.append(f"架构里声明了但代码里不存在的节点：{missing}（设计图过期了？）")
    extra = sorted(real_nodes - declared)            # 代码里有、架构没提
    if extra:
        warnings.append(f"代码里有但架构没登记的节点：{extra}（加了节点没更新设计图？）")
    return warnings


def build_topology(runtime: Any) -> dict:
    """导出**架构**（人写的设计）+ **拓扑**（从编译好的图读）+ 两者的一致性。"""
    from ..graph.sub_knowledge import build_knowledge_subgraph
    from ..graph.sub_risk import build_risk_subgraph

    real: set[str] = set()
    sub_nodes: dict[str, list[dict]] = {}
    sub_edges: dict[str, list[dict]] = {}

    def collect(compiled: Any, group: str) -> None:
        g = compiled.get_graph()
        ids = [n for n in getattr(g, "nodes", {}) if n not in _HIDDEN]
        real.update(ids)
        sub_nodes[group] = [{"id": n} for n in ids]
        sub_edges[group] = [
            {"source": e.source, "target": e.target,
             "kind": "conditional" if getattr(e, "conditional", False) else "normal"}
            for e in getattr(g, "edges", [])
            if getattr(e, "source", None) not in _HIDDEN
            and getattr(e, "target", None) not in _HIDDEN
        ]

    collect(runtime.graph, "main")
    for builder, group in ((build_knowledge_subgraph, "kb"), (build_risk_subgraph, "risk")):
        try:
            collect(builder(runtime.deps), group)
        except Exception:  # noqa: BLE001
            # 子图导不出来不该让整张图打不开 —— 架构那一半仍然是有用的
            sub_nodes.setdefault(group, [])
            sub_edges.setdefault(group, [])

    return {
        "architecture": ARCHITECTURE,
        # 真实图（前端用来交叉高亮、以及"有没有漏画的节点"）
        "graph": {"nodes": sorted(real), "sub_nodes": sub_nodes, "sub_edges": sub_edges},
        "warnings": validate(real),
    }
