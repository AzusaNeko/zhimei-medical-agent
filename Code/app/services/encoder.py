"""
BGE-M3 编码服务（进程内，dense + sparse 双输出）。

为什么用 FlagEmbedding 而不是 sentence-transformers：
  BGE-M3 的 sparse（learned lexical weights）需要通过官方 FlagEmbedding 的
  `BGEM3FlagModel.encode(return_dense=True, return_sparse=True)` 才能拿到；
  sentence-transformers 的通用 encode() 不暴露 sparse 输出。
  精排模型用同一个库的 FlagReranker，少一个依赖。

★ 性能要点（最容易踩的坑）：
  推理是同步 CPU/GPU 密集调用，直接在 async 节点里调用会卡死整个事件循环，
  所有用户的 SSE 一起停摆。这里统一走「固定大小线程池 + to_thread + 信号量」，
  并把 torch 的线程数设为 1（否则并发数 × torch 线程数会互相抢核，延迟反而抖动）。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# ★ 必须在导入 FlagEmbedding 之前导入 settings：
#   huggingface_hub 在【模块导入时】读取 HF_ENDPOINT 作为常量，
#   晚一步设置环境变量就等于没设 —— 表现是"配了镜像却直连 huggingface.co",
#   然后一路 WinError 10060 超时重试。
from ..settings import settings as _settings  # noqa: F401  (仅为触发 .env 加载)

try:
    from FlagEmbedding import BGEM3FlagModel
except Exception:  # noqa: BLE001
    BGEM3FlagModel = None  # type: ignore[assignment]


class BgeM3Encoder:
    """输出：{'dense': [[float,...], ...], 'sparse': [{token_id: weight}, ...]}"""

    def __init__(self, model_path: str, *, device: str = "cpu", concurrency: int = 4,
                 max_length: int = 1024) -> None:
        if BGEM3FlagModel is None:
            raise RuntimeError("未安装 FlagEmbedding：pip install FlagEmbedding")
        try:
            import torch
            torch.set_num_threads(1)   # ★ 每个任务单线程，并发交给线程池
        except Exception:  # noqa: BLE001
            pass

        self.model = BGEM3FlagModel(model_path, use_fp16=(device != "cpu"), devices=device)
        self.max_length = max_length
        self._concurrency = concurrency
        self._sem = asyncio.Semaphore(concurrency)
        self._pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="bge-m3")

    # ── 同步实现（只在线程池里跑）──
    def _encode_sync(self, texts: list[str]) -> dict[str, list[Any]]:
        out = self.model.encode(
            texts,
            batch_size=min(16, max(1, len(texts))),
            max_length=self.max_length,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        dense = [list(map(float, v)) for v in out["dense_vecs"]]
        sparse = [{str(k): float(w) for k, w in d.items()} for d in out["lexical_weights"]]
        return {"dense": dense, "sparse": sparse}

    async def encode(self, texts: list[str]) -> dict[str, list[Any]]:
        if not texts:
            return {"dense": [], "sparse": []}
        loop = asyncio.get_running_loop()
        async with self._sem:
            return await loop.run_in_executor(self._pool, self._encode_sync, texts)

    async def close(self) -> None:
        self._pool.shutdown(wait=False)
