# 真实依赖自测清单（Postgres + Milvus + BGE + DeepSeek）

> **这份清单验的是"真实依赖下的行为与质量"**，不是"图的接线"——接线已经用 fake 档位验过了
> （112 项断言：`smoke.py` 75 + `smoke_api.py` 37）。所以下面的每一步都写明：**期望看到什么**、**不对时先看哪里**。
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

> ★ **本项目的表都建在 `zhimei` 这个库里**，不会碰到别的项目的库；`app` / `ops`
> 两个 schema 是本项目专用的。

> ⚠️ **升级已有库 = 把 `sql/schema.sql` 再跑一遍**（`check_env.py` 的"下一步"里就是这么写的）。
> 但注意所有建表语句都是 `CREATE TABLE IF NOT EXISTS`，**对已存在的表整条跳过** ——
> 少一列是不会补的。所以每次加列，文末「10. 增量变更」里必须有一条对应的
> `ADD COLUMN IF NOT EXISTS`；跑完请用下面这条命令确认升级真的生效了：
>
> ```bash
> python scripts/check_schema.py     # 在临时库里验证「新建库」与「升级旧库」两条路径
> ```

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
python scripts/demo_api.py --base http://127.0.0.1:8077 --user <user_id>
```

- [ ] 三个场景的事件流与 fake 档位一致：`status…` → `final` / `awaiting_confirmation` → `done`
- [ ] 预约场景：`⏸ 等待确认` 后流结束，`/confirm` 恢复后才有 `final`
- [ ] 紧急场景：`final` 与 `handoff` **两个事件都出现**（并行出口）
- [ ] 过程事件里的文案是固定的几句，**不含模型生成内容**
- [ ] 预约场景带上 `--user` 后，改约是**真的执行**（回执里有预约编号），不是停在挂起

```bash
python scripts/smoke_api.py                  # 37 项（会强制走 fake 档位，用于回归）
```

### 6b. ⭐ 真实 HTTP 冒烟（服务必须真的起着）

```bash
# 终端 A
python -m app.api --host 127.0.0.1 --port 8090
# 终端 B
python scripts/smoke_api_live.py --base http://127.0.0.1:8090 --user 22222222-2222-2222-2222-222222222222   # 53 项
```

> 演示用户的 user_id 现在是**固定的** `22222222-2222-2222-2222-222222222222`。
> 以前每次跑 `seed_kb.py` 都会新建一个用户（`app_user` 主键带 `gen_random_uuid()`，
> 所以 `ON CONFLICT DO NOTHING` 永远不会命中），而演示预约的主键是固定的、
> `DO UPDATE` 里又没更新 `user_id` —— 于是预约一直绑在最早那个用户上，
> 换上新打印的 user_id 反而查不到预约，表现为改约场景莫名降级。
> 现在固定 UUID，`seed_kb.py` 可以随便重复跑。

> ⚠️ **跑 `smoke_api_live.py` 之前必须先跑一次 `python scripts/seed_kb.py`。**
> 改约场景会**真的把演示预约改掉**（它是端到端验收，不是只读探针），
> 所以连跑第二遍时会因为"预约已经不是原来那个状态"而失败 4 项。
> 这 4 项失败**看着像代码坏了，其实只是数据被上一遍消耗掉了** ——
> 先重跑 `seed_kb.py` 再判断，别急着改代码。

**为什么它和 `smoke_api.py` 不是重复的**：`smoke_api.py` 走 `httpx.ASGITransport`，
请求在**同一个进程内**直接交给 ASGI 应用，不经过 uvicorn 的启动流程、不经过网络。
实测它至少漏掉三类问题，全都是"服务根本不可用"级别的：

| 漏掉的问题 | 现象 | 为什么进程内测试看不到 |
|---|---|---|
| uvicorn 事件循环 | `python -m app.api` **启动即失败**（psycopg 不能在 ProactorEventLoop 上跑） | 它压根不经过 uvicorn |
| 坐席 id 类型 | 运营后台「接单」在真实库上 **100% 失败** | fake 是内存字典，非 UUID 字符串照收 |
| 时间戳类型 | 同上，「关单」也失败 | fake 对 ISO 字符串照收 |

- [ ] `GET /api/health` 返回 `profile=real`、`checkpointer=postgres`
- [ ] SSE 的所有 `status` 文案都来自 `STAGE_TEXT` 固定话术表（正文没混进过程通道）
- [ ] 篡改 `plan_hash` 后流程走完，但**绝不执行**
- [ ] 坐席接单后，聊天接口返回 **409 `human_takeover`**；关单后恢复正常
- [ ] `GET /ops/panel` 是页面、`GET /ops/stream?once=true` 首帧是 `snapshot`
- [ ] 未知角色返回 403

> ⚠️ **`python -m app.api` 在 Windows 上的一个坑**（已修，但换机器/升级 uvicorn 后要留意）：
> uvicorn 把 Windows 的默认事件循环**硬编码**成 `ProactorEventLoop`，而 psycopg 的异步
> 连接只认 `SelectorEventLoop`。更绕的是 uvicorn 对 `loop="auto"` 和 `loop="asyncio"`
> **都**返回 Proactor，所以在 `__main__.py` 里 `set_event_loop_policy()` 完全没用
> （循环是在策略生效之前就建好的）。解决办法是给 uvicorn 传一个自定义循环工厂：
> `loop="app.api.loop:selector_loop_factory"`。详见 `app/api/loop.py` 的注释。

### 6c. C 端聊天页与节点执行轨迹

```bash
node scripts/check_ui.cjs      # 静态校验，不需要起服务（语法 / 节点表同步 / DOM id / 测试工单隔离 / 页面启动）
# 浏览器打开 http://127.0.0.1:8090/chat
```

- [ ] 左栏提问后，**过程状态逐条出现**（"正在理解您的问题…"），正文**最后整段到达**
- [ ] **左栏会话列表**：发完第一条消息后，该会话带着标题出现在列表最上方
- [ ] **点「＋ 新对话」**：对话区清空、轨迹清空，但**不立刻建会话**
      （发第一条消息时才建 —— 免得库里堆空会话）
- [ ] **点历史会话**：消息记录完整恢复，且正文带"已审正文"标记
- [ ] **刷新页面**：回到上次那个会话，而不是开一个新的
- [ ] 被坐席接管的会话：打开后**输入框直接禁用**并提示"人工接管"
      （不是让你发出去再收 409）
- [ ] ★ **两个窗口并排做一次完整的转人工**（这一条最值得亲自走一遍）：
      左边 C 端聊天、右边坐席台，然后：
      1. C 端发一句急症话术（如"我做完水光第三天脸发白还特别疼"）→ 出现"转人工"
      2. **坐席台不用刷新**，3 秒内队列里就冒出这张 P0 工单（实时推送）
      3. 坐席台点【接单】→ C 端**输入框立刻变成禁用**（AI 已停用）
      4. 坐席台回一句话 → **C 端 3 秒内自动出现**，带「坐席」角标，无需刷新
      5. 坐席台【关单】→ C 端输入框恢复可用
      > 这五步每一步都曾经是坏的：坐席台刷新要重新登录（`connectStream` 拼错）、
      > 工单不自动出现（实时流压根没连上）、坐席回复永远不出现（C 端从不轮询）。
      > 前两个由 `check_ui.cjs` 的 F 组守着，第三个由 F 组的行为测试守着。
- [ ] 右栏轨迹按顺序实时追加，节点带中文名、子图配色与耗时
- [ ] **点任意节点能展开它的输出**——看到该节点写回的状态
      （意图与槽位 / 检索候选 / 证据筛选 / 逐句核对 / 裁决表）
- [ ] 展开审查类节点能看到 `panel_reviews`（三位专家分别判了什么）
- [ ] 展开后**不会**被新一轮的自动滚动带走（正在读的内容不该被抢走）
- [ ] 科普问题**能看到 `kb_*` 开头的蓝色节点**（说明子图内部节点被带出来了）
- [ ] 改约类问题**能看到操作确认卡片**，确认后才执行
- [ ] 急症问题**只跑十几个节点、且没有 LLM 标记**（规则快路径，不烧 token）
- [ ] 取消勾选「显示执行轨迹」后，右栏不再新增节点（后端确实没发）
- [ ] `node scripts/check_ui.cjs` 全过（35 项）—— 尤其"前端节点表与后端 `add_node` 双向一致"
      （这条守的是跨语言漂移：图里加了新节点、前端没跟上，界面上会冒出"（未登记的节点）"）
      以及 E 组"清库 SQL 必须每条都带 `WHERE is_test = true`"
      （这条守的是**不可逆删除**：子句一旦丢失，代码照跑、测试不报错，
      只会在某次执行时把真实顾客的工单删光）
      以及 F 组"两个页面在桩环境里真跑一遍脚本"
      （A~E 全是静态文本扫描，抓不到"调了一个不存在的函数"这类问题 ——
      语法没错、id 都在、函数名也在别处定义过，只有真的跑一遍才会暴露）

> ⚠️ **展开的节点输出是调试信息**，含**未经审查的中间产物**（草稿、审查意见、检索原文）。
> 顾客只应看到对话区那份「已审正文」。面向真实顾客时必须取消勾选「显示执行轨迹」——
> 后端默认就不发 `node` 事件，勾选框只是演示开关。

---

## 7. 运营后台

浏览器打开 `http://127.0.0.1:8090/ops/panel`（real 档位）；
若起的是 fake 档位（`--profile fake --port 8077`）则换成 `http://127.0.0.1:8077/ops/panel`。
先登录（`service@zhimei.test` / `admin@zhimei.test` 等，密码 `zhimei-demo-2026`）。

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

