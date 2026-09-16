"""
FastAPI 应用工厂。

启动方式：
    python -m app.api                       # 默认 127.0.0.1:8000，读 .env 的 APP_PROFILE
    uvicorn app.api.app:app --reload        # 等价的 uvicorn 写法

注意：模块级的 app 只创建应用对象与路由，**不会**在导入时连接数据库或校验密钥 ——
真正的初始化发生在 lifespan 里，这样导入即可（测试、文档生成都不需要真依赖）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..ops.routes import router as ops_router
from ..runtime import create_runtime
from ..settings import Settings
from .auth_routes import router as auth_router
from .routes import router

#: 单文件页面目录（C 端聊天页与运营面板一样，无构建步骤、由 FastAPI 直接托管）
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 模型、连接池、checkpointer、图 —— 只初始化一次
        app.state.runtime = await create_runtime(settings)
        try:
            yield
        finally:
            await app.state.runtime.close()

    app = FastAPI(
        title="智美医美顾问 API",
        version="0.1.0",
        description="LangGraph 多 Agent 顾问系统 · 过程流式 + 已审正文整段发送",
        lifespan=lifespan,
    )
    app.include_router(auth_router)     # C 端注册 / 登录 / me
    app.include_router(router)
    app.include_router(ops_router)      # 坐席工作台（/ops/panel 是单文件监控面板）

    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    async def chat_ui() -> HTMLResponse:
        """C 端聊天页（单文件，无构建步骤）。

        页面默认带 ?trace=1 请求节点执行轨迹 —— 那是给**演示与排障**用的。
        真实线上要对顾客隐藏，前端把「显示执行轨迹」勾掉即可
        （后端默认就是不发 node 事件的，见 events.EVENT_NODE）。

        ★ `Cache-Control: no-store` 是必须的，不是可选的。
          这两个单文件页面是**边改边用**的：没有这个头，浏览器会按启发式规则
          缓存住旧版本 —— 于是改了前端、刷新页面却还是老行为，
          排查方向会被带偏到"是不是没生效/是不是后端没重启"。
          实测踩到过：修好了一个前端 bug，用户刷新后说"还是老样子"。
        """
        return HTMLResponse((_WEB_DIR / "chat.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store, must-revalidate"})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """把 detail 规范化成 {code, message}，前端不必猜结构。"""
        detail = exc.detail
        if isinstance(detail, dict):
            body = {"code": detail.get("code", "http_error"), **detail}
        else:
            body = {"code": "http_error", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "code": "invalid_request", "message": "请求参数不合法",
            "errors": exc.errors()[:5],
        })

    return app


app = create_app()
