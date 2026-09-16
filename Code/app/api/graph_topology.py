"""
工作流拓扑：把**编译好的图**原样导出来给前端画。

    GET /api/graph  →  {nodes: [...], edges: [...], groups: {...}}

═══════════ 为什么不把拓扑写死在前端 ═══════════

前端那份「节点 → 中文名」表（`chat.html` 的 `NODES`）已经是手写的，
而它已经漂移过一次、需要靠 `check_ui.cjs` 的 B 组静态比对来兜。
再手写一份"连线和布局"进去，就是第二处必然漂移的副本：
图里加了节点、前端没跟上，画出来的工作流图会**静默地少一条边** ——
而一张少了边的流程图比没有图更糟，它会让人对执行过程产生错误的判断。

所以这里从 `StateGraph` 编译后的对象上**直接读**节点和边：
`compiled.get_graph()` 给的就是真实的拓扑。前端只负责画。

★ 分层（group）仍然是人工标注的：图结构里没有"这属于哪个子图"这种信息。
  但标注错了只是**配色不对**，不会像少一条边那样误导人 —— 这个代价可以接受。
"""

from __future__ import annotations

from typing import Any

#: 节点 → 分组。只标子图与少数关键层，其余归 main。
GROUP_OF: dict[str, str] = {
    # 知识科普子图（作为主图的一个节点 k_agent 挂进来）
    "kb_revise_in": "kb", "kb_intake": "kb", "kb_context": "kb", "kb_decompose": "kb",
    "kb_clarify": "kb", "kb_ctx_reply": "kb", "kb_retrieve": "kb", "kb_evidence": "kb",
    "kb_limit": "kb", "kb_draft": "kb", "kb_verify": "kb", "kb_return": "kb",
    # 风险审查子图（同一份图被 add_node 两次：risk_gate / recheck）
    "gate_in": "risk", "review_input": "risk", "emergency_check": "risk",
    "hard_rules": "risk", "review_medical": "risk", "review_ad": "risk",
    "review_privacy": "risk", "panel_fanin": "risk", "check_op": "risk",
    "biz_check": "risk", "merge_verdict": "risk", "issue_token": "risk",
}

#: 子图在主图里的挂载点：主图节点 → 子图入口。
#: ★ 这不是装饰：它让前端知道"点开 k_agent 就是走进知识库子图"，
#:   而这层关系在图结构里是看不出来的（子图被当成一个黑盒节点）。
SUBGRAPH_ENTRY: dict[str, str] = {"k_agent": "kb_intake", "risk_gate": "gate_in",
                                  "recheck": "gate_in"}

#: 这些端点在 LangGraph 的导出里是虚拟节点，不该画出来。
_HIDDEN = {"__start__", "__end__", "START", "END"}

#: groups 的中文名与配色（前端用它画图例）。
GROUP_META: dict[str, dict[str, str]] = {
    "main": {"label": "主图（意图 → 路由 → 汇合 → 出站）", "color": "#17405f"},
    "kb": {"label": "知识科普子图（检索 → 证据 → 草稿 → 核对）", "color": "#1a5044"},
    "risk": {"label": "风险审查子图（规则 + 三份面板意见 → 放行凭据）", "color": "#8a2a1e"},
}


def _edge_kind(edge: Any) -> str:
    """边的种类：普通 / 条件。

    ★ 区分它们不是为了好看：条件边意味着"这里会分叉"，
      画成一样的线会让人以为流程是线性的，而这张图的价值恰恰在于
      看清"哪一步开始分叉、走的是哪一支"。
    """
    return "conditional" if getattr(edge, "conditional", False) else "normal"


def _export(compiled: Any, *, group_default: str) -> tuple[list[dict], list[dict]]:
    g = compiled.get_graph()
    nodes = []
    for nid, node in getattr(g, "nodes", {}).items():
        if nid in _HIDDEN:
            continue
        nodes.append({"id": nid, "group": GROUP_OF.get(nid, group_default)})
    edges = []
    for e in getattr(g, "edges", []):
        src, dst = getattr(e, "source", None), getattr(e, "target", None)
        if not src or not dst or src in _HIDDEN or dst in _HIDDEN:
            continue
        edges.append({"source": src, "target": dst, "kind": _edge_kind(e)})
    return nodes, edges


def build_topology(runtime: Any) -> dict:
    """导出主图 + 两个子图的拓扑（节点去重、边去重）。

    子图要单独导出，是因为它们是以**黑盒节点**的身份挂进主图的：
    只看主图，`k_agent` 后面是什么完全看不到 —— 而用户最想看的恰恰是那里。
    这与 trace 里能直接看到 `kb_*` 节点是一致的（子图内部节点会冒泡上来）。
    """
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}

    main_nodes, main_edges = _export(runtime.graph, group_default="main")
    for n in main_nodes:
        nodes[n["id"]] = n
    for e in main_edges:
        edges[(e["source"], e["target"])] = e

    from ..graph.sub_knowledge import build_knowledge_subgraph
    from ..graph.sub_risk import build_risk_subgraph

    for builder, group in ((build_knowledge_subgraph, "kb"), (build_risk_subgraph, "risk")):
        try:
            sub_nodes, sub_edges = _export(builder(runtime.deps), group_default=group)
        except Exception:  # noqa: BLE001
            # 子图导不出来不该让整张图打不开 —— 主图仍然是有用的
            continue
        for n in sub_nodes:
            nodes.setdefault(n["id"], n)
        for e in sub_edges:
            edges.setdefault((e["source"], e["target"]), e)

    # ★ 把挂载点连起来（主图节点 → 子图入口）。不补这几条边，两个子图在图上
    #   就是**孤岛**：主图里 k_agent 的出口靠条件边回去，而子图内部的节点
    #   谁也连不上谁 —— 一张断开的流程图比没有图更让人误解。
    for host, entry in SUBGRAPH_ENTRY.items():
        if host in nodes and entry in nodes:
            edges.setdefault((host, entry), {"source": host, "target": entry,
                                             "kind": "subgraph"})

    return {
        "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
        "edges": sorted(edges.values(), key=lambda e: (e["source"], e["target"])),
        "groups": GROUP_META,
        # 子图挂载点：前端据此把 k_agent 与 KB 子图连起来
        "subgraph_entry": SUBGRAPH_ENTRY,
        # ★ 如实标注一件事：条件边的**分支目标**只有在代码里声明过
        #   （`add_conditional_edges(..., {..})` 或 `add_node(..., destinations=)`）
        #   才会出现在导出里。没声明的分支在这张图上会显示成"走到头了"。
        #   前端据此在界面上说明一句，而不是让人以为流程真的断了。
        "note": "条件分支的目标由代码声明决定；未声明的分支不会出现在连线里。"
                "实际执行路径以右侧轨迹为准（图上会实时高亮）",
    }
