"""
BGE-Reranker 精排服务（进程内）。

只对「融合后的候选」做精排，不要对全量召回做 —— 精排是 O(候选数 × 文本长度) 的开销。
与编码器使用各自独立的线程池与信号量，避免编码和精排互相抢线程。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# ★ 同 encoder.py：必须在 FlagEmbedding 之前导入 settings，
#   否则 HF_ENDPOINT 来不及生效，huggingface_hub 会直连境外站点。
from ..settings import settings as _settings  # noqa: F401  (仅为触发 .env 加载)

try:
    from FlagEmbedding import FlagReranker
except Exception:  # noqa: BLE001
    FlagReranker = None  # type: ignore[assignment]


class BgeReranker:
    def __init__(self, model_path: str, *, device: str = "cpu", concurrency: int = 4,
                 max_length: int = 512) -> None:
        if FlagReranker is None:
            raise RuntimeError("未安装 FlagEmbedding：pip install FlagEmbedding")
        try:
            import torch
            torch.set_num_threads(1)
        except Exception:  # noqa: BLE001
            pass

        self.model = FlagReranker(model_path, use_fp16=(device != "cpu"), devices=device)
        self.max_length = max_length
        self._sem = asyncio.Semaphore(concurrency)
        self._pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="bge-rerank")

    def _score_sync(self, query: str, docs: list[str]) -> list[float]:
        pairs = [[query, d] for d in docs]
        scores = self.model.compute_score(pairs, max_length=self.max_length, normalize=True)
        if isinstance(scores, float):     # 单条时返回标量
            return [float(scores)]
        return [float(s) for s in scores]

    async def score(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        loop = asyncio.get_running_loop()
        async with self._sem:
            return await loop.run_in_executor(self._pool, self._score_sync, query, docs)

    async def close(self) -> None:
        self._pool.shutdown(wait=False)
