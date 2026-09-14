"""
内存假实现（--profile fake 与单元测试用）。

定位：**只用于还没配 API key 时验证图接线、路由、预算与凭据**，
不参与生产路径。真实实现在 encoder.py / reranker.py / milvus_store.py / pg.py。

假实现必须遵守与真实实现完全相同的接口契约，否则自检就没有意义。
"""

from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

CST = timezone(timedelta(hours=8))

DEMO_DOCS: list[dict[str, Any]] = [
    {"doc_id": "DOC-1", "title": "热玛吉项目说明", "project": "热玛吉", "doc_type": "项目资料",
     "version": "v1", "text": "热玛吉通过射频能量作用于真皮层，帮助改善皮肤紧致度。治疗过程通常 60–90 分钟。"},
    {"doc_id": "DOC-2", "title": "热玛吉恢复期指引", "project": "热玛吉", "doc_type": "护理指南",
     "version": "v1", "text": "恢复期通常 3–7 天，可能出现轻微红肿。效果因人而异，维持时间有个体差异。"},
    {"doc_id": "DOC-3", "title": "超声炮项目说明", "project": "超声炮", "doc_type": "项目资料",
     "version": "v1", "text": "超声炮利用聚焦超声作用于更深层筋膜层，主要用于下颌轮廓与面部提升。"},
    {"doc_id": "DOC-4", "title": "水光针术后护理", "project": "水光针", "doc_type": "护理指南",
     "version": "v1", "text": "水光针术后 24 小时内避免化妆与剧烈运动，注意补水与防晒。"},
    {"doc_id": "DOC-5", "title": "通用风险提示", "project": None, "doc_type": "风险提示",
     "version": "v1", "text": "任何医美项目都存在个体差异，具体方案需医生面诊评估后确定。"},
]


class FakeEncoder:
    """确定性哈希向量：同样的文本永远得到同样的向量，便于断言。"""

    dim = 64

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls = 0

    def _vec(self, s: str) -> list[float]:
        h = hashlib.sha256(s.encode("utf-8")).digest()
        raw = [(b - 128) / 128.0 for b in h[: self.dim]]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]

    async def encode(self, texts: list[str]) -> dict[str, list]:
        self.calls += 1
        dense = [self._vec(t) for t in texts]
        sparse = [{str(i): 1.0 for i, ch in enumerate(t) if ch.strip()} for t in texts]
        return {"dense": dense, "sparse": sparse}


class FakeReranker:
    """按词重合度打分，保证「问题与文档越相关分越高」这一单调性。"""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls = 0

    async def score(self, query: str, docs: list[str]) -> list[float]:
        self.calls += 1
        q = set(query)
        out = []
        for d in docs:
            overlap = len(q & set(d))
            out.append(min(0.99, 0.30 + overlap * 0.02))
        return out


class FakeVectorStore:
    """内存检索：dense 余弦 + 关键词命中，模拟 Milvus 的混合检索语义。"""

    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = docs if docs is not None else DEMO_DOCS
        self._enc = FakeEncoder()

    async def search(self, *, query_text: str, query_dense: list[float] | None = None,
                     query_sparse: dict | None = None, projects: list[str] | None = None,
                     top_k: int = 40, doc_type: str | None = None) -> list[dict[str, Any]]:
        """签名与 MilvusHybridStore.search 保持一致（dense/sparse 在假实现里不参与打分）。"""
        needle = set(query_text)
        hits = []
        for d in self.docs:
            if projects and d.get("project") and d["project"] not in projects:
                continue
            if doc_type and d.get("doc_type") != doc_type:
                continue
            score = len(needle & set(d["text"])) / max(1, len(needle))
            hits.append({
                "doc_id": d["doc_id"], "title": d["title"], "version": d["version"],
                "doc_type": d["doc_type"], "text": d["text"], "score": round(score, 4),
            })
        hits.sort(key=lambda x: -x["score"])
        return hits[:top_k]

    async def close(self) -> None:
        return None


