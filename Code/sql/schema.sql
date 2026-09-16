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
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- ── 登录（邮箱 + 密码）──
  -- email 用 CITEXT 风格的小写归一化在应用层做（lower()），这里只保证唯一。
  -- ★ 唯一约束必须是 UNIQUE 而不是靠应用层判重：并发注册同名邮箱时，
  --   应用层的"先查再插"会漏（两个请求同时查到不存在），只有数据库约束能兜住。
  email         TEXT UNIQUE,
  -- ★ 只存哈希，永不存明文。格式 `scrypt$n$r$p$salt$hash`，
  --   参数写在串里，将来调强度时老密码仍可校验。
  password_hash TEXT,
  -- 邮箱验证：占位实现（没有邮件服务），但字段与流程先按真的来，
  -- 接上邮件服务时不用改表。
  email_verified BOOLEAN NOT NULL DEFAULT false,
  verify_token   TEXT,
  verify_expires TIMESTAMPTZ,
  last_login_at  TIMESTAMPTZ
);
-- ★ 邮箱唯一性索引用的是 `email`，而这一列在旧库上还不存在 ——
--   它的 CREATE INDEX 挪到了文末「10. 增量变更」，见那里的说明。
--   把索引留在这里的话，旧库上跑 schema.sql 会在这里就报
--   `column "email" does not exist` 并**中断整个脚本**，
--   后面的建列语句一行都执行不到。

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
  -- ★ 为什么升级：high_risk_tag | conflict | abstain | low_confidence | no_reviews
  --   只有布尔值的话，想知道原因得把 panel_reviews 摊开反推（真这么干过，花了三条 SQL）。
  --   原因本来就是判定函数的返回值，顺手写下来几乎零成本。
  escalation_reason      TEXT,
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
  -- ★ 这条工单是不是**测试造的**（自动化脚本 / 冒烟 / 压测）。
  --
  --   为什么值得单开一列，而不是"按时间或按 reason 猜"：
  --   测试工单和真实工单在结构上**一模一样**（都是 P0、紧急、走同一套流程），
  --   混在队列里会直接毁掉演示 —— 坐席打开面板看到几十张工单，
  --   分不清哪张是眼前这位顾客的；它还会污染指标（SLA 超时率、平均等待）。
  --
  --   判定来源是**会话的 channel**：测试脚本用 channel='test' 建会话，
  --   真实入口用 web / wechat / app。让调用方自己声明，比事后猜可靠得多。
  is_test       BOOLEAN NOT NULL DEFAULT false,
  assigned_to   TEXT,                 -- ★ TEXT 而不是 UUID，理由见文件末尾「类型约定」
  accepted_at   TIMESTAMPTZ,          -- ★ 非空后才允许告知用户"人工已接入"
  closed_at     TIMESTAMPTZ,
  close_reason  TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ticket_queue ON ops.handoff_ticket (status, priority, created_at);
-- ★ 队列默认要把测试工单过滤掉，所以把 is_test 放进索引 —— 但这一列在旧库上
--   还不存在，所以这个索引和它的建列语句一起放在文末「10. 增量变更」。
--   放在这里的话，旧库上会在这里报 `column "is_test" does not exist` 并中断脚本。

