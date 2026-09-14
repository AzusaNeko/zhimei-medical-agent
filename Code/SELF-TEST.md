# 真实依赖自测清单（Postgres + Milvus + BGE + DeepSeek）

> **这份清单验的是"真实依赖下的行为与质量"**，不是"图的接线"——接线已经用 fake 档位验过了
> （104 项断言）。所以下面的每一步都写明：**期望看到什么**、**不对时先看哪里**。
>
> 建议预留：环境 30 分钟 + 模型下载 20 分钟 + 全流程自测 90 分钟。
> 逐项打勾，把结果填到最后的记录表里。

---

## 0. 前置检查（约 10 分钟）

> 先跑一次体检，它会替你把下面这些查一遍并给出下一步：
> ```bash
> python scripts/check_env.py
> ```

### 0.1 依赖装齐

```bash
cd Code
pip install -r requirements.txt        # 或 uv pip install -r requirements.txt
python -c "import langgraph, psycopg, pymilvus, FlagEmbedding, openai, asyncpg; print('deps OK')"
```

- [ ] 上面这条命令**不报错**
- [ ] `python -c "import langgraph; print(langgraph.__version__)"` ≥ 1.0
- ⚠️ 最容易漏的是 `langgraph-checkpoint-postgres`（`AsyncPostgresSaver` 在它里面，不在 langgraph 主包里）

### 0.2 模型下到本地（国内网络必看，本机已下好）

**不要**让 FlagEmbedding 自动下载 —— 在国内镜像下它必定失败。用仓库里的脚本：

```bash
python scripts/download_models.py        # 下到 models/ 下，可断点续传
```

它会打印两行配置，写进 `.env`（本机已配好）：
```
BGE_M3_PATH=F:\...\Code\models\bge-m3
BGE_RERANKER_PATH=F:\...\Code\models\bge-reranker-v2-m3
```

**为什么不能自动下载**（三个坑叠在一起，全都踩过）：

| 现象 | 原因 | 对策 |
|---|---|---|
| 卡在连 `huggingface.co` 超时 | `huggingface_hub` 在**导入时**读 `HF_ENDPOINT`，晚设等于没设 | `.env` 里设 `HF_ENDPOINT`，且 `encoder.py`/`reranker.py` 里**先 import settings 再 import FlagEmbedding** |
| `PermissionError: [WinError 5]` 写 `.cache\huggingface` | 受限环境不让写工作区外 | `.env` 里设 `HF_HOME=<项目内目录>` |
| `WinError 1314 客户端没有所需的特权` | Windows 默认不允许普通用户建符号链接，而 hf 默认用 symlink 组织缓存 | `.env` 里设 `HF_HUB_DISABLE_SYMLINKS=1` |
| `401 Unauthorized ... cas-server.xethub.hf.co` | 新版 hub 默认走 xet 后端，hf-mirror 不代理它 | `.env` 里设 `HF_HUB_DISABLE_XET=1` |
| `403 Forbidden ... imgs/.DS_Store` | FlagEmbedding 内部拉全量文件，镜像拒绝提供 macOS 垃圾文件 | 用 `download_models.py`（带 ignore 规则）本地化 |

- [ ] `models/bge-m3` 约 2.3 GB、`models/bge-reranker-v2-m3` 约 2.3 GB 都存在
- [ ] `python scripts/check_env.py` 的"模型缓存"一节显示 ✓

### 0.2b 验证检索链条真的通

```bash
python scripts/check_retrieval.py
```

- [ ] 输出"混合检索召回 N 条"（N > 0）
- [ ] 精排最高分 ≥ `MIN_RERANK`（默认 0.30）
- ⚠️ 注意：Milvus 的 `get_collection_stats` 在 flush 前常返回 `row_count=0`，
      **看着像没写进去，其实数据在 growing segment 里可搜** —— 以能不能搜到为准

### 0.3 确认要连的实例（两种情形，选一个）

> **本机当前是情形 B，且已经就绪**：`zhimei-pg` / `zhimei-etcd` / `zhimei-minio` / `zhimei-milvus`
> 四个容器 healthy，端口 55432 / 19530，29 张业务表已建好。
> 也就是说**本节与第 1、2 节都可以直接跳过**，从第 0.1（装依赖）、0.2（下模型）开始。

**情形 A：复用本机现成实例。** 本机已经有一套 Postgres + Milvus 在跑
（别的项目在用），直接连它，**不要再起一套**：

