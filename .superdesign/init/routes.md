# Routes

Backend: FastAPI (Python). Routers are assembled in `Code/app/api/app.py::create_app`:

```python
app.include_router(auth_router)     # C 端注册 / 登录 / me
app.include_router(router)
app.include_router(ops_router)      # 坐席工作台（/ops/panel 是单文件监控面板）
```

Router prefixes (read from source, not inferred):

| Module | Prefix | Source |
|---|---|---|
| `Code/app/api/auth_routes.py` | `/api/auth` | `router = APIRouter(prefix="/api/auth", tags=["auth"])` |
| `Code/app/api/routes.py` | `/api` | `router = APIRouter(prefix="/api")` |
| `Code/app/ops/routes.py` | `/ops` | `router = APIRouter(prefix="/ops")` |
| `Code/app/api/app.py` | *(none)* | page route declared directly on the app |

> **IMPORTANT — there is no `/` root route.** `chat.html` is served at **`/chat`**, and there is no
> `@app.get("/")`, no `RedirectResponse`, no `StaticFiles` mount anywhere in `Code/app`. A request
> to `/` returns FastAPI's 404. The ops panel is served at **`/ops/panel`**.
> (Grep across `Code/**/*.py` for `@app.get(|@router.get("/"|RedirectResponse|StaticFiles` returns
> exactly one hit: the `/chat` route in `app.py`.)

---

## 1. Page-level routes (serve HTML)

### `GET /chat` → `Code/app/web/chat.html`

`Code/app/api/app.py` lines 52–67. The main product UI — customer-facing chat page.
Reads `app/web/chat.html` off disk and returns it as `HTMLResponse` with
`Cache-Control: no-store, must-revalidate`. Not listed in the OpenAPI schema
(`include_in_schema=False`).

```python
    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    async def chat_ui() -> HTMLResponse:
        """C 端聊天页（单文件，无构建步骤）。"""
        return HTMLResponse((_WEB_DIR / "chat.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store, must-revalidate"})
```

### `GET /ops/panel` → `Code/app/ops/panel.html`

`Code/app/ops/routes.py` lines 341–347. The ops console — agent workbench for high-risk replies.
Returns 500 with `<h1>panel.html 缺失</h1>` if the file is missing; otherwise the file contents
with `Cache-Control: no-store, must-revalidate`.

```python
@router.get("/panel", response_class=HTMLResponse)
async def panel() -> HTMLResponse:
    if not PANEL_FILE.exists():
        return HTMLResponse("<h1>panel.html 缺失</h1>", status_code=500)
    return HTMLResponse(PANEL_FILE.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store, must-revalidate"})
```

### `GET /docs` and `GET /openapi.json`

Auto-provided by FastAPI (`FastAPI(title="智美医美顾问 API", version="0.1.0", ...)`).
No HTML page file; useful only as an API reference.

---

## 2. Customer-facing API — `Code/app/api/auth_routes.py` (prefix `/api/auth`)

| Method | Path | Handler | Summary |
|---|---|---|---|
| POST | `/api/auth/register` | `register` | 邮箱+密码注册；演示环境在响应里直接回 `verify_token` |
| POST | `/api/auth/verify-email` | `verify_email` | 用验证令牌完成邮箱验证 |
| POST | `/api/auth/login` | `login` | C 端登录，返回 `access_token` + `user` |
| GET | `/api/auth/me` | `me` | 当前用户（令牌校验；页面启动时的第一件事） |

```python
@router.post("/register")
async def register(body: RegisterIn, request: Request) -> dict:
```

```python
@router.post("/verify-email")
async def verify_email(body: VerifyIn, request: Request) -> dict:
```

```python
@router.post("/login")
async def login(body: LoginIn, request: Request) -> dict:
```

```python
@router.get("/me")
async def me(request: Request, principal: A.Principal = Depends(current_user)) -> dict:
```

Error codes returned as `{code, message}`: `bad_credentials`, `user_disabled`,
`email_not_verified`, `email_taken`, `invalid_verify_token`, `invalid_email`, `weak_password`.

---

## 3. Chat / session API — `Code/app/api/routes.py` (prefix `/api`)

| Method | Path | Handler | Summary |
|---|---|---|---|
| GET | `/api/health` | `health` | 健康检查；返回 `status` / `profile` / `checkpointer` / `graph`（顶栏"档位"标签用它） |
| POST | `/api/sessions` | `create_session` | 新建会话（`channel` 默认 `web`），返回 `session_id` / `thread_id` |
| GET | `/api/sessions` | `list_sessions` | 当前用户的会话列表（左栏历史；`limit`、`channel` 可选） |
| GET | `/api/sessions/{session_id}` | `get_session` | 会话信息 + 最近消息（出站内容已脱敏）；`limit` 默认 10 |
| DELETE | `/api/sessions/{session_id}` | `delete_session` | 删除对话（消息/会话行/工单快照/图状态）；有未结束工单时 409 |
| GET | `/api/graph` | `graph_topology` | 工作流拓扑（右栏 SVG 架构图的数据源） |
| POST | `/api/chat/{session_id}/stream` | `chat_stream` | ★ SSE：一轮对话（`?trace=1` 才发 node 事件） |
| POST | `/api/chat/{session_id}/confirm` | `chat_confirm` | ★ SSE：用户确认/取消后恢复挂起的图（`?trace=1`） |