### 7a. 测试工单的隔离与清理

自动化脚本造的工单和真实顾客的工单结构完全一样，混在一起会毁掉演示、污染 SLA 指标。
判定来源是**会话的 `channel`**：脚本用 `channel='test'`，真实入口用 `web` / `wechat` / `app`。

- [ ] 跑一遍 `python scripts/smoke_ops.py`（它会造一张 P0 工单）
- [ ] 面板队列里**看不到**那张工单（默认 `include_test=false` 把它滤掉了）
- [ ] 勾上右上角**「显示测试工单」** → 那张工单出现，且行上有紫色**「测试」徽章**、
      整行底色降饱和 —— 一眼能和真实工单区分
- [ ] 用 `service` 登录时**看不到**【清理测试工单】按钮；换成 `admin` 才出现
      （前端只是不显示，服务端 `require(agent, PURGE_PERMISSION)` 会再判一次）
- [ ] 点【清理测试工单】→ 提示"已清理 N 张测试工单（真实工单未受影响）"，
      且**真实工单仍在队列里**（这是这个功能唯一的验收点）

```sql
-- 交叉核对：清理前后真实工单一条都不能少
select is_test, count(*) from ops.handoff_ticket group by is_test;
```

- [ ] `is_test = false` 的行数在清理前后**不变**
- [ ] 直接打接口验证权限边界（不该只靠前端藏按钮）：
      `curl -X DELETE http://127.0.0.1:8090/ops/tickets/test -H "Authorization: Bearer <service 的令牌>"`
      → **403**，错误码 `forbidden`

