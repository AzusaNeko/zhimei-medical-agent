"""
一轮对话的追踪上下文。

═══ 解决什么问题 ═══

`app.llm_call_log` 每一行都要记「这次模型调用属于哪一轮对话」（`thread_id`），
但模型网关的签名里**没有** thread_id：

    async def structured(self, role, schema, *, system, user) -> TModel

它只知道「我是哪个角色」，不知道「我在为哪一轮服务」。而改签名意味着动 14 个调用点，
代价大且容易漏。用 ContextVar 就绕开了这个矛盾：**谁发起这一轮，谁设一次上下文**，
网关只管读。

═══ 为什么是 ContextVar 而不是全局变量 ═══

ContextVar 的值跟着**执行上下文**走，而 asyncio 在创建任务时会复制当前上下文。
所以：

  · 每个 HTTP 请求 / 每轮对话各自一份，并发会话之间**不会串**（这是关键）；
  · 图内部通过 `asyncio.create_task` 起的并发分支（比如三个审查面板并行）
    会自动继承同一个 thread_id，不需要逐个透传。

用模块级全局变量就会串 —— 两个用户同时说话时，日志里的 thread_id 会互相覆盖。

═══ 一个容易踩的点 ═══

ContextVar 的 `reset(token)` 要求 token 与当前上下文匹配。异步生成器 / 跨任务边界上
可能不匹配并抛 `ValueError`。这里**吞掉这个异常**是有意的：那时说明上下文已经换了，
旧值本来就该随那个上下文一起消失，而不是把整个请求带崩。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_TURN_ID: ContextVar[str | None] = ContextVar("zhimei_turn_id", default=None)


def current_turn_id() -> str | None:
    """当前这一轮的 thread_id；不在任何一轮里时返回 None（写库时允许为空）。"""
    return _TURN_ID.get()


def set_turn(thread_id: str | None) -> None:
    """设置当前上下文的一轮标识（不做还原）。

    适用场景：ASGI 请求处理函数 —— 每个请求本来就是独立的任务、持有上下文的副本，
    请求结束时整个上下文一起丢弃，不存在"泄漏给下一个请求"的问题。
    在**同一个任务里连续跑多轮**（CLI 演示、冒烟脚本）时，每轮开头都会重新设一次，
    所以同样正确。
    """
    _TURN_ID.set(thread_id)


@contextmanager
def turn_scope(thread_id: str | None) -> Iterator[None]:
    """带还原的一轮作用域。适合 CLI、测试这类"同一任务里连续跑多轮"的场景。"""
    token = _TURN_ID.set(thread_id)
    try:
        yield
    finally:
        try:
            _TURN_ID.reset(token)
        except ValueError:
            # 跨任务/生成器边界导致 token 不匹配：此时旧上下文已随任务结束而消失，
            # 不需要（也不能）还原。宁可跳过还原，也不要让请求因为记日志而崩。
            pass