class FakePg:
    """内存版业务库。字段与 sql/schema.sql 对齐，便于将来替换成真实查询。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.audits: list[dict] = []
        self.rule_hits: list[dict] = []
        self.op_results: dict[str, dict] = {}
        self.confirmations: list[dict] = []
        self.tickets: list[dict] = []
        self.handoff_events: list[dict] = []
        self.misreports: list[dict] = []
        self.sessions: dict[str, dict] = {}
        self.appointments: dict[str, dict] = {
            "AP-1001": {"appointment_id": "AP-1001", "status": "booked", "version": 1,
                        "project": "热玛吉", "store": "浦东店",
                        "datetime": "2025-03-19T10:00:00+08:00", "fee_cents": 8000},
        }

    # ── 会话与权限 ──
    async def get_session(self, session_id: str) -> dict:
        return self.sessions.setdefault(session_id, {
            "session_id": session_id, "user_id": "U-0001", "ai_enabled": True,
            "emergency": False, "status": "active", "thread_id": session_id,
        })

    async def load_auth(self, session_id: str) -> dict:
        return {"verified": True, "user_id": "U-0001",
                "scopes": ["profile", "booking"],
                "data_consents": ["profile"]}

    async def save_message(self, *, session_id: str, turn_id: str | None, role: str,
                           content: str, content_hash: str | None = None,
                           release_token: str | None = None, risk_level: str | None = None,
                           review_kind: str | None = None, meta: dict | None = None) -> int:
        self.messages.append({
            "session_id": session_id, "turn_id": turn_id, "role": role, "content": content,
            "content_hash": content_hash, "release_token": release_token,
            "risk_level": risk_level, "review_kind": review_kind, "meta": meta or {},
        })
        return len(self.messages)

    async def recent_turns(self, session_id: str, limit: int = 5) -> list[dict]:
        rows = [m for m in self.messages if m["session_id"] == session_id]
        return rows[-limit:]

    # ── 审计 ──
    async def write_audit(self, **kw: Any) -> int:
        self.audits.append(kw)
        return len(self.audits)

    async def write_hard_rule_hits(self, hits: list[dict]) -> None:
        self.rule_hits.extend(hits)

    async def note_same_family_review(self, thread_id: str) -> None:
        self.audits.append({"thread_id": thread_id, "event": "same_family_review"})

    # ── 操作 ──
    async def op_already_done(self, key: str) -> bool:
        return key in self.op_results

    async def load_op_result(self, key: str) -> dict:
        return self.op_results[key]

    async def save_op_result(self, key: str, *, session_id: str, action: str,
                            params: dict, result: dict) -> None:
        self.op_results[key] = result

    async def save_confirmation(self, *, session_id: str, plan_hash: str,
                                confirmed: bool, raw_reply: str) -> None:
        self.confirmations.append({"session_id": session_id, "plan_hash": plan_hash,
                                   "confirmed": confirmed, "raw_reply": raw_reply})

    # ── 工单 ──
    async def create_handoff_ticket(self, *, session_id: str, thread_id: str, user_id: str | None,
                                    reason: str, priority: str, profile_summary: str,
                                    last_turns: list[dict], risk_report: dict) -> dict:
        ticket = {"ticket_id": str(uuid.uuid4()), "session_id": session_id,
                  "thread_id": thread_id, "user_id": user_id, "reason": reason,
                  "priority": priority, "status": "open", "profile_summary": profile_summary,
                  "last_turns": last_turns, "risk_report": risk_report,
                  "accepted_at": None}
        self.tickets.append(ticket)
        return ticket

    # ── 画像 ──
    async def load_profile(self, user_id: str | None) -> dict:
        return {"user_id": user_id, "summary": "女性，关注面部紧致；曾在浦东店做过水光针。",
                "preferences": {"store": "浦东店"}, "contraindications": []}

    # ── 业务（MVP 用本地表模拟；上线替换为机构业务系统接口）──
    async def query_slots(self, *, store: str, project: str, around: str | None) -> list[dict]:
        base = datetime(2025, 3, 21, 14, 0, tzinfo=CST)
        return [{"slot_id": 101, "store": store, "project": project,
                 "start_at": base.isoformat(), "doctor": "张医生"}]

    async def get_appointment(self, appointment_id: str) -> dict | None:
        return self.appointments.get(appointment_id)

    async def find_upcoming_appointment(self, user_id: str,
                                        projects: list[str] | None = None) -> dict | None:
        """★ 注意：这里**故意不模拟 status 流转**（改约后仍能查到），
        因为 fake 档位的职责是验证"图的接线"，不是复现机构的业务状态机 ——
        冒烟脚本要让同一条预约能被连续改约三次来测"确认/否认/哈希不符"。
        真实档位（PgStore）才是会过滤 status='booked' 的那个。"""
        for a in self.appointments.values():
            if projects and a.get("project") not in projects:
                continue
            return {"appointment_id": a["appointment_id"], "status": a.get("status", "booked"),
                    "version": int(a.get("version", 1)), "project": a.get("project"),
                    "fee_cents": int(a.get("fee_cents", 0)),
                    "datetime": a.get("datetime"), "store": a.get("store")}
        return None

    async def change_appointment(self, *, store: str, datetime_: str, request_id: str,
                                 appointment_id: str | None = None,
                                 expected_version: int | None = None) -> dict:
        # 与 PgStore 保持同样的入参约束，否则 fake 档位会把真实档位才有的问题藏起来
        if not appointment_id:
            raise ValueError("改约缺少 appointment_id：无法确定要修改哪一条预约")
        appt = self.appointments.get(appointment_id)
        if appt is None:
            raise ValueError(f"改约失败：预约 {appointment_id} 不存在")
        if expected_version is None:
            raise ValueError("改约缺少 expected_version：乐观锁必须有比对的基准版本")
        if int(appt.get("version", 1)) != int(expected_version):
            raise RuntimeError("改约失败：该预约状态或版本已变化（可能已被改约或取消），请刷新后重试")
        appt["version"] = int(appt.get("version", 1)) + 1
        return {"status": "success", "appointment_id": appointment_id, "store": store,
                "datetime": datetime_, "fee_cents": appt.get("fee_cents", 0), "request_id": request_id}

    async def query_clinic_info(self, *, store: str | None, doctor: str | None) -> dict:
        return {"store": store or "浦东店", "address": "上海市浦东新区示例路 1 号",
                "phone": "021-0000-0000", "doctor": doctor or "张医生",
                "credential": "执业医师资格 有效"}

    # ══════════════ 运营后台（与 PgStore 保持同一接口，否则自检就没意义）══════════════
    async def list_tickets(self, *, statuses: list[str] | None = None,
                           priorities: list[str] | None = None,
                           limit: int = 50) -> list[dict]:
        rows = [t for t in self.tickets
                if (not statuses or t["status"] in statuses)
                and (not priorities or t["priority"] in priorities)]
        order = {"P0": 0, "P1": 1, "P2": 2}
        rows.sort(key=lambda t: order.get(t["priority"], 9))
        return rows[:limit]

    async def get_ticket(self, ticket_id: str) -> dict | None:
        return next((t for t in self.tickets if t["ticket_id"] == ticket_id), None)

    async def update_ticket(self, ticket_id: str, **fields: Any) -> dict | None:
        ticket = await self.get_ticket(ticket_id)
        if ticket is None:
            return None
        ticket.update(fields)
        return ticket

    async def list_handoff_events(self, ticket_id: str) -> list[dict]:
        return [e for e in self.handoff_events if e["ticket_id"] == ticket_id]

    async def log_handoff_event(self, ticket_id: str, action: str, actor: str,
                                payload: dict | None = None) -> None:
        self.handoff_events.append({"ticket_id": ticket_id, "action": action,
                                    "actor": actor, "payload": payload or {}})

    async def set_ai_enabled(self, session_id: str, enabled: bool) -> None:
        session = await self.get_session(session_id)
        session["ai_enabled"] = enabled
        session["status"] = "active" if enabled else "handoff"

    async def list_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = [m for m in self.messages if m["session_id"] == session_id]
        return rows[-limit:]

    async def save_agent_message(self, *, session_id: str, agent_id: str,
                                 content: str, ticket_id: str | None = None) -> int:
        self.messages.append({"session_id": session_id, "turn_id": None, "role": "agent",
                              "content": content, "content_hash": None, "release_token": None,
                              "risk_level": None, "review_kind": "agent_reply",
                              "meta": {"agent_id": agent_id, "ticket_id": ticket_id}})
        return len(self.messages)

    async def save_misreport(self, *, ticket_id: str | None, source: str, ref_id: str,
                             raw_message: str, verdict: str, note: str = "") -> None:
        self.misreports.append({"ticket_id": ticket_id, "source": source, "ref_id": ref_id,
                                "raw_message": raw_message, "verdict": verdict, "note": note})

    async def ops_metrics(self) -> dict:
        open_tickets = [t for t in self.tickets
                        if t["status"] in ("open", "accepted", "in_progress", "escalated")]
        by_rule: dict[str, int] = {}
        for hit in self.rule_hits:
            by_rule[hit["rule_id"]] = by_rule.get(hit["rule_id"], 0) + 1
        return {
            "tickets_total": len(self.tickets),
            "tickets_open": len(open_tickets),
            "tickets_p0_open": len([t for t in open_tickets if t["priority"] == "P0"]),
            "tickets_accepted": len([t for t in self.tickets if t.get("accepted_at")]),
            "review_rounds": len(self.audits),
            "first_pass_rate": None,
            "hard_rule_top": sorted(by_rule.items(), key=lambda x: -x[1])[:10],
            "misreport_pending": len(self.misreports),
            "escalation_same_family": len([a for a in self.audits
                                           if a.get("escalation_used")
                                           and not a.get("escalation_independent")]),
            "avg_wait_seconds": None,
        }

    async def close(self) -> None:
        return None