```bash
docker ps --format "{{.Names}} | {{.Status}} | {{.Ports}}"
```

- [ ] 能看到 `bid_agent_postgres`（映射 **5434**）与 `bid_agent_milvus`（映射 **19532**）处于 `Up ... healthy`
- [ ] TCP 可达：19532 与 5434 都能连上
- [ ] **本项目专用的库已创建**（不要用别人的库）：
      `docker exec bid_agent_postgres psql -U bidagent -c "CREATE DATABASE zhimei OWNER bidagent;"`
- [ ] `.env` 是情形 A 的配置：`PG_DSN=...@localhost:5434/zhimei`、`MILVUS_URI=http://localhost:19532`

> ⚠️ **共享 Milvus 实例时集合名必须带项目前缀**（`.env` 已设为 `zhimei_kb_chunks`）。
> 叫 `kb_chunks` 这种通用名有和别的项目撞名的风险，而 Milvus 的集合是**实例级共享**的。

**情形 B：起独立一套**（想完全隔离，或换机器）：

```bash
docker compose -p zhimei up -d && docker compose -p zhimei ps
```

- [ ] 四个容器都 `running`（postgres / etcd / minio / **milvus**）
- [ ] Milvus 是最后一个健康的（依赖 etcd + minio，首次约 1–2 分钟）
- [ ] `.env` 改用情形 B 的配置（55432 / 19530）

---

## 1. 起依赖

- 情形 A → **跳过本节**（现成实例已经在跑）
- 情形 B → 见上一节，起完确认四个容器 `running` 且 milvus `healthy`

---

## 2. 建业务表

```bash
# 用 .env 里 PG_DSN 的值（情形 A = ...@localhost:5434/zhimei）
set PG_DSN=<你的 PG_DSN>
psql "%PG_DSN%" -f sql/schema.sql
psql "%PG_DSN%" -c "\dt app.*" -c "\dt ops.*"
```

- [ ] `app` 与 `ops` 两个 schema 下的表都建出来了
- [ ] 注意：**`lg` schema 不在这里**，它由 LangGraph 首次运行时自动创建 —— 第一次跑完再看
      `\dt lg.*`，应该能看到 `checkpoints` / `checkpoint_blobs` / `checkpoint_writes`

> ⚠️ 复用现成 Postgres 实例时，本项目的表都落在 **`zhimei` 这个库**里，
> 不会碰到别的项目的库；两个 schema（`app` / `ops`）是本项目专用的。

---

## 3. 灌演示数据（第一次真正碰 Milvus 与 BGE）

```bash
python scripts/seed_kb.py
```

- [ ] 结束后打印出 **`★ 演示用户 user_id = ...`** —— 抄下来，第 4 步要用
- [ ] 打印 `知识库 5 篇（5 个 chunk 已写入 Milvus）`

**如果这一步报错，八成是这两处：**

| 报错 | 原因 | 处理 |
|---|---|---|
| Milvus sparse 字段类型错 / `data type not match` | `lexical_weights` 的键被当成了字符串 | 把 `app/services/encoder.py` 里 `{str(k): float(w) ...}` 改成 `{int(k): float(w) ...}` |
| 建集合报 dim 不匹配 | 换了嵌入模型（BGE-M3 dense 是 1024 维） | 同步改 `milvus_store.DENSE_DIM` |

**验证检索真的通了**（不要跳过，这是后面一切的前提）：

```bash
psql "postgresql://zhimei:zhimei@localhost:55432/zhimei" -c \
  "select doc_id,title,status,version from app.kb_document;"
```

- [ ] 5 篇文档 `status` 全为 **`approved`**
- ⚠️ 检索的过滤条件写死了 `doc_status == "approved"` —— 状态不对的话**一条都查不到**，
      表现出来像"检索坏了"，其实是数据没审核态

---

## 4. 第一轮真跑（最关键的一步）

```bash
python -m app.cli -t "热玛吉和超声炮有什么区别" --user <第 3 步抄的 user_id>
```

- [ ] 打印 `档位：real（真实 Postgres / Milvus / DeepSeek）`
- [ ] 最终 `审查：verdict=pass`，且 `✅ 已放行并发送` 带凭据
- [ ] **回答里有 `[E1]` 这样的引用标注**，且属于已审核资料的内容
- [ ] 尾部有"具体以医生面诊评估为准"
- [ ] `lg` schema 里已经有 checkpoint 记录了

**如果卡住或答得不对，按这个顺序排查：**

