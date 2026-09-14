"""
运行时装配：把「服务容器 + checkpointer + 编译好的图」组装一次，供 CLI 与 API 共用。

抽出来的原因：
  · CLI 与 API 必须跑同一张图、同一套依赖，否则"演示能跑、接口不能跑"
  · checkpointer 的创建/释放是异步上下文，散落在各处容易泄漏连接
  · 按 thread 的并发锁需要一个进程内的统一位置（同一个 thread 不能并发跑两个 run）
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any

from .graph.build import build_graph
from .services.deps import Deps
from .settings import Settings

# ════════════════════════════════════════════════════════════════
#  Windows 兼容：psycopg 的异步模式只支持 SelectorEventLoop
#
#  Python 3.8+ 在 Windows 上默认用 ProactorEventLoop，AsyncPostgresSaver
#  （底层 psycopg）会直接拒绝：
#     Psycopg cannot use the 'ProactorEventLoop' to run in async mode
#
#  策略必须在【事件循环创建之前】设置 —— 在循环内部再设已经晚了。
#  所以这里在模块导入时执行：uvicorn 与 asyncio.run 都发生在本模块导入之后。
# ════════════════════════════════════════════════════════════════
if sys.platform == "win32":  # pragma: no cover - 平台相关
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@dataclass
class Runtime:
    settings: Settings
    deps: Deps
    graph: Any
    _ctx: Any = None
    _inflight: set[str] = field(default_factory=set)
    _inflight_guard: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ── 并发互斥：同一个 thread_id 不能并发执行两个 run ──
    #    用"在途集合"而不是普通 Lock：这样能立刻判断出"已在跑"并回 409，
    #    而不是让第二个请求静默排队（排队会让用户以为卡住了）。
    async def try_begin(self, thread_id: str) -> bool:
        async with self._inflight_guard:
            if thread_id in self._inflight:
                return False
            self._inflight.add(thread_id)
            return True

    async def end(self, thread_id: str) -> None:
        async with self._inflight_guard:
            self._inflight.discard(thread_id)

    def is_busy(self, thread_id: str) -> bool:
        return thread_id in self._inflight

    def config(self, thread_id: str, *, recursion_limit: int | None = None) -> dict:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": recursion_limit or self.settings.recursion_limit,
        }

    @property
    def checkpointer_kind(self) -> str:
        return "memory" if self._ctx is None else "postgres"

    async def close(self) -> None:
        await self.deps.shutdown()
        if self._ctx is not None:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = None


async def create_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or Settings()
    deps = Deps.build(settings)
    await deps.startup()

    if settings.is_fake:
        from langgraph.checkpoint.memory import InMemorySaver
        checkpointer, ctx = InMemorySaver(), None
    else:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        ctx = AsyncPostgresSaver.from_conn_string(settings.pg_dsn)
        checkpointer = await ctx.__aenter__()
        await checkpointer.setup()      # 首次运行自动创建 lg schema 的表
    return Runtime(settings=settings, deps=deps,
                   graph=build_graph(deps, checkpointer=checkpointer), _ctx=ctx)
