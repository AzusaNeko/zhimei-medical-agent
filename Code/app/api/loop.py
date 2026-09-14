"""
uvicorn 的事件循环工厂（Windows 专用补偿）。

═══ 为什么需要这个文件 ═══

现象：`python -m app.api` 在 Windows 上**根本起不来**，启动即失败：

    psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in
    async mode.

而 `python -m app.cli` 却一直好好的。差别在**事件循环是谁创建的**：

  · CLI 路径：`app/cli.py` 先 import `app.runtime`（它在 import 时设置
    `WindowsSelectorEventLoopPolicy`），之后才调 `asyncio.run()` —— 策略先生效，
    创建出来的就是 SelectorEventLoop，psycopg 正常工作。

  · uvicorn 路径：`uvicorn.run()` 会**先创建事件循环**，再在 `serve()` 里 import
    应用模块。等 `app.runtime` 那行策略执行时，循环早就建好了 —— 策略完全来不及生效。

更要命的是，光在 `__main__.py` 里设策略也没用，因为 uvicorn 把循环写死了：

    def asyncio_loop_factory(use_subprocess=False):
        if sys.platform == "win32" and not use_subprocess:
            return asyncio.ProactorEventLoop      # ← auto 和 asyncio 都走这里
        return asyncio.SelectorEventLoop

也就是说 `loop="auto"`（默认）和 `loop="asyncio"` 在 Windows 上都拿到
**ProactorEventLoop**，`set_event_loop_policy()` 形同虚设 —— 这是 uvicorn 的
既定行为，不是配置错误，所以只能由我们提供一个自定义工厂绕开它。

═══ 用法 ═══

    python -m app.api                                  # 已内置，无需额外参数
    uvicorn app.api.app:app --loop app.api.loop:selector_loop_factory   # 直接调 uvicorn 时

★ 不要删除下面这个工厂、也不要以为"设了策略就够了"：两者解决的不是同一件事。
  策略只能影响**之后**创建的循环，而这里的循环是在策略生效**之前**创建的。

═══ 签名陷阱（改这个函数前必读）═══

uvicorn 对**自定义** loop 字符串和内置名的处理方式不一样：

    def get_loop_factory(self):
        if self.loop in LOOP_FACTORIES:                  # auto / asyncio / uvloop
            loop_factory = import_from_string(LOOP_FACTORIES[self.loop])
            ...
            return loop_factory(use_subprocess=...)      # ← 内置名会被调用
        return import_from_string(self.loop)             # ← 自定义字符串【原样返回】

也就是说自定义工厂**不会**被 uvicorn 调用，它会被直接交给
`asyncio.Runner(loop_factory=...)`，再由 Runner 以**无参数**形式调用一次，
并且**期望拿到一个事件循环实例**。

第一版就栽在这里：写成 `factory(use_subprocess=False) -> asyncio.SelectorEventLoop`
（照抄内置工厂的签名），于是 Runner 拿到的是**类**而不是实例，报出来的错是

    TypeError: BaseEventLoop.create_task() missing 1 required positional argument: 'coro'

—— 信息量几乎为零，而且它还会在 Runner.close() 里再抛一个新的 TypeError 把
真正的错误盖掉。所以这里的签名必须是「无参数、返回实例」。
"""

from __future__ import annotations

import asyncio


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """创建一个 SelectorEventLoop 实例（不是类！不是类！）。

    Windows 上 `asyncio.SelectorEventLoop` 实际是 `_WindowsSelectorEventLoop`，
    psycopg 的异步连接只认它（Proactor 会直接拒绝）。
    """
    return asyncio.SelectorEventLoop()