1. `classify` 出问题 → 意图全是 `clarify`：模型名/密钥/温度；先单独把这句话跑三遍看是否稳定
2. 检索为空 → 回第 3 步查 `kb_document.status`
3. 草稿没有引用 → 看 prompt 是否被截断；`MAX_EVIDENCE` 是否太小
4. 报 JSON 解析错 → DeepSeek 的 JSON 模式要求 **prompt 里出现 "json" 字样**（现有 prompt 都满足）；确认 `DEEPSEEK_MODEL` 是支持 JSON 模式的对话模型

---

## 5. 四个典型场景

```bash
python -m app.cli --demo --user <user_id>
```

| # | 场景 | 期望 | 勾选 |
|---|---|---|---|
| 1 | 科普（会触发一次修订） | `复审轮次≥2`、`修订次数≥1`，且**最终文本里没有原来那句违规表述** | [ ] |
| 2 | 预约改约 | 先挂起要确认 → 输入 y → `execution_result` 有值 → 结果回复**再送审一次** | [ ] |
| 3 | 术后紧急 | 立即发**固定模板**（含"急诊"）+ 建 P0 工单，且**不走模型生成措辞** | [ ] |
| 4 | 意图不明确 | 追问澄清；连续问 3 次"那个怎么样"后**不再无限追问** | [ ] |

场景 2 的三个额外检查（真实 PG 才验得到）：

- [ ] `select * from app.op_execution;` 里有记录，`idempotency_key` 形如 `<thread>:<plan_hash>`
- [ ] `select status,version from app.appointment where appointment_id='1111...';` 状态变成 `changed`、version +1
- [ ] 再确认一次同一方案 → **不会重复执行**（走幂等分支）

---

## 6. 接口层

```bash
python -m app.api --port 8077                # 另开一个终端
python scripts/demo_api.py --base http://127.0.0.1:8077
```

- [ ] 三个场景的事件流与 fake 档位一致：`status…` → `final` / `awaiting_confirmation` → `done`
- [ ] 预约场景：`⏸ 等待确认` 后流结束，`/confirm` 恢复后才有 `final`
- [ ] 紧急场景：`final` 与 `handoff` **两个事件都出现**（并行出口）
- [ ] 过程事件里的文案是固定的几句，**不含模型生成内容**

```bash
python scripts/smoke_api.py                  # 26 项（会强制走 fake 档位，用于回归）
```

---

## 7. 运营后台

浏览器打开 `http://127.0.0.1:8077/ops/panel`

- [ ] 队列里能看到第 5 步场景 3 产生的 P0 工单，且**带一行上下文**（原因 + 画像摘要首行）
- [ ] 点开详情：画像摘要 / 最近对话 / 风险报告 / **AI 未发出的草稿**都在
- [ ] 详情里 `人工已接入 = 否`，并显示"此时不得告知用户人工已接入"
- [ ] 点【接单】→ 提示已接管
- [ ] 接单后回到聊天接口发消息 → **409 human_takeover**（AI 不与坐席抢话）
- [ ] 回复框里输入"我们保证根治" → **409 软提示**，确认后可发
- [ ] 输入"已为您取消该笔预约" → **403 代执行拦截**
- [ ] 输入含身份证号 → **403 硬拦**
- [ ] 切换角色为 `service` 与 `doctor`，同一段含手机号的消息**显示不同**（脱敏生效）
- [ ] 点【标记误报】→ `select * from ops.misreport_feedback;` 有记录
- [ ] 关单 → 聊天接口恢复可用（不再 409）

---

## 8. ⭐ 只有真实模型才能验的质量项

这一节是这份清单**最该花时间**的地方。下面每一项 fake 档位都验不了。

### 8.1 引用是否真的可溯源

```sql
-- 抽查：把最近一次出站的引用拿去比对证据原文
select content from app.chat_message where role='assistant' order by message_id desc limit 1;
```

- [ ] 草稿里每个 `[E1]` 都能在 `citations` 里找到对应项，且 `quote` **确实出现在**证据原文中
- [ ] 不存在"引用了 E1 但 E1 内容跟这句话无关"的情况（这是最常见的伪造引用）

### 8.2 三位专家意见是否真的"视角不同"

**这是单模型方案最脆弱的地方**（同族复核的已知缺口）：

- [ ] 在审计表里看同一轮的 `panel_reviews`，三份意见的 `findings` 是否**互不相同**：
      `medical` 只谈医疗边界、`ad` 只谈广告合规、`privacy` 只谈隐私与服务规则
