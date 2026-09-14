"""
PostgreSQL 业务库访问（asyncpg）。

注意分工：
  · 业务库（本文件）—— app schema，表和字段见 sql/schema.sql
  · 图状态（checkpoint）—— lg schema，由 AsyncPostgresSaver.setup() 自动建表，不要手改

业务表（门店/医生/项目/档期/预约）在 MVP 里是本地模拟，上线前替换为机构业务系统接口。
这一段刻意与真实查询分开标注，避免将来把业务逻辑长在模拟表上。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import asyncpg
except Exception:  # noqa: BLE001
    asyncpg = None  # type: ignore[assignment]

CST = timezone(timedelta(hours=8))


class PgStore:
    def __init__(self, dsn: str, *, min_size: int = 2, max_size: int = 10) -> None:
        if asyncpg is None:
            raise RuntimeError("未安装 asyncpg：pip install asyncpg")
        self._dsn = dsn
        self._min, self._max = min_size, max_size
        self.pool: Any = None

    async def connect(self) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(self._dsn, min_size=self._min, max_size=self._max)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def _fetch(self, sql: str, *args: Any) -> list[Any]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(sql, *args)

    async def _fetchrow(self, sql: str, *args: Any) -> Any:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def _execute(self, sql: str, *args: Any) -> str:
        async with self.pool.acquire() as conn:
            return await conn.execute(sql, *args)

    # ══════════════ 会话与权限 ══════════════
    async def get_session(self, session_id: str) -> dict:
        row = await self._fetchrow(
            "SELECT session_id, user_id, ai_enabled, emergency, status, thread_id "
            "FROM app.chat_session WHERE session_id = $1", session_id)
        if row is None:
            return {"session_id": session_id, "user_id": None, "ai_enabled": True,
                    "emergency": False, "status": "active", "thread_id": session_id}
        return dict(row)

    async def ensure_session(self, session_id: str, *, channel: str = "cli",
                             user_id: str | None = None) -> None:
        """
        幂等地建会话行（真实档位必须有，否则 chat_message 的外键会拦住写入）。

        ★ 这里踩过一个只有真实 Postgres 才会暴露的坑：
          把 $1 同时用在 session_id(uuid) 与 thread_id(text) 两列上，
          PostgreSQL 无法推断参数类型，报
          `AmbiguousParameterError: inconsistent types deduced for parameter $1 — uuid versus text`。
          所以 thread_id 用独立参数 $4，不要复用 $1。
        """
        if not _is_uuid(session_id):
            raise ValueError(f"session_id 必须是 UUID（LangGraph 的 thread_id 也用它）：{session_id!r}")
        await self._execute(
            """INSERT INTO app.chat_session (session_id, user_id, channel, thread_id)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (session_id) DO UPDATE SET last_active_at = now()""",
            _uuid(session_id),
            _uuid(user_id) if user_id else None,
            channel,
            session_id,
        )

    async def load_auth(self, session_id: str) -> dict:
        row = await self._fetchrow(
            """SELECT u.user_id,
                      COALESCE(array_agg(c.scope) FILTER (WHERE c.granted AND c.revoked_at IS NULL), '{}') AS scopes
               FROM app.chat_session s
               LEFT JOIN app.app_user u ON u.user_id = s.user_id
               LEFT JOIN app.user_consent c ON c.user_id = u.user_id
               WHERE s.session_id = $1 GROUP BY u.user_id""",
            uuid.UUID(session_id) if _is_uuid(session_id) else None)
        if row is None or row["user_id"] is None:
            # MVP：未绑定用户的会话按「已实名但无额外授权」处理
            return {"verified": False, "user_id": None, "scopes": [], "data_consents": []}
        return {"verified": True, "user_id": str(row["user_id"]),
                "scopes": list(row["scopes"] or []), "data_consents": list(row["scopes"] or [])}

    async def save_message(self, *, session_id: str, turn_id: str | None, role: str,
                           content: str, content_hash: str | None = None,
                           release_token: str | None = None, risk_level: str | None = None,
                           review_kind: str | None = None, meta: dict | None = None) -> int:
        row = await self._fetchrow(
            """INSERT INTO app.chat_message
               (session_id, turn_id, role, content, content_hash, release_token, risk_level, review_kind, meta)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING message_id""",
            _uuid(session_id), _uuid(turn_id), role, content, content_hash,
            release_token, risk_level, review_kind, json.dumps(meta or {}, ensure_ascii=False))
        return int(row["message_id"])

    async def recent_turns(self, session_id: str, limit: int = 5) -> list[dict]:
        rows = await self._fetch(
            """SELECT role, content, created_at FROM app.chat_message
               WHERE session_id = $1 ORDER BY message_id DESC LIMIT $2""",
            _uuid(session_id), limit)
        return [dict(r) for r in reversed(rows)]

    # ══════════════ 审计 ══════════════
    async def write_audit(self, *, thread_id: str, session_id: str, review_round: int,
                          review_kind: str, verdict: str, risk_level: str, content_hash: str,
                          panel_reviews: list[dict], escalation_used: bool,
                          escalation_independent: bool, token_id: str | None = None,
                          model_versions: dict | None = None) -> int:
        row = await self._fetchrow(
            """INSERT INTO app.review_audit
               (thread_id, session_id, review_round, review_kind, verdict, risk_level,
                content_hash, panel_reviews, escalation_used, escalation_independent,
                token_id, model_versions)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING audit_id""",
            thread_id, _uuid(session_id), review_round, review_kind, verdict, risk_level,
            content_hash, json.dumps(panel_reviews, ensure_ascii=False),
            escalation_used, escalation_independent, token_id,
            json.dumps(model_versions or {}, ensure_ascii=False))
        return int(row["audit_id"])

    async def write_hard_rule_hits(self, hits: list[dict], *, audit_id: int | None = None) -> None:
        if not hits:
            return
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO app.hard_rule_hit (audit_id, rule_id, rule_version, severity, action, span)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                [(audit_id, h["rule_id"], h["rule_version"], h["severity"], h["action"], h["span"])
                 for h in hits])

    async def note_same_family_review(self, thread_id: str) -> None:
        """MVP 阶段复核与首次审查同族 —— 如实记一笔，不假装独立。"""
        await self._execute(
            """INSERT INTO app.review_audit
               (thread_id, session_id, review_round, review_kind, verdict, risk_level,
                content_hash, panel_reviews, escalation_used, escalation_independent)
               VALUES ($1, NULL, 0, 'note', 'note', 'low', '', '[]', true, false)""",
            thread_id)

    # ══════════════ 操作执行（幂等）══════════════
    async def op_already_done(self, key: str) -> bool:
        row = await self._fetchrow(
            "SELECT status FROM app.op_execution WHERE idempotency_key = $1", key)
        return bool(row and row["status"] == "success")

    async def load_op_result(self, key: str) -> dict:
        row = await self._fetchrow(
            "SELECT result FROM app.op_execution WHERE idempotency_key = $1", key)
        return json.loads(row["result"]) if row and row["result"] else {}

    async def save_op_result(self, key: str, *, session_id: str, action: str,
                            params: dict, result: dict) -> None:
        await self._execute(
            """INSERT INTO app.op_execution (idempotency_key, session_id, action, params, status, result)
               VALUES ($1,$2,$3,$4,'success',$5)
               ON CONFLICT (idempotency_key) DO UPDATE
                 SET status='success', result=EXCLUDED.result, updated_at=now()""",
            key, _uuid(session_id), action,
            json.dumps(params, ensure_ascii=False), json.dumps(result, ensure_ascii=False))

    async def save_confirmation(self, *, session_id: str, plan_hash: str,
                                confirmed: bool, raw_reply: str) -> None:
        await self._execute(
            """INSERT INTO app.op_confirmation (session_id, plan_hash, confirmed, raw_reply)
               VALUES ($1,$2,$3,$4)""",
            _uuid(session_id), plan_hash, confirmed, raw_reply)

    # ══════════════ 模型调用日志（成本与延迟的唯一数据来源）══════════════
    async def log_llm_call(self, *, thread_id: str | None, role: str, model: str,
                           latency_ms: int, prompt_tokens: int | None,
                           completion_tokens: int | None, ok: bool,
                           error: str | None = None) -> None:
        """记一次模型调用。

        ★ 调用方（模型网关）会把这里包在 try/except 里：**记日志失败绝不影响对话**。
          这是刻意的 —— 它是观测，不是业务。
        ★ token 拿不到时写 NULL 而不是 0：写 0 会被成本报表误读成"这次没花钱"，
          而真相是"不知道" —— 两种含义在报表里必须区分得开。
        """
        await self._execute(
            """INSERT INTO app.llm_call_log
               (thread_id, role, model, latency_ms, prompt_tokens, completion_tokens, ok, error)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            thread_id, role, model, int(latency_ms),
            None if prompt_tokens is None else int(prompt_tokens),
            None if completion_tokens is None else int(completion_tokens),
            bool(ok), error)

    # ══════════════ 工单 ══════════════
    async def create_handoff_ticket(self, *, session_id: str, thread_id: str,
                                    user_id: str | None, reason: str, priority: str,
                                    profile_summary: str, last_turns: list[dict],
                                    risk_report: dict) -> dict:
        row = await self._fetchrow(
            """INSERT INTO ops.handoff_ticket
               (session_id, thread_id, user_id, reason, priority, profile_summary, last_turns, risk_report)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING ticket_id, status, created_at""",
            _uuid(session_id), thread_id, _uuid(user_id), reason, priority,
            profile_summary, json.dumps(last_turns, ensure_ascii=False, default=str),
            json.dumps(risk_report, ensure_ascii=False, default=str))
        return {"ticket_id": str(row["ticket_id"]), "status": row["status"],
                "created_at": row["created_at"].isoformat(), "reason": reason, "priority": priority}

    # ══════════════ 画像 ══════════════
    async def load_profile(self, user_id: str | None) -> dict:
        if not user_id:
            return {"user_id": None, "summary": None, "preferences": {}, "contraindications": []}
        row = await self._fetchrow(
            "SELECT summary, preferences, contraindications FROM app.user_profile WHERE user_id = $1",
            _uuid(user_id))
        if row is None:
            return {"user_id": user_id, "summary": None, "preferences": {}, "contraindications": []}
        return {"user_id": user_id, "summary": row["summary"],
                "preferences": json.loads(row["preferences"] or "{}"),
                "contraindications": json.loads(row["contraindications"] or "[]")}

    # ════════════════════════════════════════════════════════════
    #  以下为「业务域模拟」—— 上线前替换为机构业务系统接口
    # ════════════════════════════════════════════════════════════
    async def query_slots(self, *, store: str, project: str, around: str | None) -> list[dict]:
        rows = await self._fetch(
            """SELECT s.slot_id, s.start_at, s.status, d.name AS doctor, st.name AS store
               FROM app.schedule_slot s
               JOIN app.doctor d ON d.doctor_id = s.doctor_id
               JOIN app.store st ON st.store_id = s.store_id
               WHERE st.name = $1 AND s.status = 'open' AND s.booked < s.capacity
               ORDER BY s.start_at LIMIT 5""", store)
        return [{"slot_id": r["slot_id"], "start_at": r["start_at"].isoformat(),
                 "doctor": r["doctor"], "store": r["store"]} for r in rows]

    async def find_upcoming_appointment(self, user_id: str,
                                        projects: list[str] | None = None) -> dict | None:
        """查用户当前可改约的最近一次预约（含乐观锁版本号）。

        ★ 这个方法必须有，否则"改约"根本无从谈起 —— 改约是**对一个已存在的预约**
          做操作，没有 appointment_id 就只能改空气。第一版正是漏了这一步：
          release 层传 `appointment_id=None`，SQL 变成 `appointment_id = NULL`
          （永远不匹配），而报出来的错是"状态已变化，请刷新后重试" ——
          把矛头完全指向并发冲突（实际上这一行是 booked/version=1，根本没变过）。
          一个误导性的报错比一个直接的报错贵得多。

        ★ 匹配要同时比 id 和 name，且接受**多个候选**：库里存的是 'P-01'，
          而槽位/意图里拿到的是"热玛吉"；一句话还可能同时提到几个项目，
          拼成"热玛吉、超声炮"去精确比对必然匹配不到 ——
          而且不报错，只会静默返回"查不到"，又是一种静默失败。
        """
        rows = await self._fetch(
            """SELECT a.appointment_id, a.status, a.version, a.project_id,
                      a.fee_cents, s.start_at, st.name AS store
                 FROM app.appointment a
                 LEFT JOIN app.project p ON p.project_id = a.project_id
                 LEFT JOIN app.schedule_slot s ON s.slot_id = a.slot_id
                 LEFT JOIN app.store st ON st.store_id = s.store_id
                WHERE a.user_id = $1 AND a.status = 'booked'
                  AND ($2::text[] IS NULL OR a.project_id = ANY($2::text[])
                                            OR p.name = ANY($2::text[]))
                ORDER BY s.start_at NULLS LAST, a.created_at
                LIMIT 1""",
            _uuid(user_id), list(projects) if projects else None)
        if not rows:
            return None
        r = rows[0]
        return {"appointment_id": str(r["appointment_id"]), "status": r["status"],
                "version": int(r["version"]), "project": r["project_id"],
                "fee_cents": int(r["fee_cents"] or 0),
                "datetime": r["start_at"].isoformat() if r["start_at"] else None,
                "store": r["store"]}

    async def get_appointment(self, appointment_id: str) -> dict | None:
        row = await self._fetchrow(
            """SELECT appointment_id, status, fee_cents, version FROM app.appointment
               WHERE appointment_id = $1""", _uuid(appointment_id))
        return dict(row) if row else None

    async def change_appointment(self, *, store: str, datetime_: str, request_id: str,
                                 appointment_id: str | None = None,
                                 expected_version: int | None = None) -> dict:
        aid = _uuid(appointment_id)
        # ★ 缺参数时直接、明确地报错，不要让它退化成"WHERE = NULL 不匹配"
        #   再被误报成并发冲突 —— 这两种原因的处理方式完全不同。
        if aid is None:
            raise ValueError("改约缺少 appointment_id：无法确定要修改哪一条预约")
        if expected_version is None:
            raise ValueError("改约缺少 expected_version：乐观锁必须有比对的基准版本")

        row = await self._fetchrow(
            """UPDATE app.appointment SET status='changed', updated_at=now(), version=version+1
               WHERE appointment_id = $1 AND version = $2 AND status = 'booked'
               RETURNING appointment_id, status, version""",
            aid, int(expected_version))
        if row is None:
            raise RuntimeError("改约失败：该预约状态或版本已变化（可能已被改约或取消），请刷新后重试")
        await self._execute(
            """INSERT INTO app.appointment_event (appointment_id, from_status, to_status, reason, operator, idempotency_key)
               VALUES ($1,'booked','changed','用户改约','ai',$2)""",
            aid, request_id)
        return {"status": "success", "appointment_id": str(row["appointment_id"]),
                "store": store, "datetime": datetime_, "request_id": request_id}

    async def query_clinic_info(self, *, store: str | None, doctor: str | None) -> dict:
        row = await self._fetchrow(
            """SELECT st.name AS store, st.address, st.phone, d.name AS doctor, c.kind, c.status
               FROM app.store st
               LEFT JOIN app.doctor d ON d.store_id = st.store_id AND ($2::text IS NULL OR d.name = $2)
               LEFT JOIN app.doctor_credential c ON c.doctor_id = d.doctor_id
               WHERE ($1::text IS NULL OR st.name = $1)
               ORDER BY st.name LIMIT 1""", store, doctor)
        if row is None:
            return {}
        return {"store": row["store"], "address": row["address"], "phone": row["phone"],
                "doctor": row["doctor"], "credential": f"{row['kind']} {row['status']}" if row["kind"] else None}


    # ════════════════════════════════════════════════════════════
    #  运营后台（工单队列与处置）
    # ════════════════════════════════════════════════════════════
    #: 允许被 update_ticket 修改的字段白名单 —— 动态 SQL 必须走白名单，不能拼接列名
    _TICKET_FIELDS = {"status", "priority", "assigned_to", "accepted_at",
                      "closed_at", "close_reason", "profile_summary", "risk_report"}

    async def list_tickets(self, *, statuses: list[str] | None = None,
                           priorities: list[str] | None = None,
                           limit: int = 50) -> list[dict]:
        rows = await self._fetch(
            """SELECT ticket_id, session_id, thread_id, user_id, reason, priority, status,
                      profile_summary, assigned_to, accepted_at, closed_at, close_reason, created_at
               FROM ops.handoff_ticket
               WHERE ($1::text[] IS NULL OR status = ANY($1))
                 AND ($2::text[] IS NULL OR priority = ANY($2))
               ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,
                        created_at
               LIMIT $3""",
            statuses, priorities, limit)
        return [_row(r) for r in rows]

    async def get_ticket(self, ticket_id: str) -> dict | None:
        row = await self._fetchrow(
            """SELECT ticket_id, session_id, thread_id, user_id, reason, priority, status,
                      profile_summary, last_turns, risk_report, assigned_to,
                      accepted_at, closed_at, close_reason, created_at
               FROM ops.handoff_ticket WHERE ticket_id = $1""",
            _uuid(ticket_id))
        return _row(row) if row else None

    async def update_ticket(self, ticket_id: str, **fields: Any) -> dict | None:
        cols = {k: v for k, v in fields.items() if k in self._TICKET_FIELDS}
        if not cols:
            return await self.get_ticket(ticket_id)
        # ★ 时间戳列必须转成 datetime 再绑定：asyncpg 只接受 datetime 对象，
        #   传 ISO 字符串会直接 DataError（expected a datetime.date or datetime.datetime
        #   instance, got 'str'）。调用方（ops/service.py）传的是 .isoformat() 字符串，
        #   而 fake 档位是内存字典、存什么读什么都行 —— 于是 36 项运营冒烟全绿，
        #   真实库上「接单」却 100% 失败。
        #   转换放在存储层是有意的：数据库类型的映射本来就该由它负责，
        #   否则每多一个调用点就多一次踩坑的机会。
        cols = {k: (_ts(v) if k in _TICKET_TS_FIELDS else v) for k, v in cols.items()}
        assigns = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(cols))
        await self._execute(
            f"UPDATE ops.handoff_ticket SET {assigns} WHERE ticket_id = $1",
            _uuid(ticket_id), *cols.values())
        return await self.get_ticket(ticket_id)

    async def list_handoff_events(self, ticket_id: str) -> list[dict]:
        rows = await self._fetch(
            """SELECT action, actor, payload, created_at FROM ops.handoff_event
               WHERE ticket_id = $1 ORDER BY event_id""", _uuid(ticket_id))
        return [_row(r) for r in rows]

    async def log_handoff_event(self, ticket_id: str, action: str, actor: str,
                                payload: dict | None = None) -> None:
        await self._execute(
            """INSERT INTO ops.handoff_event (ticket_id, action, actor, payload)
               VALUES ($1,$2,$3,$4)""",
            _uuid(ticket_id), action, actor,
            json.dumps(payload or {}, ensure_ascii=False, default=str))

    async def set_ai_enabled(self, session_id: str, enabled: bool) -> None:
        """接单 → 关掉 AI；关单 → 恢复 AI。这一步是"AI 不与坐席抢话"的唯一开关。"""
        await self._execute(
            "UPDATE app.chat_session SET ai_enabled = $2, status = $3 WHERE session_id = $1",
            _uuid(session_id), enabled, "active" if enabled else "handoff")

    async def list_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = await self._fetch(
            """SELECT role, content, review_kind, risk_level, meta, created_at
               FROM app.chat_message WHERE session_id = $1
               ORDER BY message_id DESC LIMIT $2""",
            _uuid(session_id), limit)
        return [_row(r) for r in reversed(rows)]

    async def save_agent_message(self, *, session_id: str, agent_id: str,
                                 content: str, ticket_id: str | None = None) -> int:
        row = await self._fetchrow(
            """INSERT INTO app.chat_message (session_id, role, content, review_kind, meta)
               VALUES ($1,'agent',$2,'agent_reply',$3) RETURNING message_id""",
            _uuid(session_id), content,
            json.dumps({"agent_id": agent_id, "ticket_id": ticket_id}, ensure_ascii=False))
        return int(row["message_id"])

    async def save_misreport(self, *, ticket_id: str | None, source: str, ref_id: str,
                             raw_message: str, verdict: str, note: str = "") -> None:
        await self._execute(
            """INSERT INTO ops.misreport_feedback
               (ticket_id, source, ref_id, raw_message, verdict, note)
               VALUES ($1,$2,$3,$4,$5,$6)""",
            _uuid(ticket_id), source, ref_id, raw_message, verdict, note)

    async def ops_metrics(self) -> dict:
        row = await self._fetchrow(
            """SELECT
                 COUNT(*)                                                          AS tickets_total,
                 COUNT(*) FILTER (WHERE status IN ('open','accepted','in_progress','escalated'))
                                                                                   AS tickets_open,
                 COUNT(*) FILTER (WHERE priority = 'P0'
                                    AND status IN ('open','accepted','in_progress','escalated'))
                                                                                   AS tickets_p0_open,
                 COUNT(*) FILTER (WHERE accepted_at IS NOT NULL)                    AS tickets_accepted,
                 AVG(EXTRACT(EPOCH FROM (accepted_at - created_at)))                AS avg_wait_seconds
               FROM ops.handoff_ticket""")
        audit = await self._fetchrow(
            """SELECT COUNT(*) AS review_rounds,
                      COUNT(*) FILTER (WHERE verdict = 'pass' AND review_round = 1) AS first_pass,
                      COUNT(*) FILTER (WHERE escalation_used
                                         AND escalation_independent IS FALSE)      AS same_family
               FROM app.review_audit WHERE review_kind <> 'note'""")
        rules = await self._fetch(
            """SELECT rule_id, COUNT(*) AS n FROM app.hard_rule_hit
               GROUP BY rule_id ORDER BY n DESC LIMIT 10""")
        mis = await self._fetchrow(
            "SELECT COUNT(*) AS n FROM ops.misreport_feedback WHERE verdict = 'false_positive'")
        rounds = int(audit["review_rounds"] or 0)
        return {
            "tickets_total": int(row["tickets_total"] or 0),
            "tickets_open": int(row["tickets_open"] or 0),
            "tickets_p0_open": int(row["tickets_p0_open"] or 0),
            "tickets_accepted": int(row["tickets_accepted"] or 0),
            "avg_wait_seconds": round(float(row["avg_wait_seconds"]), 1)
            if row["avg_wait_seconds"] is not None else None,
            "review_rounds": rounds,
            "first_pass_rate": round(int(audit["first_pass"] or 0) / rounds, 4) if rounds else None,
            "hard_rule_top": [[r["rule_id"], int(r["n"])] for r in rules],
            "misreport_pending": int(mis["n"] or 0),
            "escalation_same_family": int(audit["same_family"] or 0),
        }


