# 数据模型：PostgreSQL 表清单（本项目专用 v0.1）

> 这份清单**替代**早期草稿。已剔除与另一项目相关的表（如"试卷"）。
> 三个 schema 分开，不要混：
>
> | schema | 归属 | 说明 |
> |---|---|---|
> | `app` | 本项目业务表 | 全部由应用维护 |
> | `lg` | LangGraph | checkpoint 由 `AsyncPostgresSaver.setup()` 自动建表，**不要手改** |
> | `ops` | 运营后台 | 工单、坐席、处置日志 |
>
> 通用约定：主键用 `BIGSERIAL` 或 UUID（对外可见的用 UUID，避免暴露量级）；
> 时间统一 `TIMESTAMPTZ`；需要"软删"的表带 `is_deleted BOOLEAN`；
> JSON 字段用 `JSONB` 并加 GIN 索引（只在真的要查的字段上加）。

---

## 1. 用户与授权

```sql
-- 用户主表
CREATE TABLE app.app_user (
  user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name  TEXT,
  gender        SMALLINT,                       -- 0未知 1男 2女
  birth_year    SMALLINT,
  status        TEXT NOT NULL DEFAULT 'active', -- active | blocked
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 身份标识（一个用户可有多个渠道身份）
CREATE TABLE app.user_identity (
  identity_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES app.app_user(user_id),
  channel       TEXT NOT NULL,                  -- wechat | app | web | phone
  external_id   TEXT NOT NULL,                  -- openid / 手机号哈希 / 设备号
  verified      BOOLEAN NOT NULL DEFAULT false,
  bound_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (channel, external_id)
);

-- 授权记录：数据能不能用，靠这张表说话（不是靠模型判断）
CREATE TABLE app.user_consent (
  consent_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES app.app_user(user_id),
  scope         TEXT NOT NULL,                  -- profile | photo | medical_record | marketing
  purpose       TEXT NOT NULL,                  -- service | analytics
  granted       BOOLEAN NOT NULL,
  granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at    TIMESTAMPTZ,
  expires_at    TIMESTAMPTZ,                    -- 保留期限
  evidence      JSONB NOT NULL DEFAULT '{}'     -- 授权凭证（截图 id / 勾选记录）
);

-- 附件（术后照片等）：MVP 只入库不解析
CREATE TABLE app.user_attachment (
  file_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES app.app_user(user_id),
  session_id    UUID,
  storage_key   TEXT NOT NULL,                  -- 对象存储 key
  mime          TEXT NOT NULL,
  bytes         BIGINT NOT NULL,
  sha256        TEXT NOT NULL,
  consent_id    UUID REFERENCES app.user_consent(consent_id),  -- 必须有授权
  parsed        BOOLEAN NOT NULL DEFAULT false,  -- ★ MVP 恒为 false（未接视觉模型）
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 2. 会话与消息

```sql
CREATE TABLE app.chat_session (
  session_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID REFERENCES app.app_user(user_id),
  channel       TEXT NOT NULL,
  thread_id     TEXT NOT NULL UNIQUE,           -- ★ LangGraph 的 thread_id，直接用 session_id 亦可
  ai_enabled    BOOLEAN NOT NULL DEFAULT true,  -- ★ 人工接管后置 false，AI 不再自动回复
  emergency     BOOLEAN NOT NULL DEFAULT false,
  status        TEXT NOT NULL DEFAULT 'active', -- active | handoff | closed
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at     TIMESTAMPTZ
);

