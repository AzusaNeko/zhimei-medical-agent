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
    citations: list[Citation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    operation: dict[str, Any] | None = None

    @field_validator("citations", mode="before")
    @classmethod
    def _coerce_bare_ids(cls, v: Any) -> Any:
        """把 `["E1"]` 这种裸字符串收成 `[{"evidence_id": "E1"}]`。

        ★ 为什么会发生：Prompt 里给对话历史之后，模型会看到**上一轮回答**里的
          `[E1]` 标记，于是它"顺手"把 E1 填进了本轮的 citations。
          实测（`scripts/multi_turn_cases.py M1`）正是这样：
          `citations.0  Input should be an object  input_value='E1'`
          —— 连试两次都不合格 → `LLMError` → 兜底转人工。
          顾客问了一句"那个更适合我？"，收到的是"已为您转接人工客服"，
          而**原因只是一个引用编号的写法**。

        ★ 为什么在这里容忍、而在 `KbDraftOut` 里不容忍：
          专业 Agent 的 citations **不是溯源凭据**（它们本来就没有知识库证据，
          模板里一直是空数组），为一个格式小事把整轮变成转人工不成比例。
          而知识库草稿的 citations **就是**"引用必须可溯源"的凭据本身，
          少一个 doc_id 就不能定位到原文 —— 那里必须严格，宁可回炉重写。
        """
        if isinstance(v, list):
            return [{"evidence_id": s} if isinstance(s, str) else s for s in v]
        return v


class ReceiptOut(BaseModel):
    content: str = ""
    citations: list[Citation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