> ★ 前端把 `admin` 的 `{"*"}` 展开成具体权限名（`effective_permissions()`）再下发。
> 直接把星号发给前端的话，每个客户端都得自己实现一遍通配符语义，
> 漏一处就变成"按钮点下去才 403"。

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

```bash
python scripts/llm_stats.py                       # 最近 24 小时，按角色汇总
python scripts/llm_stats.py --hours 168           # 最近 7 天
python scripts/llm_stats.py --thread <thread_id>  # 某一轮的逐次调用明细
python scripts/llm_stats.py --failures            # 失败 / schema 重试的调用
```

- [ ] 一次问答（科普类）的模型调用次数在合理范围（实测 7 次）
- [ ] 每轮 token 量可接受（实测约 7.3k：入 6.1k / 出 1.2k）
- [ ] 没有哪个角色的 P95 延迟异常突出（★ 实测 `classify` 单句意图识别要 2.35 秒，
      是仅次于 `kb_verify` 的第二慢 —— 这是明确的优化点：换小模型或规则前置）
- [ ] `--failures` 里没有大量 `schema(attempt …)`（有就说明那个角色的输出结构对不上）
- [ ] **紧急场景的模型调用次数应为 0** —— 急症走的是规则命中 + 固定模板，
      不烧 token 也不排队等模型。这条同时验证了"急症快路径"是真的快