CREATE TABLE app.chat_message (
  message_id    BIGSERIAL PRIMARY KEY,
  session_id    UUID NOT NULL REFERENCES app.chat_session(session_id),
  turn_id       UUID,                           -- 对应图 state 的 turn_id
  role          TEXT NOT NULL,                  -- user | assistant | agent(人工) | system
  content       TEXT NOT NULL,
  content_hash  TEXT,                           -- ★ 出站内容哈希，与 release_token 对得上
  release_token TEXT,                           -- AI 出站才有；人工回复为 NULL
  risk_level    TEXT,
  review_kind   TEXT,                           -- content | operation | result_reply | emergency
  meta          JSONB NOT NULL DEFAULT '{}',    -- 意图、槽位、命中规则等
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON app.chat_message (session_id, created_at DESC);

-- 长会话压缩摘要（给"最近 5 轮"之外的历史用）
CREATE TABLE app.chat_turn_summary (
  summary_id    BIGSERIAL PRIMARY KEY,
  session_id    UUID NOT NULL REFERENCES app.chat_session(session_id),
  from_message  BIGINT NOT NULL,
  to_message    BIGINT NOT NULL,
  summary       TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 3. 图运行与审计

```sql
-- 每次用户消息触发的一次图运行
CREATE TABLE app.graph_run (
  run_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id     TEXT NOT NULL,
  session_id    UUID NOT NULL,
  turn_id       UUID NOT NULL,
  status        TEXT NOT NULL,                  -- running | done | interrupted | error
  interrupted_at TEXT,                          -- 挂起在哪个节点（await_confirm 等）
  error         TEXT,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ
);
CREATE INDEX ON app.graph_run (thread_id, started_at DESC);

-- ★ 审查审计：每一次审查结论都要能回溯到规则版本
CREATE TABLE app.review_audit (
  audit_id      BIGSERIAL PRIMARY KEY,
  thread_id     TEXT NOT NULL,
  session_id    UUID NOT NULL,
  review_round  INT  NOT NULL,
  review_kind   TEXT NOT NULL,
  verdict       TEXT NOT NULL,                  -- pass | revise | need_info | block | human
  risk_level    TEXT NOT NULL,
  content_hash  TEXT NOT NULL,
  panel_reviews JSONB NOT NULL DEFAULT '[]',    -- 三份专家意见（含 confidence / abstain）
  escalation_used        BOOLEAN NOT NULL DEFAULT false,
  escalation_independent BOOLEAN,               -- ★ MVP 为 false（同族复核），必须如实记录
  model_versions JSONB NOT NULL DEFAULT '{}',   -- 各角色实际使用的模型与提示词版本
  token_id      TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON app.review_audit (thread_id, created_at DESC);

-- 硬规则命中明细（一行一条，便于统计 Top 规则）
CREATE TABLE app.hard_rule_hit (
  hit_id        BIGSERIAL PRIMARY KEY,
  audit_id      BIGINT REFERENCES app.review_audit(audit_id),
  rule_id       TEXT NOT NULL,                  -- AD-001 ...
  rule_version  TEXT NOT NULL,
  severity      TEXT NOT NULL,
  action        TEXT NOT NULL,                  -- revise | block
  span          TEXT NOT NULL,                  -- 命中的原文片段
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON app.hard_rule_hit (rule_id, created_at DESC);

-- 放行凭据
CREATE TABLE app.release_token (
  token_id      TEXT PRIMARY KEY,
  content_hash  TEXT NOT NULL,
  plan_hash     TEXT,
  review_kind   TEXT NOT NULL,
  session_id    UUID NOT NULL,
  issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL,
  revoked_at    TIMESTAMPTZ,
  used_at       TIMESTAMPTZ                     -- 出站使用时间（一次性凭据）
);
```

---

## 4. 业务域（MVP 用本地表模拟）

> ⚠️ **上线前必须替换**：真实环境里这些属于机构业务系统（预约系统 / 机构数据库）。
> MVP 阶段用本地表模拟，是为了让演示能跑通；**不要**在上面长业务逻辑，
> 保持"仓储接口 + 本地实现"的形态，将来换成 HTTP 客户端即可。

```sql
CREATE TABLE app.store (
  store_id   TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT, address TEXT,
  phone      TEXT, status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE app.doctor (
  doctor_id  TEXT PRIMARY KEY, name TEXT NOT NULL, title TEXT,
  store_id   TEXT REFERENCES app.store(store_id), status TEXT NOT NULL DEFAULT 'active'
);

-- 资质：查询类 Agent 的唯一事实来源（模型不得编造）
CREATE TABLE app.doctor_credential (
  credential_id TEXT PRIMARY KEY,
  doctor_id     TEXT NOT NULL REFERENCES app.doctor(doctor_id),
  kind          TEXT NOT NULL,                  -- 执业医师资格 | 主诊医师 | 专项培训
  cert_no       TEXT NOT NULL,
  issued_by     TEXT,
  valid_from    DATE, valid_to DATE,
  status        TEXT NOT NULL DEFAULT 'valid'   -- valid | expired | revoked
);

CREATE TABLE app.project (
  project_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT,
  status     TEXT NOT NULL DEFAULT 'active'     -- active | offline（下架不得推荐）
);

-- 价格：只能取正式记录，模型不得估价
CREATE TABLE app.project_price (
  price_id   BIGSERIAL PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES app.project(project_id),
  store_id   TEXT REFERENCES app.store(store_id),
  price_cents INT NOT NULL,
  currency   TEXT NOT NULL DEFAULT 'CNY',
  effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_to   TIMESTAMPTZ,
  source     TEXT NOT NULL                      -- 来源标识，审计用
);

CREATE TABLE app.schedule_slot (
  slot_id    BIGSERIAL PRIMARY KEY,
  doctor_id  TEXT NOT NULL REFERENCES app.doctor(doctor_id),
  store_id   TEXT NOT NULL REFERENCES app.store(store_id),
  start_at   TIMESTAMPTZ NOT NULL,
  end_at     TIMESTAMPTZ NOT NULL,
  capacity   INT NOT NULL DEFAULT 1,
  booked     INT NOT NULL DEFAULT 0,
  status     TEXT NOT NULL DEFAULT 'open'       -- open | closed
);

CREATE TABLE app.appointment (
  appointment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES app.app_user(user_id),
  slot_id        BIGINT NOT NULL REFERENCES app.schedule_slot(slot_id),
  project_id     TEXT NOT NULL REFERENCES app.project(project_id),
  status         TEXT NOT NULL,                 -- booked | changed | cancelled | done | no_show
  fee_cents      INT NOT NULL DEFAULT 0,
  version        INT NOT NULL DEFAULT 1,        -- ★ 乐观锁：并发改约靠它
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 状态变更流水：可追溯、可对账
CREATE TABLE app.appointment_event (
  event_id       BIGSERIAL PRIMARY KEY,
  appointment_id UUID NOT NULL REFERENCES app.appointment(appointment_id),
  from_status    TEXT, to_status TEXT NOT NULL,
  reason         TEXT,
  operator       TEXT NOT NULL,                 -- ai | agent:<id>
  idempotency_key TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 5. 操作执行（外部副作用的唯一防线）

```sql
CREATE TABLE app.op_execution (
  idempotency_key TEXT PRIMARY KEY,             -- f"{thread_id}:{plan_hash}"
  session_id      UUID NOT NULL,
  action          TEXT NOT NULL,                -- change_appointment | cancel_appointment ...
  params          JSONB NOT NULL,
  status          TEXT NOT NULL,                -- pending | success | failed
  result          JSONB,
  error           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 用户确认记录：证明"执行前确实确认过，且确认的是这一版方案"
CREATE TABLE app.op_confirmation (
  confirm_id   BIGSERIAL PRIMARY KEY,
  session_id   UUID NOT NULL,
  plan_hash    TEXT NOT NULL,
  confirmed    BOOLEAN NOT NULL,
  raw_reply    TEXT,                            -- 用户原话
  confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 6. 知识库治理（向量在 Milvus，元数据在 PG）

```sql
CREATE TABLE app.kb_document (
  doc_id       TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  project_id   TEXT,                            -- 归属项目
  doc_type     TEXT NOT NULL,                   -- 项目资料 | 护理指南 | FAQ | 风险提示
  status       TEXT NOT NULL DEFAULT 'draft',   -- draft | approved | offline
  version      TEXT NOT NULL,                   -- ★ 检索必须按版本过滤
  source       TEXT NOT NULL,                   -- 来源（可追溯）
  reviewed_by  TEXT, reviewed_at TIMESTAMPTZ,
  effective_at TIMESTAMPTZ, expire_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE app.kb_chunk (
  chunk_id     BIGSERIAL PRIMARY KEY,
  doc_id       TEXT NOT NULL REFERENCES app.kb_document(doc_id),
  seq          INT NOT NULL,
  text         TEXT NOT NULL,
  token_count  INT,
  milvus_pk    BIGINT,                          -- 对应 Milvus 主键
  UNIQUE (doc_id, seq)
);

-- 向量版本映射：换嵌入模型时要能重灌，而不是把旧向量当新的用
CREATE TABLE app.kb_chunk_vector (
  chunk_id     BIGINT NOT NULL REFERENCES app.kb_chunk(chunk_id),
  embed_model  TEXT NOT NULL,                   -- BAAI/bge-m3
  embed_version TEXT NOT NULL,                  -- 与 milvus collection 一一对应
  milvus_pk    BIGINT NOT NULL,
  indexed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, embed_version)
);

CREATE TABLE app.kb_review_log (
  log_id     BIGSERIAL PRIMARY KEY,
  doc_id     TEXT NOT NULL,
  action     TEXT NOT NULL,                     -- submit | approve | reject | offline
  actor      TEXT NOT NULL,
  comment    TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 7. 画像与记忆

```sql
CREATE TABLE app.user_profile (
  user_id         UUID PRIMARY KEY REFERENCES app.app_user(user_id),
  summary         TEXT,                          -- 长期摘要（转人工时随工单携带）
  preferences     JSONB NOT NULL DEFAULT '{}',   -- {项目偏好, 时间偏好, 门店偏好}
  contraindications JSONB NOT NULL DEFAULT '[]', -- ★ 只记录"用户自述"，不推断、不诊断
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 事实抽取：每条都要有来源证据，冲突只标注不裁决
CREATE TABLE app.user_fact (
  fact_id      BIGSERIAL PRIMARY KEY,
  user_id      UUID NOT NULL REFERENCES app.app_user(user_id),
  fact_key     TEXT NOT NULL,                    -- budget_range | preferred_store | postop_day ...
  fact_value   TEXT NOT NULL,
  source_message BIGINT REFERENCES app.chat_message(message_id),
  confidence   NUMERIC(3,2),
  conflict_with BIGINT REFERENCES app.user_fact(fact_id),  -- ★ 冲突标注，不覆盖
  expires_at   TIMESTAMPTZ,                      -- 保留期限
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 8. 规则资产（不允许硬编码）

```sql
CREATE TABLE app.rule_registry (
  rule_id      TEXT NOT NULL,                    -- AD-001
  version      TEXT NOT NULL,                    -- v1.0
  category     TEXT NOT NULL,                    -- ad | medical | privacy | ops | legal
  severity     TEXT NOT NULL,                    -- high | medium | low
  action       TEXT NOT NULL,                    -- revise | block
  title        TEXT NOT NULL,
  spec         JSONB NOT NULL,                   -- 判定细节（正则 / 说明 / 依据）
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  retired_at   TIMESTAMPTZ,
  updated_by   TEXT NOT NULL,
  PRIMARY KEY (rule_id, version)
);

CREATE TABLE app.emergency_lexicon (
  term_id      BIGSERIAL PRIMARY KEY,
  tier         TEXT NOT NULL,                    -- P0 | P1 | P2
  cluster      TEXT NOT NULL,                    -- vision | respiratory | necrosis | ...
  term         TEXT NOT NULL,
  match_type   TEXT NOT NULL DEFAULT 'phrase',   -- phrase | regex | cooccur
  cooccur_with JSONB NOT NULL DEFAULT '[]',      -- 共现要求
  negation_sensitive BOOLEAN NOT NULL DEFAULT true,
  version      TEXT NOT NULL,
  enabled      BOOLEAN NOT NULL DEFAULT true,
  UNIQUE (term, tier, version)
);

CREATE TABLE app.risk_tag_dict (
  tag          TEXT PRIMARY KEY,                 -- complication_signal ...
  level        TEXT NOT NULL,                    -- HIGH | MEDIUM | LOW
  description  TEXT NOT NULL,
  action_hint  TEXT NOT NULL
);
```

---

## 9. 人工接管与运营（详细设计见 `ops-console.md`）

```sql
CREATE TABLE ops.handoff_ticket (
  ticket_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    UUID NOT NULL,
  thread_id     TEXT NOT NULL,
  user_id       UUID,
  reason        TEXT NOT NULL,                   -- risk_high | emergency | revision_exhausted | need_info | clarify_exhausted
  priority      TEXT NOT NULL,                   -- P0 | P1 | P2
  status        TEXT NOT NULL DEFAULT 'open',    -- open | accepted | in_progress | closed | escalated
  profile_summary TEXT,
  last_turns    JSONB NOT NULL DEFAULT '[]',     -- 最近 5 轮
  risk_report   JSONB NOT NULL DEFAULT '{}',     -- 命中规则 + 专家意见 + 证据
  assigned_to   UUID,                            -- ops.agent_user
  accepted_at   TIMESTAMPTZ,                     -- ★ 只有非空后才能告知用户"人工已接入"
  closed_at     TIMESTAMPTZ, close_reason TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON ops.handoff_ticket (status, priority, created_at);

CREATE TABLE ops.handoff_event (
  event_id   BIGSERIAL PRIMARY KEY,
  ticket_id  UUID NOT NULL REFERENCES ops.handoff_ticket(ticket_id),
  action     TEXT NOT NULL,                      -- create | accept | reply | escalate | close | reopen | mark_misreport
  actor      TEXT NOT NULL,                      -- agent:<id> | system
  payload    JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ops.agent_user (
  agent_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL,
  role       TEXT NOT NULL,                      -- service | doctor | compliance | admin
  on_duty    BOOLEAN NOT NULL DEFAULT false,
  status     TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE ops.agent_action_log (
  log_id     BIGSERIAL PRIMARY KEY,
  agent_id   UUID NOT NULL REFERENCES ops.agent_user(agent_id),
  ticket_id  UUID,
  action     TEXT NOT NULL,
  detail     JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 误报回流：紧急词表优化的唯一数据来源
CREATE TABLE ops.misreport_feedback (
  feedback_id BIGSERIAL PRIMARY KEY,
  ticket_id   UUID REFERENCES ops.handoff_ticket(ticket_id),
  source      TEXT NOT NULL,                     -- emergency | hard_rule | risk_tag
  ref_id      TEXT NOT NULL,                     -- 命中的 term / rule_id / tag
  raw_message TEXT NOT NULL,
  verdict     TEXT NOT NULL,                     -- true_positive | false_positive
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 10. 可观测与出站

```sql
CREATE TABLE app.llm_call_log (
  call_id      BIGSERIAL PRIMARY KEY,
  thread_id    TEXT,
  role         TEXT NOT NULL,                    -- classify | kb_draft | review_medical ...
  model        TEXT NOT NULL,
  prompt_version TEXT,
  latency_ms   INT,
  prompt_tokens INT, completion_tokens INT,
  ok           BOOLEAN NOT NULL,
  error        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON app.llm_call_log (role, created_at DESC);

-- 出站消息（发送失败可重投；也是"是否真的发出去了"的证据）
CREATE TABLE app.outbox_message (
  outbox_id  BIGSERIAL PRIMARY KEY,
  session_id UUID NOT NULL,
  token_id   TEXT REFERENCES app.release_token(token_id),
  payload    JSONB NOT NULL,
  status     TEXT NOT NULL DEFAULT 'pending',    -- pending | sent | failed
  attempts   INT NOT NULL DEFAULT 0,
  sent_at    TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 11. LangGraph checkpoint 表（不要手改）

```sql
-- schema: lg —— 由 checkpointer.setup() 自动创建，属于框架内部结构
--   lg.checkpoints           图状态快照
--   lg.checkpoint_blobs      大字段（state 序列化内容）
--   lg.checkpoint_writes     待写入（并发写恢复用）
--
-- 运维要点：
--   1) 这些表会随会话数持续增长 —— 必须配定期归档策略（见下）
--   2) thread_id 与 app.chat_session.thread_id 对应
--   3) 不要自己写 SQL 改这些表；要清理用框架之外的归档脚本
```

**归档策略**（MVP 就要有，否则演示环境很快膨胀）：

```sql
-- 会话空闲超过 SESSION_IDLE 且已 closed 的 thread，其 checkpoint 归档后删除
DELETE FROM lg.checkpoints
WHERE thread_id IN (
  SELECT thread_id FROM app.chat_session
  WHERE status = 'closed' AND closed_at < now() - interval '7 days'
);
```

---

## 12. 与图/节点的对应关系

| 图元素 | 落库位置 |
|---|---|
| `thread_id` | `app.chat_session.thread_id` ↔ `lg.checkpoints.thread_id` |
| `turn_id` | `app.graph_run.turn_id`、`app.chat_message.turn_id` |
| `review_audit` 写入 | 风险审查子图 `issue_token` 节点 |
| `hard_rule_hit` 写入 | 风险审查子图 `hard_rules` 节点 |
| `release_token` 签发 | `issue_token` 节点；校验在 `send` / `execute_op` |
| `op_execution` 幂等 | `execute_op` 节点（唯一写入方） |
| `op_confirmation` | `await_confirm` 恢复后写入 |
| `handoff_ticket` 创建 | `human_handoff` 节点 |
| `chat_session.ai_enabled=false` | 工单 `accepted` 时由运营后台写入（不是图写的） |
| `emergency_lexicon` / `rule_registry` | `emergency_screen` 与 `hard_rules` 只读加载（带缓存） |
| `kb_*` 元数据 | 知识科普子图 `kb_retrieve` 的过滤条件来源 |

---

## 13. MVP 阶段可以偷懒的地方（但要留痕）

| 表 | MVP 处理 | 备注 |
|---|---|---|
| `app.kb_document` / `kb_chunk` | **只建表，灌 10–20 条演示数据即可** | 知识库内容你说了可以暂缓 |
| `app.doctor_credential` / `project_price` | 造演示数据 | 上线前换成机构接口 |
| `app.schedule_slot` / `appointment` | 造演示数据 + 本地事务 | 上线前换成预约系统接口 |
| `app.user_fact` | 可以先不做冲突标注 | 但字段留着 |
| `app.chat_turn_summary` | 会话不长可先不写 | 字段留着 |
| `lg.*` | 必须真建 | 否则 interrupt / 恢复跑不起来 |
| `app.review_audit` / `hard_rule_hit` / `release_token` | **必须真建** | 这是审计与合规的骨架，演示时最有说服力 |

---

## 14. 几条必须写进 DDL 的约束（别只写在代码里）

1. `op_execution.idempotency_key` 是**主键** —— 把幂等交给数据库，而不是靠应用层自觉。
2. `app.chat_message.content_hash` + `release_token` 同时存在 —— 能证明"发出去的就是审过的那一版"。
3. `app.user_attachment.consent_id` 未授权应为 `NULL` 且**不允许被检索使用**（应用层强校验 + 视图隔离）。
4. `ops.handoff_ticket.accepted_at` 为空的工单，**不允许**向上层返回"人工已接入"（在 API 层断言）。
5. `app.kb_document.status = 'approved'` 且 `expire_at > now()` 才允许被检索 —— 检索过滤条件必须来自表，不能靠内存过滤。
