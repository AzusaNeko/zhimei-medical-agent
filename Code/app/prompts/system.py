"""Prompt 通用约定：每个 system prompt 都以 BASE_RULES 开头，并附渲染工具。"""

from __future__ import annotations

from typing import Any

BASE_RULES = """
【角色边界】
- 你是机构客服体系中的一个具体岗位，不是"AI 助手"。不要自我介绍，不要寒暄。
- 你不做诊断、不给用药建议、不判断病情严重程度。涉及这些一律建议面诊或转人工。

【事实纪律】
- 只能使用我提供的 <evidence> 内容作答。禁止用你的记忆或常识补充医学事实。
- 每一句涉及原理、流程、恢复期、风险的表述，都必须绑定一个 evidence id。
- 没有依据就说不确定并把缺口写进 gaps。编造比答不出来严重得多。

【表达纪律】
- 不承诺疗效（保证/根治/一次见效/永久），不使用绝对化用语（最好/最安全/全网最低）。
- 价格、资质、档期只能引用业务系统返回值，不得估算。
- 必须保留限制条件与"具体以医生面诊评估为准"类提示。

【输出纪律】
- 只输出 JSON，不要输出任何解释性文字，不要 markdown 代码块标记。
- 不确定时使用 false / null，不要猜一个看起来合理的值。
""".strip()


def render_evidence(evidence: list[dict[str, Any]]) -> str:
    """证据块：形如 [E1] doc=DOC-1 version=v1 text=..."""
    if not evidence:
        return "（无可用证据）"
    lines = []
    for e in evidence:
        lines.append(
            f'[{e.get("evidence_id", "?")}] doc={e.get("doc_id", "?")} '
            f'version={e.get("version", "?")} type={e.get("doc_type", "")} '
            f'score={e.get("score", 0):.3f}\n{e.get("text", "")}'
        )
    return "\n\n".join(lines)


def render_slots(slots: dict[str, Any] | None) -> str:
    if not slots:
        return "（无）"
    items = [f"{k}={v}" for k, v in slots.items() if v]
    return "、".join(items) if items else "（无）"


def render_recent_turns(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return "（无）"
    return "\n".join(f'{t.get("role", "?")}: {t.get("content", "")}' for t in turns)


def render_draft(draft: dict[str, Any] | None) -> str:
    if not draft:
        return "（空）"
    return str(draft.get("content", ""))


def render_feedback(feedback: list[str]) -> str:
    if not feedback:
        return "（无）"
    return "\n".join(f"{i + 1}. {f}" for i, f in enumerate(feedback))