> 单轮明细里"模型调用累计耗时"是串行相加的，并行的三个审查面板会被重复计入，
> 所以它**大于**用户实际等待时间。两者之差 = 检索 / 数据库 / 图调度的开销 ——
> 这个差额比总耗时更有用，它告诉你瓶颈在模型还是在别处。

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
3. **视觉链路未实现**：图片只入库不解析，转人工时明确告知，不假装看图。

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

以下是**起真实 HTTP 服务**（`python -m app.api`）才暴露的一批 —— 前 8 条是真实依赖，
这 4 条是"真实网络 + 真实服务器进程"：

| # | 现象 | 真实原因 | 为什么进程内测试测不出来 |
|---|---|---|---|
| 9 | `python -m app.api` **启动即失败**（`Psycopg cannot use the 'ProactorEventLoop'`） | uvicorn 把 Windows 的循环硬编码成 ProactorEventLoop，而 `loop="auto"` 与 `loop="asyncio"` **都**返回 Proactor；`set_event_loop_policy()` 也救不了（循环在策略生效前就建好了）→ 必须传自定义循环工厂 | `httpx.ASGITransport` 不经过 uvicorn 的启动流程 |
| 10 | 运营后台「接单」在真实库上 **100% 失败（500）** | `ops.agent_user.agent_id` / `handoff_ticket.assigned_to` 是 **UUID 列**，而代码自己的默认坐席是 `'demo-agent-0001'` | fake 是内存字典，非 UUID 字符串照单全收 → 36 项运营冒烟全绿 |
| 11 | 「关单」同样 500 | `update_ticket` 把值原样绑定，而调用方传的是 `.isoformat()` 字符串；asyncpg 的 TIMESTAMPTZ 只收 `datetime` 对象 | 同上：fake 对字符串照收 |
| 12 | 图执行中抛异常时，用户**什么都收不到** | `_event_source` 只发一个 `error` 事件就结束：没有回答、没有工单、也没人说会跟进 —— 而对客系统里"静默失败"是最糟的一种失败 | 需要真实发生一次异常才看得到，而 fake 档位不会异常 |

以下是**把真实链路跑久一点、再回头翻统计**才暴露的一批。前 12 条靠"单次运行"就能发现，
这 4 条必须看数据：