- [ ] 如果三份意见高度雷同 → prompt 需要强化差异（`app/prompts/review.py`），
      否则"多专家并行审查"就是自欺欺人

```sql
select review_kind, verdict, risk_level,
       jsonb_array_length(panel_reviews) as n,
       escalation_used, escalation_independent
from app.review_audit order by audit_id desc limit 10;
```

- [ ] `escalation_independent` 为 **false**（MVP 单模型），且面板上有"同族复核"标识
- [ ] 这是**必须如实记录**的项 —— 面试被问到独立性时，答案是"不构成独立复核，我们标注了"

### 8.3 审查失败时必须降级为转人工，不能"装作通过"

```bash
# 把审查超时设成极小，制造必然超时
set T_REVIEW=0.001
python -m app.cli -t "热玛吉和超声炮有什么区别" --user <user_id>
```

- [ ] 结果是 **转人工（human）**，不是 `verdict=pass`
- [ ] 这是安全侧默认值；如果这里通过了，说明降级路径写错了，是**严重缺陷**

### 8.4 事实核对真的会抓出夸大

- [ ] 在 `app/config/rules.yaml` 里临时把 `AD-001` 的 `action` 改成 `revise` 后，
      构造一句含"效果永久"的草稿（或直接改 `kb_draft` 的 prompt 让模型试），
      确认 `kb_verify` 的 `claims` 里 `status` 标成 `overstated` 而不是放行

### 8.5 紧急信号：规则层与模型提示的边界

连续跑这几句，看 `emergency` 与 `emergency_hit` 事件：

| 输入 | 期望 |
|---|---|
| 我现在眼睛看不清 | 命中（P0） |
| 热玛吉会不会导致失明 | **不命中**（询问风险） |
| 网上说做完会失明 | **不命中**（转述） |
| 我没有视力模糊 | **不命中**（否定） |
| 我朋友做完眼睛看不清 | **不命中**（第三人称） |
| 脸发白还特别疼 | 命中（泛词 + 部位 + 程度） |

- [ ] 六条全部符合预期
- [ ] ⚠️ 重点看第 2 条：**模型很容易把"会不会失明"也标成紧急**（`emergency_hint=true`），
      但规则层的抑制逻辑必须把它压住。如果压不住，说明 `_suppressed` 的顺序判定被绕过

### 8.6 多意图是否真的并行扇出

```bash
python -m app.cli -t "下周三下午能约浦东店的热玛吉吗，另外这玩意儿跟超声炮啥区别" --user <user_id>
```

- [ ] `草稿来源` 里**同时出现两个 agent**（如 `b_agent、k_agent`）
- [ ] 最终内容分段展示，两件事都回答了

### 8.7 延迟与成本

- [ ] 记录每轮的端到端耗时（正常应 < 15 秒；超过 30 秒要看是不是检索或审查在拖）
- [ ] `select role, count(*), avg(latency_ms) from app.llm_call_log group by role;`
      —— 如果这张表是空的，说明 `llm_call_log` 还没接（当前实现未写入，属已知待补项）

---

## 9. 必须复核的不变量（fake 档位验过，真实档位要再看一遍）

| # | 不变量 | 真实档位要看什么 | 勾选 |
|---|---|---|---|
| 1 | 修订环有上限 | 第二轮审查若仍命中**同一个 `rule_id`** → 第三轮应升级为 block 或转人工，不能无限改 | [ ] |
| 2 | 修订真的改了内容 | 对比修订前后文本，不是换了个说法又踩同一条规则 | [ ] |
| 3 | 执行幂等 | 同一 `plan_hash` 重复确认不会重复改约（看 `op_execution` 与 `appointment_event`） | [ ] |
| 4 | 方案哈希绑定 | 确认时传一个错的 `plan_hash` → 作废方案、**绝不执行**、也不再二次挂起 | [ ] |
| 5 | 凭据绑定内容 | 手动改一下 `release_token` 里的内容再发 → 校验失败 | [ ] |
| 6 | clarify 预算跨轮次 | 连续 3 轮"那个怎么样" → 第 3 轮起不再追问（真实模型可能一次就问对，需刻意构造） | [ ] |
| 7 | 转人工上下文完整 | `ops.handoff_ticket.last_turns` 里**既有用户原话也有 AI 回复**（用户消息落库修好了，要复核） | [ ] |
| 8 | 会话与身份绑定 | 不带 `--user` 跑预约类请求应判 `need_info`；带 `--user` 后正常 —— 这条同时验证了权限校验真的在起作用 | [ ] |
| 9 | 人工接管开关 | 接单 → 聊天 409；关单 → 恢复。面板与聊天两个模块联动 | [ ] |
| 10 | 脱敏在服务端 | 用 `curl` 直接打 `/ops/tickets/{id}`（不带浏览器）也应看到脱敏后的手机号 | [ ] |
| 11 | 硬性阻断不产生出站 | 构造一条命中 `block` 的请求 → `outbound.kind == "blocked"`，且 `chat_message` 里没有业务内容 | [ ] |
| 12 | 同会话互斥 | 同时发两条消息 → 第二条 409 `busy` | [ ] |

