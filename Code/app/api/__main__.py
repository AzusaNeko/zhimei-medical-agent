"""python -m app.api —— 本地起服务。

    python -m app.api --host 127.0.0.1 --port 8000
    python -m app.api --profile fake          # 不连任何外部依赖，验证接口层
"""

from __future__ import annotations

import argparse
import os


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
                reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()