| # | 现象 | 真实原因 | 为什么单次运行看不出来 |
|---|---|---|---|
| 13 | 一次检索故障让用户**挂了 30 分钟** | Milvus 返回 `inconsistent requery result` 后 pymilvus **内部重试 75 次、跨 30 分钟**才放弃。我们给 LLM 都设了超时，唯独漏了向量检索这条同样依赖外部服务的路径 | 单次跑大多正常；而且 SSE 心跳照发，前端看起来"还在处理"而不是出错 |
| 14 | 「**做完热玛吉脸会肿吗？**」被判成 P1 紧急 | 泛词表里有「**热**」，而它是项目名「**热**玛吉」的第一个字 —— 每句提到热玛吉的话都自带一个"症状词"；再叠加部位词「脸」共现即触发 | 要恰好问到这句。后果是发急诊模板 + 开 P1 工单，而用户真正的问题一个字没回答 |
| 15 | 「脸有点肿正常吗」也判紧急，但「术后会肿几天」不判 | 共现规则不区分"正在发生"和"询问可能性"。抑制词表只有 `会不会/能不能/是否`，覆盖不了 `会…吗 / 正常吗 / 几天` | 属于规则覆盖面问题，跑一两次碰不到 |
| 16 | 二次复核触发率虚高（真实 25%，被算成 43%） | `note_same_family_review` 写的备注行 `escalation_used=true`，任何没排除 `review_kind='note'` 的统计都会把它算成升级 | 这是**统计口径**问题 —— 只有拿数据算比例时才会被它骗 |
| 17 | 加的列在**已有库上根本不会出现**（`column "is_test" does not exist`，500） | 建表全是 `CREATE TABLE IF NOT EXISTS`，它对已存在的表**整条跳过**，少一列也不补；而"升级"的文档做法就是"再跑一遍 schema.sql" | **新建库完全正常**，只有升级旧库才炸 —— 所以开发时永远看不到 |
| 18 | 上面那条修了三遍才真的修好 | `CREATE INDEX` 引用了新列，它排在补列的 ALTER **之前**，报错后**中断整个脚本** —— 补列语句一行都没跑到。中招两处：`idx_app_user_email`（依赖 `email`）、`idx_ticket_queue_real`（依赖 `is_test`） | 两处都紧贴各自的建表语句，看起来是最不可能有问题的位置 |

| 19 | 坐席台**每次刷新都要重新登录** | `bootData()` 里写的是 `connectStream()`，而函数真名是 `connect` → `ReferenceError` 冒到 `start()` 的大 try/catch → 结论"无法连接服务" → 弹登录页。而队列和指标其实**已经加载好了** | 真正的原因被"登录"两个字盖住了；重新登录后就正常，于是看着像"令牌没存住" |
| 20 | 转人工后坐席端不出工单、坐席回复在用户端永远不出现 | 同一个 `connectStream()` 拼错 → **实时流从没连上**（队列只能手动刷新）；用户端的 SSE 是"一轮对话"的流、早断了，而坐席回复走 `/ops/tickets/{id}/reply` 另写一条消息，**用户侧没有任何机制会被告知** | 两条都是"看起来能用"：手动刷新一下就好了，所以一直没被发现 |

第 17、18 条是一对，教训很具体：**"文件里有这行语句"不等于"它被执行了"。**
第 18 条尤其反直觉 —— 我明明在文件里写了补列语句，跑完列还是不在，
因为一条更早的 `CREATE INDEX` 已经让脚本中断了。而且它只在**升级旧库**这条路上出现：
新建库时列和索引一起建，顺序无所谓，所以本地怎么测都是绿的。

现在这条路径由 `python scripts/check_schema.py` 守着：在临时库里把新列删掉模拟旧库，
再跑一遍 schema.sql，验证列补上了、历史行回填成 DEFAULT 而不是 NULL、索引建回来了。
（写完之后特意把那个 `CREATE INDEX` 挪回去验证过一次 —— 这个检查确实会红，
不是个永远绿的摆设。）

第 19、20 条其实是**同一个拼写错误**造成的两个现象。它值得单独记一笔，
因为它暴露的是**错误处理的设计问题**，而不只是笔误：

```js
// 改之前：一个大 try 包住"校验令牌 + 全部初始化"
try {
  const r = await fetch("/ops/auth/me", ...);
  if (!r.ok) { 清掉令牌; 弹登录页; return; }   // ← 任何非 200 都算"没登录"
  await bootData();                          // ← 里面任何一个 bug 也冒到这里
} catch (e) { 弹登录页("无法连接服务"); }      // ← 连"为什么"都不打印
```

三个后果叠在一起，把一次普通的函数名写错包装成了"登录失效"：

