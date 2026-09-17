"""
所有结构化输出契约（Pydantic）。

集中放在一处的原因：这些 schema 同时是「模型输出契约」和「节点之间的数据契约」，
散落在各节点里会导致字段漂移。改这里就等于改接口。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator

Intent = Literal["knowledge_edu", "recommend", "clinic_info", "booking",
                 "postcare", "clarify", "other"]
RiskLevel = Literal["low", "medium", "high"]
ClaimStatus = Literal["supported", "unsupported", "overstated"]


# ══════════════ 意图识别 ══════════════
class Slots(BaseModel):
    #: ★ project 是**数组**而不是单值：一句话里完全可能同时提到多个项目
    #:   （"热玛吉和超声炮有什么区别"）。第一版写成 str | None，
    #:   结果真实模型返回列表 → Pydantic 校验失败 → classify 两次都失败 → 整条链降级。
    #:   这里同时做兼容：模型偶尔也会返回单个字符串。
    project: list[str] = Field(default_factory=list)
    store: str | None = None
    doctor: str | None = None
    datetime: str | None = None
    symptom: str | None = None
    postop_days: int | None = None
    budget: str | None = None

    @field_validator("project", mode="before")
    @classmethod
    def _coerce_project(cls, v: object) -> list[str]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if isinstance(v, (list, tuple, set)):
            return [str(x).strip() for x in v if str(x).strip()]
        return [str(v)]


class ClassifyOut(BaseModel):
    intents: list[Intent] = Field(default_factory=list)
    slots: Slots = Field(default_factory=Slots)
    time_note: str | None = None
    entity_note: str | None = None
    emergency_hint: bool = False
    emergency_terms: list[str] = Field(default_factory=list)
    uncertain: bool = False
    confidence: float = 1.0


class EmergencyTriage(BaseModel):
    """规则层没命中时的兜底判断：只回答「要不要升级」。"""
    escalate: bool = False
    reason: str = ""


# ══════════════ 总控规划（可选路径）══════════════
class SupervisorPlan(BaseModel):
    ordered: list[str] = Field(default_factory=list)
    defer: list[str] = Field(default_factory=list)
    reason: str = ""
    need_clarify: bool = False
    clarify_question: str | None = None
    need_human: bool = False
    human_reason: str | None = None


# ══════════════ 澄清 ══════════════
class ClarifyOut(BaseModel):
    content: str = ""
    why: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)   # project/body_part/recovery/...


# ══════════════ 知识科普子图 ══════════════
class KbQuery(BaseModel):
    sub_task: str = ""
    query_text: str = ""


class KbDecomposeOut(BaseModel):
    queries: list[KbQuery] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    evidence_id: str = ""
    quote: str = ""
    doc_id: str | None = None
    version: str | None = None


def _coerce_bare_ids(v: Any) -> Any:
    """把 `["E1"]` 这种裸字符串收成 `[{"evidence_id": "E1"}]`。

    ★ 为什么会发生：Prompt 里给对话历史之后，模型会看到**上一轮回答**里的 `[E1]`
      标记，于是"顺手"把它填进本轮的 citations。实测报错是
      `citations.0  Input should be an object  input_value='E1'`。

    ★★ 为什么**所有**带 citations 的 schema 都要用它（这里推翻过一个更早的判断）★★
      早先只在 `SpecialistDraftOut` 上加了容忍，理由写的是
      "知识库草稿的 citations 就是'引用必须可溯源'的凭据本身，那里必须严格，
      宁可回炉重写"。**这个判断被生产数据证伪了**：

        真实档位下 `kb_limit`（证据不足的降级路径，代码注释里写着"真实数据下最常走"）
        6 次调用里 4 次失败，全是同一个 `citations.0` 形状问题：
        连试两次不合格 → `LLMError` → **兜底转人工**。
        顾客问一句本该得到"资料不足 + 建议面诊"的话，收到的是"已为您转接人工客服"。

      而且严格在这里**并没有换来它想要的东西**：
        · `kb_limit` 按定义就是"证据不足"，本来就没有可引用的证据，
          一条 `{"evidence_id": "E1"}` 无论如何都溯源不到原文 —— 校验形状拦不住假引用；
        · 真正管"引用可不可溯源"的是下游的 `kb_verify`（逐句核对 claim 与证据），
          它比对的是**证据内容**，跟 pydantic 的形状校验是两件事。
      结论：schema 只负责"能不能解析成结构"，溯源由 `kb_verify` 负责。
      把一件格式小事升级成"转人工"，是在最常走的路径上制造失败。
    """
    if isinstance(v, list):
        return [{"evidence_id": s} if isinstance(s, str) else s for s in v]
    return v


#: 容错后的 citations 字段类型：`list[Citation]`，但先把裸字符串收成对象
CitationList = Annotated[list[Citation], BeforeValidator(_coerce_bare_ids)]


class KbDraftOut(BaseModel):
    content: str = ""
    citations: CitationList = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    used_evidence_ids: list[str] = Field(default_factory=list)


class KbCtxOut(BaseModel):
    """越界问题的"先用上下文答一次"输出。

    ★ content 允许为空，而且**空是常态**：空 = 交给人工（安全默认），
      有内容 = 只用会话里已有的事实回答。判定权交给模型，但默认值是安全的。
    """
    content: str = ""
    reason: str = ""


class ClaimCheck(BaseModel):
    sentence: str = ""
    cited: str | None = None
    status: ClaimStatus = "supported"
    quote: str | None = None
    comment: str = ""


class KbVerifyOut(BaseModel):
    claims: list[ClaimCheck] = Field(default_factory=list)
    all_grounded: bool = True
    must_fix: list[str] = Field(default_factory=list)


class EvidenceOpinion(BaseModel):
    verdict: Literal["sufficient", "insufficient", "conflict"] = "sufficient"
    reason: str = ""


# ══════════════ 风险审查 ══════════════
class Finding(BaseModel):
    span: str = ""
    rule_hint: str = ""
    reason: str = ""
    suggest_fix: str = ""


class PanelOpinion(BaseModel):
    """三位专家（医疗边界 / 广告表达 / 隐私服务）共用的输出契约。"""
    level: RiskLevel = "low"
    risk_tags: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    confidence: float = 1.0
    abstain: bool = False


class EscalationOpinion(PanelOpinion):
    conflicts: list[str] = Field(default_factory=list)


class RevisionFeedbackOut(BaseModel):
    feedback: list[str] = Field(default_factory=list)
    keep: list[str] = Field(default_factory=list)


# ══════════════ 专业 Agent 与出站 ══════════════
class SpecialistDraftOut(BaseModel):
    content: str = ""
    citations: CitationList = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    operation: dict[str, Any] | None = None


class ReceiptOut(BaseModel):
    content: str = ""
    citations: CitationList = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