CREATE TABLE IF NOT EXISTS ops.handoff_event (
  event_id   BIGSERIAL PRIMARY KEY,
  ticket_id  UUID NOT NULL REFERENCES ops.handoff_ticket(ticket_id),
  action     TEXT NOT NULL,
  actor      TEXT NOT NULL DEFAULT 'system',
  payload    JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.agent_user (
  -- ★ agent_id 是 TEXT 而不是 UUID：坐席 id 来自机构既有的客服/SSO 系统
  --   （工号、"zhangsan" 这种），强制 UUID 等于要求每个接入方额外维护一张映射表，
  --   是纯粹的过度约束。而且代码里自己的默认坐席就是 'demo-agent-0001'（非 UUID），
  --   与 UUID 列直接冲突 —— 接单接口会 500。详见文件末尾「类型约定」。
  agent_id TEXT PRIMARY KEY,
  name     TEXT NOT NULL,
  role     TEXT NOT NULL DEFAULT 'service',   -- service | doctor | compliance | admin
  on_duty  BOOLEAN NOT NULL DEFAULT false,
  status   TEXT NOT NULL DEFAULT 'active',

  -- ── 坐席登录 ──
  -- ★ 这两列是补上的一个真实安全洞：在此之前，坐席角色是**客户端在请求头里
  --   自己声明的**（X-Agent-Role: compliance），任何人都能拿到未脱敏的手机号。
  --   现在角色从 JWT 里读，客户端说什么不算数。
  email         TEXT UNIQUE,
  password_hash TEXT,
  last_login_at TIMESTAMPTZ
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

-- ══════════════════════════════════════════════════════════════
--  10. 增量变更（对**已有库**执行）
--
--  ★ 为什么需要这一段：上面所有建表都写成 `CREATE TABLE IF NOT EXISTS`，
--    它对**已存在**的表是**整条跳过**的 —— 表里少一列也不会补。
--    于是"升级就是重跑一遍 schema.sql"这条路径在**加列**时是失效的，
--    症状是运行期一个毫无线索的 500（`column "is_test" does not exist`），
--    而建表语句看上去明明写着这一列。
--
--    所以每次加列，除了改上面的 CREATE TABLE，**必须**在这里补一条
--    `ADD COLUMN IF NOT EXISTS`。这一段可以随便重复跑。
--
--  ⚠️ 加列时默认值要给全：已有行会按 DEFAULT 回填。
--     如果新列是 `NOT NULL` 又不给 DEFAULT，已有库上加列会直接失败。
-- ══════════════════════════════════════════════════════════════

-- 登录与鉴权（JWT 那一版加的）
ALTER TABLE app.app_user  ADD COLUMN IF NOT EXISTS email          TEXT;
ALTER TABLE app.app_user  ADD COLUMN IF NOT EXISTS password_hash  TEXT;
ALTER TABLE app.app_user  ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE app.app_user  ADD COLUMN IF NOT EXISTS verify_token   TEXT;
ALTER TABLE app.app_user  ADD COLUMN IF NOT EXISTS verify_expires TIMESTAMPTZ;
ALTER TABLE app.app_user  ADD COLUMN IF NOT EXISTS last_login_at  TIMESTAMPTZ;

ALTER TABLE ops.agent_user ADD COLUMN IF NOT EXISTS status        TEXT NOT NULL DEFAULT 'active';
ALTER TABLE ops.agent_user ADD COLUMN IF NOT EXISTS email         TEXT;
ALTER TABLE ops.agent_user ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE ops.agent_user ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

-- 二次复核对齐（`escalation_reason` 让"为什么升级"可统计）
ALTER TABLE app.review_audit ADD COLUMN IF NOT EXISTS escalation_reason TEXT;

-- 测试工单标记（见 README「测试工单为什么要单开一列」）
ALTER TABLE ops.handoff_ticket ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;

-- ★ 依赖新列的索引必须建在**建列之后**。
--   这条规则不是洁癖：`CREATE INDEX` 引用了不存在的列会直接报错并**中断整个
--   schema.sql**，于是后面所有语句（包括补列的 ALTER）一行都跑不到 ——
--   表现为"升级脚本明明写了补列，跑完列还是不在"。
--   实测踩到两次：`idx_app_user_email`（依赖 email）和 `idx_ticket_queue_real`
--   （依赖 is_test，而且它就写在建表语句下面，看起来最不可能出错）。
--   所以这两条都从各自的建表处挪到了这里。
--
--   以后加列时请一并检查：有没有 CREATE INDEX / 约束引用了这一列。
CREATE INDEX IF NOT EXISTS idx_app_user_email ON app.app_user (lower(email));
CREATE INDEX IF NOT EXISTS idx_ticket_queue_real
  ON ops.handoff_ticket (is_test, status, priority, created_at);

-- ★ 一次性回填（**不要**放进自动执行的部分，是否执行取决于你库里有什么）：
--   本次加列时，历史工单全部来自自动化脚本，所以整表回填成测试工单；
--   随后用 DELETE /ops/tickets/test 清掉。真实环境里**不要**跑这两句。
--     UPDATE ops.handoff_ticket SET is_test = true;                      -- 全部是测试残留时
--     UPDATE ops.handoff_ticket SET is_test = true WHERE created_at < '...';  -- 只回填某个时间点之前

-- ★ 类型订正：UUID → TEXT（详见文件末尾「类型约定」）。
--   这几列的类型在最初的库里是 UUID，而代码里的坐席工号是 'demo-agent-0001'
--   这种非 UUID 字符串，于是真实库上「接单」100% 失败、fake 档位却全绿。
--   写成 DO 块是因为 `ALTER COLUMN ... TYPE` **没有** IF NOT EXISTS 语法，
--   得自己查 information_schema；已经是 TEXT 时整段是空操作。
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'ops' AND table_name = 'agent_user'
                AND column_name = 'agent_id' AND data_type <> 'text') THEN
    ALTER TABLE ops.agent_user ALTER COLUMN agent_id TYPE TEXT USING agent_id::text;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'ops' AND table_name = 'handoff_ticket'
                AND column_name = 'assigned_to' AND data_type <> 'text') THEN
    ALTER TABLE ops.handoff_ticket ALTER COLUMN assigned_to TYPE TEXT USING assigned_to::text;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'ops' AND table_name = 'handoff_event'
                AND column_name = 'actor' AND data_type <> 'text') THEN
    ALTER TABLE ops.handoff_event ALTER COLUMN actor TYPE TEXT USING actor::text;
  END IF;
END $$;

-- ══════════════════════════════════════════════════════════════
--  类型约定：什么该是 UUID，什么该是 TEXT
--
--  这条约定是踩过坑之后写下来的，改表结构前请先读一遍。
--
--  · UUID —— 只用于【本系统自己生成】的实体主键：
--    session_id / ticket_id / appointment_id / user_id …
--    它们的共同点是"生成者就是我们"，用 UUID 便于分布式生成且不暴露数量。
--
--  · TEXT —— 用于【外部系统给定的标识】：
--    agent_id（客服系统/SSO 的工号）、operator、actor、project_id（机构项目编码）、
--    doc_id、rule_id …
--    这些 id 长什么样由对接方决定。把它们设成 UUID 会强制每个接入方额外维护
--    一张映射表，属于纯粹的过度约束，而且一旦对接方给的 id 不是 UUID，
--    报错会发生在**运行期最深的一条 SQL 上**，表现为一个毫无线索的 500。
--
--  ★ 实际踩到的例子：`ops.agent_user.agent_id` 和 `ops.handoff_ticket.assigned_to`
--    原来都是 UUID，而代码里自己的默认坐席是 `'demo-agent-0001'`。
--    于是运营后台的「接单」在真实 Postgres 上 100% 失败：
--      asyncpg.exceptions.DataError: invalid input for query argument $3:
--      'agent-001' (invalid UUID 'agent-001': length must be between 32..36 characters)
--    而 fake 档位是内存字典、照单全收，所以 36 项运营冒烟全绿也照样漏过去。
--    同一个概念在 `ops.handoff_event.actor` 里是 TEXT、在 `assigned_to` 里却是 UUID，
--    这种**同概念不同型**本身就是坏味道，发现时应当直接统一，而不是去迁就它。
-- ══════════════════════════════════════════════════════════════