```python
router = APIRouter(prefix="/api")
```

```python
@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
```

```python
@router.post("/sessions", response_model=SessionOut)
async def create_session(body: CreateSessionIn, request: Request,
                         user: Principal = Depends(current_user)) -> SessionOut:
```

```python
@router.get("/sessions")
async def list_sessions(request: Request,
                        limit: int = 30,
                        channel: str | None = None,
                        user: Principal = Depends(current_user)) -> dict:
```

```python
@router.get("/graph", response_model=dict)
async def graph_topology(request: Request,
                         user: Principal = Depends(current_user)) -> dict:
```

```python
@router.delete("/sessions/{session_id}", response_model=dict)
async def delete_session(session_id: str, request: Request,
                         user: Principal = Depends(current_user)) -> dict:
```

```python
@router.get("/sessions/{session_id}", response_model=dict)
async def get_session(session_id: str, request: Request, limit: int = 10,
                      user: Principal = Depends(current_user)) -> dict:
```

```python
@router.post("/chat/{session_id}/stream")
async def chat_stream(session_id: str, body: ChatIn, request: Request,
                      trace: bool = Query(default=False,
                                          description="是否发送节点执行轨迹（node 事件）。"
                                                      "演示与排障用；生产环境保持 false"),
                      user: Principal = Depends(current_user)) -> StreamingResponse:
```

```python
@router.post("/chat/{session_id}/confirm")
async def chat_confirm(session_id: str, body: ConfirmIn, request: Request,
                       trace: bool = Query(default=False),
                       user: Principal = Depends(current_user)) -> StreamingResponse:
```

SSE event contract (`Code/app/api/events.py` lines 13–41) — the two `/api/chat/...` routes are the
only producers:

```python
EVENT_STATUS = "status"
EVENT_AWAITING = "awaiting_confirmation"
EVENT_FINAL = "final"
EVENT_HANDOFF = "handoff"
EVENT_BLOCKED = "blocked"
EVENT_DONE = "done"
EVENT_ERROR = "error"
EVENT_NODE = "node"
EVENT_TAKEOVER = "human_takeover"
```

---

## 4. Agent-facing API — `Code/app/ops/routes.py` (prefix `/ops`)

### 4.1 Auth

| Method | Path | Handler | Summary |
|---|---|---|---|
| POST | `/ops/auth/login` | `agent_login` | 坐席登录；角色由数据库决定，返回 `permissions` 清单 |
| GET | `/ops/auth/me` | `agent_me` | 当前坐席 + 权限 + `sees_raw_pii` |

```python
@router.post("/auth/login")
async def agent_login(body: AgentLoginIn, request: Request) -> dict:
```

```python
@router.get("/auth/me")
async def agent_me(agent: Agent = Depends(current_agent)) -> dict:
```

### 4.2 Queue, detail, disposition

| Method | Path | Handler | Permission | Summary |
|---|---|---|---|---|
| GET | `/ops/tickets` | `list_tickets` | `ticket:read` | 队列（`status[]`、`priority[]`、`include_test`、`limit=50`） |
| DELETE | `/ops/tickets/test` | `purge_test_tickets` | `PURGE_PERMISSION` (`admin:purge`) | 只清 `is_test=true` 的工单，真实工单不受影响 |
| GET | `/ops/tickets/{ticket_id}` | `ticket_detail` | `ticket:read` | 详情：画像 / 最近对话 / 风险报告 |
| POST | `/ops/tickets/{ticket_id}/accept` | `accept` | `ticket:accept` | 接单（→ 关掉 AI） |
| POST | `/ops/tickets/{ticket_id}/reply` | `reply` | `ticket:reply` | 回复；命中 revise 级规则先回 409 `soft_warning` |
| POST | `/ops/tickets/{ticket_id}/escalate` | `escalate` | `ticket:escalate` | 升级给值班医师 |
| POST | `/ops/tickets/{ticket_id}/close` | `close` | `ticket:close` | 关闭（→ 恢复 AI），原因必填 |
| POST | `/ops/tickets/{ticket_id}/reopen` | `reopen` | `ticket:close` | 重新打开 |
| POST | `/ops/tickets/{ticket_id}/misreport` | `misreport` | `misreport:write` | 标记误报（词表调优的唯一数据来源） |
| GET | `/ops/metrics` | `metrics` | `ticket:read` | 指标看板 |
| GET | `/ops/stream` | `stream` | `ticket:read` | SSE：队列快照 + SLA 告警（`include_test`、`once`） |
| GET | `/ops/panel` | `panel` | — | 监控面板（单文件 HTML，见 §1） |

