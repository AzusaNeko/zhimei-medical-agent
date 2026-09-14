"""
所有结构化输出契约（Pydantic）。

集中放在一处的原因：这些 schema 同时是「模型输出契约」和「节点之间的数据契约」，
散落在各节点里会导致字段漂移。改这里就等于改接口。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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


class KbDraftOut(BaseModel):
    content: str = ""
    citations: list[Citation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    used_evidence_ids: list[str] = Field(default_factory=list)


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
    citations: list[Citation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    operation: dict[str, Any] | None = None


class ReceiptOut(BaseModel):
    content: str = ""
    citations: list[Citation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
