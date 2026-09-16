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
        #: 认证：C 端用户与坐席（与真实库同形）
        self.users: dict[str, dict] = {}
        self.agents: dict[str, dict] = {}
        #: 模型调用日志（与 PgStore.log_llm_call 同形）——
        #: 放在 fake 里是为了让"日志链路是否接对"在不需要 Postgres 时也能验证
        self.llm_calls: list[dict] = []
        self.appointments: dict[str, dict] = {
            "AP-1001": {"appointment_id": "AP-1001", "status": "booked", "version": 1,
                        "project": "热玛吉", "store": "浦东店",
                        "datetime": "2025-03-19T10:00:00+08:00", "fee_cents": 8000},
        }

    # ── 会话与权限 ──
    async def log_llm_call(self, *, thread_id: str | None, role: str, model: str,
                           latency_ms: int, prompt_tokens: int | None,
                           completion_tokens: int | None, ok: bool,
                           error: str | None = None) -> None:
        self.llm_calls.append({
            "thread_id": thread_id, "role": role, "model": model,
            "latency_ms": latency_ms, "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens, "ok": ok, "error": error,
        })

    async def ensure_session(self, session_id: str, *, channel: str = "cli",
                             user_id: str | None = None) -> None:
        """建会话行（幂等），与 PgStore.ensure_session 同形。

        ★ 这个方法原来在 fake 里**根本不存在**（真实档位有），于是：
          · 会话的 channel / last_active_at 在 fake 下永远是缺失的，
            连"会话列表按最近活动排序"这种行为都测不出来；
          · normalize 里是靠 `getattr(pg, "ensure_session", None)` 兜住的，
            所以不报错 —— 又一个"fake 比真实世界宽容"的例子。
        """
        now = datetime.now(timezone.utc).isoformat()
        sess = self.sessions.get(session_id)
        if sess is None:
            self.sessions[session_id] = {
                "session_id": session_id, "user_id": user_id or "U-0001",
                "channel": channel, "ai_enabled": True, "emergency": False,
                "status": "active", "thread_id": session_id,
                "started_at": now, "last_active_at": now,
                "human_request_count": 0,
            }
        else:
            # ★ 只更新 last_active_at，**不要动 channel / user_id** ——
            #   必须与真实库的 `ON CONFLICT (session_id) DO UPDATE SET last_active_at = now()`
            #   完全一致。
            #
            #   第一版我在这里顺手把 channel 也覆盖了，结果踩坑：会话创建时是
            #   channel='web'（用户在网页上），而聊天请求里的 state["channel"] 是
            #   'api'（这条消息走的是 API 传输）—— 两者含义不同。覆盖之后，
            #   "按 channel 过滤会话列表"就再也找不到网页会话了。
            #   而真实库不受影响（它的 ON CONFLICT 本来就没更新 channel），
            #   于是这变成了一个**只存在于 fake 档位**的假 bug ——
            #   或者说，fake 又一次比真实世界"更不一样"，而这次是我自己造的。
            sess["last_active_at"] = now

    async def get_session(self, session_id: str) -> dict:
        return self.sessions.setdefault(session_id, {
            "session_id": session_id, "user_id": "U-0001", "channel": "web",
            "ai_enabled": True, "emergency": False, "status": "active",
            "thread_id": session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_active_at": datetime.now(timezone.utc).isoformat(),
            "human_request_count": 0,
        })

    async def bump_human_request(self, session_id: str) -> int:
        """与 PgStore 同形：+1 并返回累加后的值。"""
        sess = await self.get_session(session_id)
        sess["human_request_count"] = int(sess.get("human_request_count") or 0) + 1
        return sess["human_request_count"]

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

    async def list_sessions(self, *, limit: int = 30,
                            channel: str | None = None,
                            user_id: str | None = None) -> list[dict]:
        """与 PgStore.list_sessions 同形（内存版）。

        ★ 必须同形：这两个实现会互换（fake 档位跑测试、real 档位跑生产），
          形状不一致的话，接口层和前端在 fake 下测过了、上真实库就崩。
        """
        out = []
        for sid, sess in self.sessions.items():
            if channel and sess.get("channel") != channel:
                continue
            if user_id and str(sess.get("user_id")) != str(user_id):
                continue
            msgs = [m for m in self.messages if m.get("session_id") == sid]
            first_user = next((m["content"] for m in msgs if m.get("role") == "user"), None)
            title = (first_user or "").strip().replace("\n", " ")
            out.append({
                "session_id": sid,
                "channel": sess.get("channel", "web"),
                "status": sess.get("status", "active"),
                "ai_enabled": sess.get("ai_enabled", True),
                "title": (title[:40] + "…") if len(title) > 40 else title,
                "msg_count": len(msgs),
                "last_active_at": sess.get("last_active_at"),
            })
        # 按最近活动倒序（与真实库的 ORDER BY last_active_at DESC 一致）
        out.sort(key=lambda x: str(x.get("last_active_at") or ""), reverse=True)
        return out[:limit]

    # ── 认证：C 端用户 ──
    async def create_user(self, *, email: str, password_hash: str,
                          display_name: str = "", verify_token: str | None = None,
                          verify_expires: Any = None) -> dict:
        key = email.lower()
        if any(u.get("email") == key for u in self.users.values()):
            raise ValueError("该邮箱已注册")
        uid = str(uuid.uuid4())
        self.users[uid] = {
            "user_id": uid, "email": key,
            "display_name": display_name or email.split("@")[0],
            "password_hash": password_hash,
            "email_verified": False,
            "verify_token": verify_token,
            "verify_expires": verify_expires,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return {k: v for k, v in self.users[uid].items() if k != "password_hash"}

    async def find_user_by_email(self, email: str) -> dict | None:
        for u in self.users.values():
            if u.get("email") == (email or "").lower():
                return dict(u)
        return None

    async def get_user(self, user_id: str | None) -> dict | None:
        if not user_id:
            return None
        u = self.users.get(str(user_id))
        return {k: v for k, v in u.items() if k != "password_hash"} if u else None

    async def verify_user_email(self, token: str) -> dict | None:
        for u in self.users.values():
            if u.get("verify_token") and u["verify_token"] == token:
                u["email_verified"] = True
                u["verify_token"] = None
                u["verify_expires"] = None
                return {k: v for k, v in u.items() if k != "password_hash"}
        return None

    async def touch_user_login(self, user_id: str) -> None:
        u = self.users.get(str(user_id))
        if u:
            u["last_login_at"] = datetime.now(timezone.utc).isoformat()

    # ── 认证：坐席 ──
    async def find_agent_by_email(self, email: str) -> dict | None:
        for a in self.agents.values():
            if a.get("email") == (email or "").lower():
                return dict(a)
        return None

    async def get_agent(self, agent_id: str) -> dict | None:
        a = self.agents.get(agent_id)
        return {k: v for k, v in a.items() if k != "password_hash"} if a else None

    async def upsert_agent(self, *, agent_id: str, name: str, role: str,
                           email: str | None = None,
                           password_hash: str | None = None) -> dict:
        cur = self.agents.get(agent_id) or {}
        self.agents[agent_id] = {
            "agent_id": agent_id, "name": name, "role": role,
            "email": (email or cur.get("email") or "") or None,
            "password_hash": password_hash or cur.get("password_hash"),
            # ★ status 必须补上：真实表的这一列有 `DEFAULT 'active'`，
            #   数据库会替我们填；内存字典不会。少了它，登录时会命中
            #   "账号已被停用"分支 —— 又一个"fake 没有模拟数据库默认值"
            #   造成的假故障（写上这些默认值，就是在模拟 DDL）。
            "status": cur.get("status") or "active",
        }
        return {k: v for k, v in self.agents[agent_id].items() if k != "password_hash"}

    async def touch_agent_login(self, agent_id: str) -> None:
        a = self.agents.get(agent_id)
        if a:
            a["last_login_at"] = datetime.now(timezone.utc).isoformat()

    # ── 审计 ──
    async def write_audit(self, *, thread_id: str, session_id: str, review_round: int,
                          review_kind: str, verdict: str, risk_level: str, content_hash: str,
                          panel_reviews: list[dict], escalation_used: bool,
                          escalation_independent: bool, token_id: str | None = None,
                          model_versions: dict | None = None,
                          escalation_reason: str | None = None) -> int:
        """★ 签名必须与 PgStore.write_audit **逐字一致**。

        原来这里写的是 `async def write_audit(self, **kw)` —— 照单全收。
        看起来"更宽容所以更安全"，实际相反：调用方把 `risk_level` 拼错成
        `risk_lvl`，fake 档位照收不误、测试全绿，上真实库才 TypeError。
        **fake 的宽容不是安全，是把错误推迟到更难查的地方。**
        """
        self.audits.append({
            "thread_id": thread_id, "session_id": session_id, "review_round": review_round,
            "review_kind": review_kind, "verdict": verdict, "risk_level": risk_level,
            "content_hash": content_hash, "panel_reviews": panel_reviews,
            "escalation_used": escalation_used,
            "escalation_independent": escalation_independent, "token_id": token_id,
            "model_versions": model_versions or {}, "escalation_reason": escalation_reason,
        })
        return len(self.audits)

    async def write_hard_rule_hits(self, hits: list[dict],
                                   *, audit_id: int | None = None) -> None:
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
        # is_test 的判定与 PgStore 一致：看**会话的 channel** 是不是 'test'。
        # 内存版没有 SQL 可以用，就手写同一条规则 —— 两边判定必须一样，
        # 否则会出现"fake 档位测过了、真实档位行为不同"。
        sess = self.sessions.get(session_id) or {}
        ticket = {"ticket_id": str(uuid.uuid4()), "session_id": session_id,
                  "thread_id": thread_id, "user_id": user_id, "reason": reason,
                  "priority": priority, "status": "open", "profile_summary": profile_summary,
                  "last_turns": last_turns, "risk_report": risk_report,
                  "is_test": sess.get("channel") == "test",
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
    async def open_ticket_for_session(self, session_id: str) -> dict | None:
        """与 PgStore 同形：取这个会话**未结束**的最新一张工单。"""
        live = ("open", "accepted", "in_progress", "escalated")
        cands = [t for t in self.tickets
                 if t.get("session_id") == session_id and t.get("status") in live]
        return cands[-1] if cands else None

    async def list_tickets(self, *, statuses: list[str] | None = None,
                           priorities: list[str] | None = None,
                           include_test: bool = False,
                           limit: int = 50) -> list[dict]:
        rows = [t for t in self.tickets
                if (not statuses or t["status"] in statuses)
                and (not priorities or t["priority"] in priorities)
                and (include_test or not t.get("is_test"))]
        order = {"P0": 0, "P1": 1, "P2": 2}
        rows.sort(key=lambda t: order.get(t["priority"], 9))
        # msg_count 与 PgStore 的子查询同义：这台会话里有多少条消息。
        # 坐席台靠它发现"接管期间用户又说话了"。
        out = []
        for t in rows[:limit]:
            sid = t.get("session_id")
            n = sum(1 for m in self.messages if m.get("session_id") == sid)
            out.append({**t, "msg_count": n})
        return out

    async def purge_test_tickets(self) -> int:
        """与 PgStore 同形：只删 is_test 的，返回条数。"""
        doomed = [t["ticket_id"] for t in self.tickets if t.get("is_test")]
        self.tickets = [t for t in self.tickets if not t.get("is_test")]
        self.handoff_events = [e for e in self.handoff_events
                               if e.get("ticket_id") not in set(doomed)]
        return len(doomed)

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
        # is_test 的过滤与 PgStore 一致（那边 SQL 里写了 WHERE is_test = false）。
        # 两边判定必须一样，否则"fake 下指标正常、真实库上被测试数据污染"。
        real = [t for t in self.tickets if not t.get("is_test")]
        open_tickets = [t for t in real
                        if t["status"] in ("open", "accepted", "in_progress", "escalated")]
        by_rule: dict[str, int] = {}
        for hit in self.rule_hits:
            by_rule[hit["rule_id"]] = by_rule.get(hit["rule_id"], 0) + 1
        return {
            "tickets_total": len(real),
            "tickets_open": len(open_tickets),
            "tickets_p0_open": len([t for t in open_tickets if t["priority"] == "P0"]),
            "tickets_accepted": len([t for t in real if t.get("accepted_at")]),
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
