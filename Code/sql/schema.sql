-- ════════════════════════════════════════════════════════════════
--  智美医美顾问 MVP · 业务库表结构
--  用法：psql "$PG_DSN" -f sql/schema.sql
--
--  注意分工：
--    app / ops  schema → 本文件（业务与运营）
--    lg         schema → 由 LangGraph 的 AsyncPostgresSaver.setup() 自动创建，
--                        不要手改，也不要写进本文件
--
--  字段设计说明见设计文档 data-model.md，此处为实现所需的最小完整集。
-- ════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS ops;

-- ══════════════ 1. 用户与授权 ══════════════
CREATE TABLE IF NOT EXISTS app.app_user (
  user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name  TEXT,
  gender        SMALLINT,
  birth_year    SMALLINT,
  status        TEXT NOT NULL DEFAULT 'active',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.user_identity (
  identity_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES app.app_user(user_id),
  channel       TEXT NOT NULL,
  external_id   TEXT NOT NULL,
  verified      BOOLEAN NOT NULL DEFAULT false,
  bound_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (channel, external_id)
);

-- 授权记录：数据能不能用靠这张表说话，不是靠模型判断
CREATE TABLE IF NOT EXISTS app.user_consent (
  consent_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES app.app_user(user_id),
  scope         TEXT NOT NULL,            -- profile | photo | medical_record | marketing
  purpose       TEXT NOT NULL DEFAULT 'service',
  granted       BOOLEAN NOT NULL DEFAULT true,
  granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at    TIMESTAMPTZ,
  expires_at    TIMESTAMPTZ,
  evidence      JSONB NOT NULL DEFAULT '{}'
);

-- 附件（术后照片等）：MVP 只入库不解析
CREATE TABLE IF NOT EXISTS app.user_attachment (
  file_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID REFERENCES app.app_user(user_id),
  session_id    UUID,
  storage_key   TEXT NOT NULL,
  mime          TEXT NOT NULL,
  bytes         BIGINT NOT NULL DEFAULT 0,
  sha256        TEXT NOT NULL DEFAULT '',
  consent_id    UUID REFERENCES app.user_consent(consent_id),
  parsed        BOOLEAN NOT NULL DEFAULT false,   -- ★ 未接视觉模型前恒为 false
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ══════════════ 2. 会话与消息 ══════════════
CREATE TABLE IF NOT EXISTS app.chat_session (
  session_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID REFERENCES app.app_user(user_id),
  channel        TEXT NOT NULL DEFAULT 'web',
  thread_id      TEXT NOT NULL,
  ai_enabled     BOOLEAN NOT NULL DEFAULT true,   -- ★ 人工接管后置 false
  emergency      BOOLEAN NOT NULL DEFAULT false,
  status         TEXT NOT NULL DEFAULT 'active',
  started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS app.chat_message (
  message_id    BIGSERIAL PRIMARY KEY,
  session_id    UUID NOT NULL REFERENCES app.chat_session(session_id),
  turn_id       UUID,
  role          TEXT NOT NULL,                  -- user | assistant | agent | system
  content       TEXT NOT NULL,
  content_hash  TEXT,                           -- ★ 与 release_token 对得上
  release_token TEXT,
  risk_level    TEXT,
  review_kind   TEXT,
  meta          JSONB NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_message_session ON app.chat_message (session_id, created_at DESC);

-- ══════════════ 3. 审计（合规骨架，必须真建）══════════════
CREATE TABLE IF NOT EXISTS app.review_audit (
  audit_id       BIGSERIAL PRIMARY KEY,
  thread_id      TEXT NOT NULL,
  session_id     UUID,
  review_round   INT NOT NULL DEFAULT 0,
  review_kind    TEXT NOT NULL,                 -- content | operation | result_reply | emergency | note
  verdict        TEXT NOT NULL,                 -- pass | revise | need_info | block | human | note
  risk_level     TEXT NOT NULL,
  content_hash   TEXT NOT NULL DEFAULT '',
  panel_reviews  JSONB NOT NULL DEFAULT '[]',
  escalation_used        BOOLEAN NOT NULL DEFAULT false,
  escalation_independent BOOLEAN,               -- ★ MVP 为 false（同族复核），必须如实记录
  model_versions JSONB NOT NULL DEFAULT '{}',
  token_id       TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_review_audit_thread ON app.review_audit (thread_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app.hard_rule_hit (
  hit_id       BIGSERIAL PRIMARY KEY,
  audit_id     BIGINT REFERENCES app.review_audit(audit_id),
  rule_id      TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  severity     TEXT NOT NULL,
  action       TEXT NOT NULL,                   -- revise | block
  span         TEXT NOT NULL DEFAULT '',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_hard_rule_hit_rule ON app.hard_rule_hit (rule_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app.release_token (
  token_id     TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  plan_hash    TEXT,
  review_kind  TEXT NOT NULL,
  session_id   UUID,
  issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at   TIMESTAMPTZ NOT NULL,
  revoked_at   TIMESTAMPTZ,
  used_at      TIMESTAMPTZ
);

-- ══════════════ 4. 操作执行（外部副作用的唯一防线）══════════════
CREATE TABLE IF NOT EXISTS app.op_execution (
  idempotency_key TEXT PRIMARY KEY,             -- f"{thread_id}:{plan_hash}"
  session_id      UUID,
  action          TEXT NOT NULL,
  params          JSONB NOT NULL DEFAULT '{}',
  status          TEXT NOT NULL,                -- pending | success | failed
  result          JSONB,
  error           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.op_confirmation (
  confirm_id   BIGSERIAL PRIMARY KEY,
  session_id   UUID,
  plan_hash    TEXT NOT NULL,
  confirmed    BOOLEAN NOT NULL,
  raw_reply    TEXT,
  confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ══════════════ 5. 画像 ══════════════
CREATE TABLE IF NOT EXISTS app.user_profile (
  user_id           UUID PRIMARY KEY REFERENCES app.app_user(user_id),
  summary           TEXT,
  preferences       JSONB NOT NULL DEFAULT '{}',
  contraindications JSONB NOT NULL DEFAULT '[]',   -- ★ 只记录用户自述，不推断不诊断
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.user_fact (
  fact_id        BIGSERIAL PRIMARY KEY,
  user_id        UUID NOT NULL REFERENCES app.app_user(user_id),
  fact_key       TEXT NOT NULL,
  fact_value     TEXT NOT NULL,
  source_message BIGINT,
  confidence     NUMERIC(3,2),
  conflict_with  BIGINT,
  expires_at     TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ════════════════════════════════════════════════════════════════
--  6. 业务域（MVP 用本地表模拟）
--  ⚠️ 上线前替换为机构业务系统接口；不要在这些表上长业务逻辑
-- ════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS app.store (
  store_id TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT, address TEXT,
  phone TEXT, status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS app.doctor (
  doctor_id TEXT PRIMARY KEY, name TEXT NOT NULL, title TEXT,
  store_id  TEXT REFERENCES app.store(store_id),
  status    TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS app.doctor_credential (
  credential_id TEXT PRIMARY KEY,
  doctor_id     TEXT NOT NULL REFERENCES app.doctor(doctor_id),
  kind          TEXT NOT NULL,
  cert_no       TEXT NOT NULL,
  issued_by     TEXT,
  valid_from    DATE, valid_to DATE,
  status        TEXT NOT NULL DEFAULT 'valid'
);

CREATE TABLE IF NOT EXISTS app.project (
  project_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT,
  status     TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS app.project_price (
  price_id   BIGSERIAL PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES app.project(project_id),
  store_id   TEXT REFERENCES app.store(store_id),
  price_cents INT NOT NULL,
  currency   TEXT NOT NULL DEFAULT 'CNY',
  effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_to   TIMESTAMPTZ,
  source     TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS app.schedule_slot (
  slot_id   BIGSERIAL PRIMARY KEY,
  doctor_id TEXT NOT NULL REFERENCES app.doctor(doctor_id),
  store_id  TEXT NOT NULL REFERENCES app.store(store_id),
  start_at  TIMESTAMPTZ NOT NULL,
  end_at    TIMESTAMPTZ NOT NULL,
  capacity  INT NOT NULL DEFAULT 1,
  booked    INT NOT NULL DEFAULT 0,
  status    TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_slot_store_start ON app.schedule_slot (store_id, start_at);

CREATE TABLE IF NOT EXISTS app.appointment (
  appointment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID REFERENCES app.app_user(user_id),
  slot_id        BIGINT REFERENCES app.schedule_slot(slot_id),
  project_id     TEXT REFERENCES app.project(project_id),
  status         TEXT NOT NULL DEFAULT 'booked',
  fee_cents      INT NOT NULL DEFAULT 0,
  version        INT NOT NULL DEFAULT 1,     -- ★ 乐观锁：并发改约靠它
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.appointment_event (
  event_id        BIGSERIAL PRIMARY KEY,
  appointment_id  UUID NOT NULL REFERENCES app.appointment(appointment_id),
  from_status     TEXT, to_status TEXT NOT NULL,
  reason          TEXT,
  operator        TEXT NOT NULL DEFAULT 'ai',
  idempotency_key TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ══════════════ 7. 知识库治理（向量在 Milvus，元数据在 PG）══════════════
CREATE TABLE IF NOT EXISTS app.kb_document (
  doc_id       TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  project_id   TEXT,
  doc_type     TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'draft',   -- draft | approved | offline
  version      TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT '',
  reviewed_by  TEXT, reviewed_at TIMESTAMPTZ,
  effective_at TIMESTAMPTZ, expire_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.kb_chunk (
  chunk_id    BIGSERIAL PRIMARY KEY,
  doc_id      TEXT NOT NULL REFERENCES app.kb_document(doc_id),
  seq         INT NOT NULL,
  text        TEXT NOT NULL,
  token_count INT,
  milvus_pk   BIGINT,
  UNIQUE (doc_id, seq)
);

CREATE TABLE IF NOT EXISTS app.kb_chunk_vector (
  chunk_id      BIGINT NOT NULL REFERENCES app.kb_chunk(chunk_id),
  embed_model   TEXT NOT NULL,
  embed_version TEXT NOT NULL,
  milvus_pk     BIGINT NOT NULL,
  indexed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, embed_version)
);

-- ══════════════ 8. 人工接管与运营 ══════════════
CREATE TABLE IF NOT EXISTS ops.handoff_ticket (
  ticket_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    UUID,
  thread_id     TEXT NOT NULL,
  user_id       UUID,
  reason        TEXT NOT NULL,        -- risk_high | emergency | revision_exhausted | need_info | clarify_exhausted
  priority      TEXT NOT NULL,        -- P0 | P1 | P2
  status        TEXT NOT NULL DEFAULT 'open',
  profile_summary TEXT,
  last_turns    JSONB NOT NULL DEFAULT '[]',
  risk_report   JSONB NOT NULL DEFAULT '{}',
  assigned_to   UUID,
  accepted_at   TIMESTAMPTZ,          -- ★ 非空后才允许告知用户"人工已接入"
  closed_at     TIMESTAMPTZ,
  close_reason  TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ticket_queue ON ops.handoff_ticket (status, priority, created_at);

CREATE TABLE IF NOT EXISTS ops.handoff_event (
  event_id   BIGSERIAL PRIMARY KEY,
  ticket_id  UUID NOT NULL REFERENCES ops.handoff_ticket(ticket_id),
  action     TEXT NOT NULL,
  actor      TEXT NOT NULL DEFAULT 'system',
  payload    JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.agent_user (
  agent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name     TEXT NOT NULL,
  role     TEXT NOT NULL DEFAULT 'service',   -- service | doctor | compliance | admin
  on_duty  BOOLEAN NOT NULL DEFAULT false,
  status   TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS ops.misreport_feedback (
  feedback_id BIGSERIAL PRIMARY KEY,
  ticket_id   UUID REFERENCES ops.handoff_ticket(ticket_id),
  source      TEXT NOT NULL,                  -- emergency | hard_rule | risk_tag
  ref_id      TEXT NOT NULL,
  raw_message TEXT NOT NULL,
  verdict     TEXT NOT NULL,                  -- true_positive | false_positive
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ══════════════ 9. 可观测 ══════════════
CREATE TABLE IF NOT EXISTS app.llm_call_log (
  call_id        BIGSERIAL PRIMARY KEY,
  thread_id      TEXT,
  role           TEXT NOT NULL,
  model          TEXT NOT NULL,
  latency_ms     INT,
  prompt_tokens  INT, completion_tokens INT,
  ok             BOOLEAN NOT NULL DEFAULT true,
  error          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