---

## 10. 故障对照表

| 症状 | 最可能原因 | 处理 |
|---|---|---|
| `No module named 'langgraph.checkpoint.postgres'` | 没装 checkpoint-postgres | `pip install "langgraph-checkpoint-postgres>=2.0"` |
| `connection refused` 到 55432 | 容器没起 / 端口冲突 | `docker compose ps`；冲突则改映射并同步 `.env` |
| 卡在 `Downloading model.safetensors` | HuggingFace 直连慢 | 设 `HF_ENDPOINT=https://hf-mirror.com`，或用本地模型路径 |
| `Milvus Proxy successfully initialized` 之后仍连不上 | 首次启动还没就绪 | 等 1–2 分钟再试；`docker compose logs milvus` |
| `collection not found: kb_chunks` | 没跑 seed | `python scripts/seed_kb.py` |
| 检索永远为空 | `kb_document.status` 不是 approved | 见第 3 步的 SQL |
| 检索内容不相关 | 阈值/召回数不合适 | 调 `MIN_RERANK` / `RECALL_K`（`mvp-config-rules.md` §5.4 有三档推荐） |
| 意图恒为 `clarify` | 模型名/密钥/温度 | 单独验 `classify`；确认 `.env` 的 `DEEPSEEK_MODEL` 正确 |
| 模型返回不是合法 JSON | JSON 模式未生效 | 确认 prompt 含 "json" 字样；换支持 JSON 模式的模型 |
| 预约类总是 `need_info` | 会话没绑 user_id → `auth.verified=false` | CLI 加 `--user <uuid>`；API 建会话时传 `user_id` |
| 改约报"状态已变化" | 乐观锁 version 不匹配 | 重新 seed 该预约 |
| 控制台中文乱码 | Windows 控制台编码 | `chcp 65001`，或设 `PYTHONIOENCODING=utf-8` |
| 面板打不开 | 路径错 | `http://127.0.0.1:8077/ops/panel`（直接访问，不要走 `/docs`） |
| SSE 只有 `status` 没有 `final` | 图内部报错 | 看服务端日志；流里会发 `error` 事件带原因 |
| 服务启动即退出 | 缺 `DEEPSEEK_API_KEY` 或 `PG_DSN` | `settings.validate()` 会在启动时报出缺哪项 |
| `No module named 'pkg_resources'` | pymilvus 2.4.x 依赖它，而 setuptools ≥ 81 已移除 | `pip install "setuptools<81"`（已写进 requirements.txt） |
| `IndexParams has no attribute 'add_field'` | pymilvus 2.4 里索引用 `add_index`（`add_field` 是 schema 的方法） | 已修 `app/services/milvus_store.py` |
| 重跑 seed 后向量变多（重复数据） | 集合用了 `auto_id=True`，upsert 无法定位主键 | 已改为 `auto_id=False` + 显式 `pk=chunk_id` |
| `the number of fields is less than needed` | 集合 schema 与写入字段数不一致 | `python scripts/seed_kb.py --recreate` 重建集合 |
| 模型下载见 §0.2 的五个坑 | HF_ENDPOINT / HF_HOME / symlink / xet / .DS_Store | 见 §0.2 表格 |

---

## 11. 结果记录表（填完就是一份可交付的自测报告）

| 项目 | 结果 | 备注 |
|---|---|---|
| 环境（OS / Python / Docker） | | |
| BGE-M3 加载耗时 / 设备 | | |
| 第一轮端到端耗时 | | |
| 科普场景延迟 / 是否发生修订 | | |
| 预约场景延迟 / 是否幂等 | | |
| 紧急场景：规则命中，模型是否误报 | | |
| 检索召回质量（人工判断 1–5 分） | | |
| 专家意见差异度（三份是否雷同） | | |
| 审查降级（超时→转人工）是否生效 | | |
| 面板全流程是否顺畅 | | |
| 发现的问题（按严重度） | | |
| 需要改的规则/参数 | | |

