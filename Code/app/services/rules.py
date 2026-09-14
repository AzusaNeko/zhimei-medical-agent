"""
规则引擎：紧急信号词表、硬性阻断规则、意图关键词兜底、风险标签。

全部规则来自 app/config/rules.yaml，不硬编码。
匹配策略见 docs：判定必须按「子句」做，且要处理四种"看起来像但不是"的情况：
  询问风险 / 否定 / 第三人称 / 假设
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import text as T

#: 等级优先级：取"最高档"时用它排序（数字越小越紧急）
TIER_ORDER = {"P0": 0, "P1": 1, "P2": 2}


@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    rule_version: str
    category: str
    severity: str
    action: str          # revise | block
    title: str
    span: str            # 命中的原文片段

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id, "rule_version": self.rule_version,
            "category": self.category, "severity": self.severity,
            "action": self.action, "title": self.title, "span": self.span,
        }


@dataclass(frozen=True)
class EmergencyHit:
    tier: str            # P0 | P1 | P2
    cluster: str
    term: str
    clause: str

    def as_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "cluster": self.cluster, "term": self.term, "clause": self.clause}


class RuleEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.cfg = config
        self.version = str(config.get("version", "0"))

        em = config.get("emergency", {})
        self.em_version = str(em.get("version", "0"))
        self.generic_terms = em.get("generic_terms", [])
        self.intensity_terms = em.get("intensity_terms", [])
        self.body_terms = em.get("body_terms", [])
        self.question_markers = em.get("question_markers", [])
        self.negation_markers = em.get("negation_markers", [])
        self.third_person_markers = em.get("third_person_markers", [])
        self.emergency_tiers = {
            "P0": em.get("p0", []), "P1": em.get("p1", []), "P2": em.get("p2", []),
        }

        hr = config.get("hard_rules", {})
        self.hard_version = str(hr.get("version", "0"))
        self.repeat_threshold = int(hr.get("repeat_escalate_threshold", 2))
        self.hard_rules = hr.get("rules", [])
        # 预编译正则
        self._compiled: dict[str, list[re.Pattern]] = {}
        for rule in self.hard_rules:
            if rule.get("check") == "regex":
                self._compiled[rule["id"]] = [re.compile(p) for p in rule.get("patterns", [])]

        self.routing: dict[str, str] = config.get("routing", {})
        self.intent_priority: list[str] = config.get("intent_priority", [])
        self.max_fanout: int = int(config.get("max_fanout", 3))
        self.risk_tags: dict[str, list[dict]] = config.get("risk_tags", {})
        self.clarify: dict[str, Any] = config.get("clarify", {})
        self.entities: dict[str, list[str]] = config.get("entities", {})

        # 意图关键词兜底表（模型挂掉时用）
        self._keyword_route = {
            "booking": ["预约", "改约", "取消", "改时间", "约一下", "挂号"],
            "postcare": ["术后", "做完", "恢复", "肿", "疼", "护理", "第几天"],
            "clinic_info": ["门店", "地址", "营业", "医生", "资质", "在哪儿", "电话"],
            "recommend": ["适合", "推荐", "建议做", "选哪个"],
            "knowledge_edu": ["区别", "原理", "是什么", "多久", "维持", "多少钱", "效果"],
        }

    # ══════════════ 加载 ══════════════
    @classmethod
    def from_file(cls, path: str | Path) -> "RuleEngine":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"规则文件格式不正确：{path}")
        return cls(data)

    # ══════════════ 紧急信号 ══════════════
    def match_emergency(self, raw_text: str) -> list[EmergencyHit]:
        """
        按子句判定，返回命中的最高优先级信号（P0 命中即返回 P0）。

        四种抑制规则（见 rules.yaml 的注释）：
          · 询问风险：疑问词出现在症状词【之前】→ 不是自述症状
          · 否定：否定词紧邻症状词之前 → 不算
          · 第三人称/假设：出现在症状词之前 → 不算
          · 泛词不单独触发：疼/肿/红 必须与程度词或部位词共现
        """
        hits: list[EmergencyHit] = []
        for clause in T.split_clauses(raw_text):
            # ① 泛词（疼/肿/红…）：单独出现不算，必须与程度词或部位词共现 → P1
            for term, idx in T.find_terms(clause, self.generic_terms):
                if self._suppressed(clause, term, idx):
                    continue
                if not self._has_cooccurrence(clause):
                    continue
                hits.append(EmergencyHit("P1", "generic_amplified", term, clause))
            # ② 分级词表
            for tier in ("P0", "P1", "P2"):
                for group in self.emergency_tiers.get(tier, []):
                    for term, idx in T.find_terms(clause, group.get("terms", [])):
                        if self._suppressed(clause, term, idx):
                            continue
                        hits.append(EmergencyHit(tier, group.get("cluster", ""), term, clause))
        # 去重（同一个词可能在多个子句出现）
        seen, out = set(), []
        for h in hits:
            key = (h.tier, h.term)
            if key not in seen:
                seen.add(key)
                out.append(h)
        # ★ 按等级排序：调用方普遍取"最高档"，如果顺序随机会出现
        #   "命中了 P0 却按 P1 处理"的严重漏判
        return sorted(out, key=lambda h: TIER_ORDER.get(h.tier, 9))

    def _suppressed(self, clause: str, term: str, idx: int) -> bool:
        # 否定：紧邻在词前（最多 3 字窗口）
        prefix = clause[max(0, idx - 3):idx]
        if any(prefix.endswith(m) for m in self.negation_markers):
            return True
        # 询问 / 第三人称 / 假设：出现在症状词之前才算"不是自述"
        for marker in self.question_markers + self.third_person_markers:
            m_idx = clause.find(marker)
            if 0 <= m_idx < idx:
                return True
        return False

    def _has_cooccurrence(self, clause: str) -> bool:
        return any(t in clause for t in self.intensity_terms) or any(t in clause for t in self.body_terms)

    def emergency_tier(self, hits: list[EmergencyHit]) -> str | None:
        tiers = {h.tier for h in hits}
        for t in ("P0", "P1", "P2"):
            if t in tiers:
                return t
        return None

    # ══════════════ 硬性阻断规则 ══════════════
    def check_text(self, content: str, *, prior_hits: list[dict] | None = None) -> list[RuleHit]:
        """
        只检查 literal / regex 类规则（ops / semantic 类由代码在别处判定）。
        prior_hits：之前轮次的命中记录，用于"重复命中升级为 block"。
        """
        hits: list[RuleHit] = []
        prior_counts: dict[str, int] = {}
        for h in prior_hits or []:
            prior_counts[h.get("rule_id", "")] = prior_counts.get(h.get("rule_id", ""), 0) + 1

        for rule in self.hard_rules:
            check = rule.get("check")
            if check not in ("literal", "regex"):
                continue
            span = ""
            if check == "literal":
                for p in rule.get("patterns", []):
                    if p and p in content:
                        span = p
                        break
            else:
                for pat in self._compiled.get(rule["id"], []):
                    m = pat.search(content)
                    if m:
                        span = m.group()[:40]
                        break
            if not span:
                continue

            action = rule.get("action", "revise")
            # 同一规则重复命中 → 升级为 block（说明改写解决不了问题）
            if action == "revise" and prior_counts.get(rule["id"], 0) + 1 >= self.repeat_threshold:
                action = "block"

            hits.append(RuleHit(
                rule_id=rule["id"], rule_version=self.hard_version,
                category=rule.get("category", ""), severity=rule.get("severity", "medium"),
                action=action, title=rule.get("title", ""), span=span,
            ))
        return hits

    def has_block(self, hits: list[RuleHit]) -> bool:
        return any(h.action == "block" for h in hits)

    # ══════════════ 风险标签 ══════════════
    def tags_from_text(self, content: str) -> list[str]:
        out: list[str] = []
        for level in ("HIGH", "MEDIUM", "LOW"):
            for entry in self.risk_tags.get(level, []):
                for kw in entry.get("keywords", []):
                    if kw and kw in content:
                        out.append(entry["tag"])
                        break
        return T.dedupe(out)

    def level_of_tag(self, tag: str) -> str | None:
        for level in ("HIGH", "MEDIUM", "LOW"):
            for entry in self.risk_tags.get(level, []):
                if entry["tag"] == tag:
                    return level
        return None

    @staticmethod
    def has_high_risk_tag(tags: list[str]) -> bool:
        high = {
            "complication_signal", "diagnosis_request", "medication_advice", "refund_dispute",
            "legal_threat", "minor", "pregnant_or_lactating", "comorbidity",
            "unauthorized_provider", "privacy_leak", "self_harm", "professional_claim",
        }
        return bool(set(tags) & high)

    @staticmethod
    def count_medium_risk_tag(tags: list[str]) -> int:
        medium = {
            "efficacy_guarantee", "absolute_wording", "comparative_ad", "anxiety_induce",
            "case_evidence", "missing_disclaimer", "vague_price",
        }
        return len(set(tags) & medium)

    # ══════════════ 意图关键词兜底 ══════════════
    def keyword_route(self, raw_text: str) -> list[str]:
        found = [intent for intent, kws in self._keyword_route.items() if any(k in raw_text for k in kws)]
        return self.resolve_intents(found)

    # ══════════════ 多意图冲突消解（纯规则，可单测）══════════════
    def resolve_intents(self, intents: list[str]) -> list[str]:
        s = [i for i in T.dedupe(intents) if i not in ("clarify", "other") and i in self.routing]
        # 术后场景优先于科普，避免科普与护理建议互相打架
        if "postcare" in s and "knowledge_edu" in s:
            s.remove("knowledge_edu")
        # 有明确操作意图时先完成操作，其余下一轮
        if "booking" in s and ("postcare" in s or "recommend" in s):
            s = ["booking"] + [i for i in s if i == "knowledge_edu"]
        order = {name: i for i, name in enumerate(self.intent_priority)}
        return sorted(s, key=lambda i: order.get(i, 99))[: self.max_fanout]

    def targets_for(self, intents: list[str]) -> list[str]:
        return T.dedupe([self.routing[i] for i in intents if i in self.routing])

    # ══════════════ 澄清模板 ══════════════
    def clarify_text(self, missing: list[str]) -> str | None:
        parts = [self.clarify.get("templates", {}).get(k) for k in missing]
        parts = [p for p in parts if p][:3]
        if not parts:
            return None
        body = "".join(f"{i + 1}）{p}\n" for i, p in enumerate(parts))
        return f"为了给您更贴切的说明，想先确认几点：\n{body}{self.clarify.get('disclaimer', '')}"
