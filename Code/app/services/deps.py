"""
依赖容器：所有服务在这里组装一次，节点通过闭包拿到它。

为什么用闭包工厂而不是把 deps 塞进 config：
  · 显式：节点签名只能是 (state)，不会出现"某个节点悄悄依赖了全局单例"
  · 可测：换一套假实现就是换一个 Deps 实例，图代码零改动
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..settings import Settings
from . import llm as llm_mod
from .rules import RuleEngine
from .security import SecurityService

CST = timezone(timedelta(hours=8))


@dataclass
class Deps:
    settings: Settings
    llm: llm_mod.ModelGateway
    rules: RuleEngine
    security: SecurityService
    encoder: Any
    reranker: Any
    vector_store: Any
    pg: Any
    profile: Any = None

    # ── 时间：从服务端取，绝不让模型猜今天几号 ──
    def now(self) -> datetime:
        return datetime.now(CST)

    def now_iso(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def now_plus(self, *, seconds: int) -> str:
        return (self.now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")

    # ── 组装 ──
    @classmethod
    def build(cls, settings: Settings, *, rules: RuleEngine | None = None) -> "Deps":
        rules = rules or RuleEngine.from_file(settings.rules_file())
        security = SecurityService(settings.release_secret, settings.token_ttl_seconds)

        if settings.is_fake:
            from . import fakes
            llm = llm_mod.ScriptedGateway(settings)
            encoder, reranker = fakes.FakeEncoder(), fakes.FakeReranker()
            vector_store, pg = fakes.FakeVectorStore(), fakes.FakePg()
        else:
            from .encoder import BgeM3Encoder
            from .milvus_store import MilvusHybridStore
            from .pg import PgStore
            from .reranker import BgeReranker
            settings.validate()
            llm = llm_mod.DeepSeekGateway(settings)
            encoder = BgeM3Encoder(settings.bge_m3_path, device=settings.embed_device,
                                   concurrency=settings.embed_concurrency)
            reranker = BgeReranker(settings.bge_reranker_path, device=settings.rerank_device,
                                   concurrency=settings.rerank_concurrency)
            vector_store = MilvusHybridStore(settings.milvus_uri, settings.milvus_collection,
                                             token=settings.milvus_token)
            pg = PgStore(settings.pg_dsn)

        return cls(settings=settings, llm=llm, rules=rules, security=security,
                   encoder=encoder, reranker=reranker, vector_store=vector_store, pg=pg)

    async def startup(self) -> None:
        connect = getattr(self.pg, "connect", None)
        if connect is not None:
            await connect()
        ensure = getattr(self.vector_store, "ensure_collection", None)
        if ensure is not None:
            ensure()

    async def shutdown(self) -> None:
        for obj in (self.encoder, self.reranker, self.vector_store, self.pg):
            close = getattr(obj, "close", None)
            if close is None:
                continue
            result = close()
            if hasattr(result, "__await__"):
                await result