```python
@router.get("/tickets")
async def list_tickets(request: Request,
                       status: list[str] | None = Query(default=None),
                       priority: list[str] | None = Query(default=None),
                       include_test: bool = Query(default=False,
                                                  description="是否包含测试工单（channel=test）"),
                       limit: int = 50,
                       agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.delete("/tickets/test")
async def purge_test_tickets(request: Request,
                             agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.get("/tickets/{ticket_id}")
async def ticket_detail(ticket_id: str, request: Request,
                        agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.post("/tickets/{ticket_id}/accept")
async def accept(ticket_id: str, request: Request,
                 agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.post("/tickets/{ticket_id}/reply")
async def reply(ticket_id: str, body: ReplyIn, request: Request,
                agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.post("/tickets/{ticket_id}/escalate")
async def escalate(ticket_id: str, body: EscalateIn, request: Request,
                   agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.post("/tickets/{ticket_id}/close")
async def close(ticket_id: str, body: CloseIn, request: Request,
                agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.post("/tickets/{ticket_id}/reopen")
async def reopen(ticket_id: str, request: Request,
                 agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.post("/tickets/{ticket_id}/misreport")
async def misreport(ticket_id: str, body: MisreportIn, request: Request,
                    agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.get("/metrics")
async def metrics(request: Request, agent: Agent = Depends(current_agent)) -> dict:
```

```python
@router.get("/stream")
async def stream(request: Request,
                 once: bool = Query(default=False,
                                    description="只推一帧就结束（轮询型客户端与测试用）"),
                 include_test: bool = Query(default=False,
                                            description="是否包含测试工单（与 /tickets 同一个开关）"),
                 agent: Agent = Depends(current_agent)) -> StreamingResponse:
```

`/ops/stream` emits exactly two event names (`Code/app/ops/routes.py` lines 288–302):

```python
                yield _sse("snapshot", {
                    "tickets": [_queue_row(t) for t in tickets],
                    "metrics": await service.metrics(rt, agent),
                })
                # SLA 告警：只推跨过阈值的那一次，避免每 3 秒重复刷屏
                for t in tickets:
                    key = t["ticket_id"]
                    if t["sla_breached"] and key not in last_alert:
                        last_alert.add(key)
                        yield _sse("alert", {
                            "ticket_id": key,
                            "priority": t["priority"],
                            "wait_seconds": t["wait_seconds"],
                            "message": f'{t["priority"]} 工单已等待 {t["wait_seconds"]} 秒未接单',
                        })
```

---

## 5. Route → page map

```
GET  /chat                                         → Code/app/web/chat.html   (C 端聊天页)
GET  /ops/panel                                    → Code/app/ops/panel.html  (坐席工作台)

page /chat calls:
  GET    /api/health
  GET    /api/auth/me
  POST   /api/auth/login
  POST   /api/auth/register
  POST   /api/auth/verify-email
  GET    /api/sessions?channel=web&limit=40
  POST   /api/sessions
  GET    /api/sessions/{session_id}?limit=60
  DELETE /api/sessions/{session_id}
  GET    /api/graph
  POST   /api/chat/{session_id}/stream?trace=1
  POST   /api/chat/{session_id}/confirm

page /ops/panel calls:
  GET    /ops/auth/me
  POST   /ops/auth/login
  GET    /ops/tickets?include_test={true|false}
  DELETE /ops/tickets/test
  GET    /ops/tickets/{ticket_id}
  POST   /ops/tickets/{ticket_id}/accept
  POST   /ops/tickets/{ticket_id}/reply
  POST   /ops/tickets/{ticket_id}/escalate
  POST   /ops/tickets/{ticket_id}/close
  POST   /ops/tickets/{ticket_id}/misreport
  GET    /ops/metrics
  GET    /ops/stream?include_test={true|false}     (SSE via fetch + reader)
```

Not called by either page (available, backend-only): `POST /ops/tickets/{id}/reopen`,
`GET /ops/stream?once=true`, `GET /docs`, `GET /openapi.json`.

---

## 6. Server entry point

`Code/app/api/__main__.py` — default `127.0.0.1:8000`, so the two pages are reached at
`http://127.0.0.1:8000/chat` and `http://127.0.0.1:8000/ops/panel`.

```python
def main() -> None:
    parser = argparse.ArgumentParser(prog="app.api", description="智美医美顾问 API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    parser.add_argument("--profile", choices=["real", "fake"], help="覆盖 APP_PROFILE")
    args = parser.parse_args()
```
