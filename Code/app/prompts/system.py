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


#: 回答类 Prompt 里"此前对话"每条助手消息的截断长度。
#:
#: ★ 为什么要截断助手的话：助手每条回复都很长（免责声明 + 建议 + 追问，常见 300 字以上），
#:   而回答本轮问题时真正需要的是**用户说过什么**。原样塞进去，几条消息就吃掉大半
#:   上下文，模型还容易被自己上一轮的措辞带偏（照着复述一遍）。
#:   用户的话**完整保留** —— 那才是记忆的主体。
HISTORY_ASSISTANT_LIMIT = 160


def render_dialog_history(turns: list[dict[str, Any]],
                          assistant_limit: int = HISTORY_ASSISTANT_LIMIT) -> str:
    """把最近对话渲染成**回答问题时**该看到的形式。

    这里要跟 `render_recent_turns` 分开，不能合并成一个 —— 两者喂给的是
    **两类任务不同**的节点：

      · 理解类（classify）：只需要靠它解析指代（"那个""这个项目"），
        Prompt 里还明确要求"不要从中抽取新信息"。原始文本即可。
      · **回答类（四个专业 Agent + 知识库子图）**：需要把用户此前说过的事实
        **当成已知条件**（"我做的是水光"→后面就不该再问做的是什么），
        还要知道助手已经承诺/追问过什么（免得重复追问同一件事）。
        所以这里把角色写成中文，并且**截断助手的长回复**、保留用户原话。

    ★ 没有这一条的时候，回答类 Prompt 里**完全没有历史** ——
      结果是"用户上一轮说过的事，这一轮写答案时看不见"。
      槽位能兜住一部分（项目/门店/术后天数这类正好有槽位的），
      但"我对利多卡因过敏"这种没有对应槽位的信息就彻底丢了。
    """
    if not turns:
        return "（无，这是本次会话的开头）"
    who = {"user": "用户", "assistant": "助手", "agent": "人工客服", "system": "系统"}
    lines = []
    for t in turns:
        role = t.get("role") or "?"
        text = str(t.get("content") or "").strip()
        if role != "user" and len(text) > assistant_limit:
            text = text[:assistant_limit] + "…（后文略）"
        lines.append(f"{who.get(role, role)}：{text}")
    return "\n".join(lines)


#: 注入到**回答类** Prompt 里的历史块。措辞是关键的一半：
#: 只说"这是历史"模型会当成背景噪音；必须明确"用户说过的是已知条件"，
#: 同时明确"不要复述无关历史"，否则它会把自己上一轮的话再讲一遍。
HISTORY_RULES = """此前对话（仅在与本轮问题相关时使用）：
- 用户自己说过的信息（做过的项目、术后天数、症状、过敏史、门店偏好…）
  一律当作**已知条件**沿用，**不要重复追问**已经问过的事。
- 助手/人工客服说过的话只用于避免自相矛盾与重复，**不要**当成事实依据，
  也不要据此生成引用。
- 不要复述与本轮问题无关的历史。"""


def render_draft(draft: dict[str, Any] | None) -> str:
    if not draft:
        return "（空）"
    return str(draft.get("content", ""))


def render_feedback(feedback: list[str]) -> str:
    if not feedback:
        return "（无）"
    return "\n".join(f"{i + 1}. {f}" for i, f in enumerate(feedback))