---

## 已知的、这份清单无法覆盖的事项

1. **规则资产未经审定**：`app/config/rules.yaml` 里的紧急词表与高风险标签是草稿 v0.1，
   必须经**医学顾问与法务**过一遍才能上线。自测只能验证"规则是否按预期生效"，
   验证不了"规则本身对不对"。
2. **独立复核缺口**：单模型下 `escalation` 与首次审查同族，不构成独立复核（已在审计里标注）。
   要真正解决，得填 `ESCALATION_MODEL` 接另一家模型 —— 那时图代码一行不改。
3. **`llm_call_log` 未写入**：表建好了，但 `ModelGateway` 还没往里写调用记录。
   成本与延迟观测需要补这一块（约 20 行）。
4. **视觉链路未实现**：图片只入库不解析，转人工时明确告知，不假装看图。

---

## 附：第一次真实依赖跑通时暴露并已修复的缺陷

这一节值得留着，因为它说明**"fake 档位全绿"离"真实档位可用"还有多远** ——
下面 8 个问题里有 6 个在 fake 档位下永远不会出现，而且其中 4 个的表现和真实原因完全对不上。

| # | 现象 | 真实原因 | 为什么 fake 档位测不出来 |
|---|---|---|---|
| 1 | 预约类请求**一律**判 `need_info`（"身份未核验"） | `normalize` 里先 `load_auth` 再 `ensure_session`：首次会话用户还没绑上，算出的 `verified=false` 被存进 state | fake 的 `load_auth` 恒返回 `verified=true` |
| 2 | 日志/管道下进程直接崩（`UnicodeEncodeError`） | Windows 上 stdout 被重定向时退回 GBK，而 `✓ ✗ ⏸ ✅ ⛔` **都不在 GBK 里** | 直接跑是控制台 UTF-16 接口，什么都能打 |
| 3 | 科普答案"越改越空"，最后变成"没有可引用的说明" | 事实核对只要有任何非 `supported` 就回炉，3 轮预算耗尽后 `loop_out` 把**已基本成稿的草稿整个丢掉** —— 预算成了质量杀手 | 假模型的 `kb_verify` 只会返回 `supported` |
| 4 | 改约报"**状态已变化，请刷新后重试**"，但库里那行是 `booked/version=1`，从没变过 | 三层叠加：`release` 不传 `appointment_id`、`b_agent` 方案里没有它、SQL 的 `version` 写死为 `1` → 退化成 `WHERE appointment_id = NULL` | fake 的 `change_appointment` 忽略所有参数，恒返回成功 |
| 5 | 改约演示**只能成功跑一次** | 种子用 `ON CONFLICT DO NOTHING`，改约成功后该行 `status='changed'` 而改约只作用于 `booked` → 此后永远"查不到可改约的预约" | fake 是内存字典，每次进程重启都重置 |
| 6 | 非交互环境下 CLI 吐一屏 traceback | `input()` 在无 stdin 时抛 `EOFError` | 交互式运行时不会发生 |
| 7 | 同一句改约两次运行结论不同（一次放行执行、一次转人工 P1） | 三个审查角色的 `temperature=0.1`，审查是**判定**却带采样随机性 | 假模型不走温度 |
| 8 | 空 `claims` 被当成"核对通过" | `kb_enough = not unsupported`，而 `not []` 为真 → **什么都没核对**的草稿直接放行 | 同类：假模型不会返回空 claims |

两条结论，建议带进真实档位的自测：

- **fake 档位证明的是"接线对"，不是"功能对"。** 它做得越省事（忽略参数、恒返回成功、
  恒返回 verified），就越会以"帮你测过了"的方式骗你。第 4、5 两条都是这样漏过去的。
- **报错信息本身要当验收项。** 第 4 条如果直接说"缺少 appointment_id"，
  五分钟就能定位；说成"状态已变化"，会让排查方向整个偏到并发冲突上。
  一个误导性的报错，比一个直接的报错贵得多。

修复后回归：`smoke` 53 项 + `smoke_api` 26 项 + `smoke_ops` 36 项 = **115 项全过**，
`check_env` 18 通过 / 2 提醒 / 0 阻塞，`check_retrieval` 检索链路 OK，
真实档位 `--demo` 四场景退出码 0。