# ── 小工具 ──
_JSON_COLUMNS = {"last_turns", "risk_report", "meta", "payload", "preferences",
                 "contraindications", "panel_reviews", "hard_rule_hits"}

#: ops.handoff_ticket 里的时间戳列（值可能以 ISO 字符串形式传进来）
_TICKET_TS_FIELDS = {"accepted_at", "closed_at"}


def _ts(value: Any) -> Any:
    """把 ISO 字符串转成 datetime；已经是 datetime / None 就原样返回。

    asyncpg 的参数绑定是**强类型**的：TIMESTAMPTZ 列只接受 datetime 对象，
    给字符串会抛 `DataError: ... (expected a datetime.date or datetime.datetime
    instance, got 'str')`。

    ★ 这类"字符串 vs 类型化值"的不匹配是 fake 档位最擅长掩盖的一类缺陷：
      FakePg 是内存字典，`'2026-09-14T07:54:39+00:00'` 存进去、读出来都毫无问题；
      真实 asyncpg 会直接拒绝。而且报错发生在最深的一条 SQL 上，
      第一眼看上去像"数据库有问题"，实际是调用方少转了一次类型。
      同一个文件里已经因为同类原因踩过两次（UUID 列收了非 UUID 字符串）。
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _row(row: Any) -> dict:
    """asyncpg Record → 可 JSON 序列化的 dict（datetime / UUID 转字符串，JSONB 字段解出来）。"""
    if row is None:
        return {}
    out: dict[str, Any] = {}
    for key, value in dict(row).items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif key in _JSON_COLUMNS and isinstance(value, str):
            try:
                out[key] = json.loads(value)
            except Exception:  # noqa: BLE001
                out[key] = value
        else:
            out[key] = value
    return out


def _uuid(value: str | None):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _is_uuid(value: str) -> bool:
    return _uuid(value) is not None


def now_cst() -> str:
    return datetime.now(CST).isoformat()
