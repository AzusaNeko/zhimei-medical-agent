"""python -m app.api —— 本地起服务。

    python -m app.api --host 127.0.0.1 --port 8000
    python -m app.api --profile fake          # 不连任何外部依赖，验证接口层

直接调 uvicorn 时必须显式指定循环工厂（原因见 app/api/loop.py 的详细说明）：

    uvicorn app.api.app:app --loop app.api.loop:selector_loop_factory
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# 这里设置策略是为了与 app.runtime 保持一致（任何后续 asyncio.run 都拿到 Selector）。
# ★ 但它【修不了】uvicorn 的启动问题 —— uvicorn 会在这个策略生效之前就把循环建好，
#   而且它把 Windows 上的循环硬编码成 ProactorEventLoop。
#   真正起作用的是下面传给 uvicorn.run 的 loop 参数（自定义工厂）。
if sys.platform == "win32":  # pragma: no cover - 平台相关
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

#: 自定义事件循环工厂的 import 字符串，交给 uvicorn 解析。
#: 不写死成 "asyncio"：uvicorn 在 Windows 上对 auto/asyncio 都返回 ProactorEventLoop，
#: 而 psycopg 的异步连接无法在 Proactor 上运行 —— 表现是服务启动即失败。
_SELECTOR_LOOP = "app.api.loop:selector_loop_factory"


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.api", description="智美医美顾问 API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    parser.add_argument("--profile", choices=["real", "fake"], help="覆盖 APP_PROFILE")
    args = parser.parse_args()

    if args.profile:
        os.environ["APP_PROFILE"] = args.profile

    import uvicorn
    uvicorn.run("app.api.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info",
                loop=_SELECTOR_LOOP)


if __name__ == "__main__":
    main()