1. **`!r.ok` 就删令牌** —— 后端 500、网络抖动，都算"请你重新登录"；
2. **一个大 try 包住初始化** —— 界面渲染的 bug 被归因到认证上；
3. **catch 里不留原始异常** —— 屏幕上只有"无法连接服务"，控制台里什么都没有。

改法是拆成一条明确约定：**只有 401 / 403 才算"没登录"**，其余情况一律
保留令牌并如实报错；初始化每一步各自兜异常，互不连累。

教训：**错误处理里"归错因"比"没处理"更贵。** 没有兜底时异常会直接冒到
控制台，一眼就能看到 `connectStream is not defined`；而"贴心的"兜底把它
翻译成了用户看得懂、开发者却完全被带偏的一句话。

同时补上 `check_ui.cjs` 的 **F 组**：A~E 全是静态文本扫描，抓不到这种东西 ——
语法没错、DOM id 都在、被调的函数名也在别处定义过（只是不叫这个）。
只有给一个最小 DOM 桩 + 假 fetch，把脚本在"已登录、正在刷新"的状态下
**真的跑一遍**才能发现。F 组还会手动触发一次轮询定时器，
断言坐席回复确实被画进了对话区、且不会重复追加。
（同样验证过它会红：把 `connect` 改回 `connectStream`，F 组立刻报
"没请求过 `/ops/stream`" + `ReferenceError: connectStream is not defined`。）

第 16 条尤其值得记住：**算指标之前先搞清"哪些行不该算进来"。**
27 条备注行把升级率从 25% 抬到 43%，而我据此得出的"93% 的升级是浪费的"也跟着错了
（真实是 85%）。结论方向没错，但数字错了 —— 拿错数字去推动优化，比没有数字更危险。

两条结论，建议带进真实档位的自测：

- **fake 档位证明的是"接线对"，不是"功能对"。** 它做得越省事（忽略参数、恒返回成功、
  恒返回 verified、对任何类型照收），就越会以"帮你测过了"的方式骗你。
  第 4、5、10、11 条都是这样漏过去的 —— 它们的共同形状是"**fake 比真实世界宽容**"。
- **报错信息本身要当验收项。** 第 4 条如果直接说"缺少 appointment_id"，
  五分钟就能定位；说成"状态已变化"，会让排查方向整个偏到并发冲突上。
  一个误导性的报错，比一个直接的报错贵得多。

第 10、11 条还留下一条更普遍的教训：**同一个概念在两张表里用了不同类型**
（`ops.handoff_event.actor` 是 TEXT，`assigned_to` 却是 UUID）。这种"同概念不同型"
本身就是坏味道，看到时应当直接统一，而不是去迁就它。判断标准写在
`sql/schema.sql` 末尾的「类型约定」里：**系统自己生成的 id 用 UUID，
外部系统给定的 id 用 TEXT**。

修复后回归：`smoke` 71 + `smoke_api` 26 + `smoke_ops` 36 + `smoke_api_live` 41
= **174 项全过**，外加 `check_ui.cjs` 前端静态校验 5 项，`check_env` 18 通过 / 2 提醒 / 0 阻塞，
`check_retrieval` 检索链路 OK，真实档位 `--demo` 四场景退出码 0。

修复效果（真实档位实测，已排除统计口径干扰）：

| 指标 | 改动前 | 改动后 |
|---|---|---|
| 二次复核触发率 | 25%（20/79） | **11%（2/18）** |
| 其中"三个面板全判低风险无发现"的白升 | 17/20 = 85% | 0 |
| 面板自报置信度分布 | 量子化：0.9×218、**0.4×37** | 收敛到 0.9 附近（ad 0.90 / medical 0.91） |
| 隐私面板弃权率 | 31%（29/95） | 29%（2/7）→ 仍需继续修 Prompt |
| 「做完热玛吉脸会肿吗？」 | 判 P1 紧急 + P1 工单 | 正常回答 |
| 真实急症（脸发白 + 看不清） | P0 | P0（未受影响） |
| 检索故障 | 挂 30 分钟 | 10 秒超时 → 降级为"资料不足"回答 |
