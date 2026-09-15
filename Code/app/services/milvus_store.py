"""
Milvus 混合检索（dense + sparse → RRF 融合）。

合规底线：检索过滤**必须**带 doc_status == "approved" 且未过期，
而且过滤条件要下推到 Milvus（filter_expr），不能在应用层内存过滤 ——
内存过滤意味着过期资料已经被召回了，只是被你丢掉，审计上说不清。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

try:
    from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker
except Exception:  # noqa: BLE001
    AnnSearchRequest = MilvusClient = RRFRanker = None  # type: ignore[assignment]

DENSE_DIM = 1024          # BGE-M3 dense 维度
OUTPUT_FIELDS = ["chunk_id", "doc_id", "title", "text", "project", "version", "doc_type"]


class RecallTimeout(RuntimeError):
    """检索超时（我们自己的预算耗尽，不是 Milvus 报的错）。"""


class MilvusHybridStore:
    def __init__(self, uri: str, collection: str, *, token: str = "",
                 timeout: float = 10.0) -> None:
        if MilvusClient is None:
            raise RuntimeError("未安装 pymilvus：pip install pymilvus")
        self.collection = collection
        self.timeout = float(timeout)
        self.client = MilvusClient(uri=uri, token=token or None)

    # ══════════════ 建集合（幂等，可在启动时调用）══════════════
    def ensure_collection(self, *, recreate: bool = False) -> None:
        from pymilvus import DataType, MilvusException

        if recreate and self.client.has_collection(self.collection):
            self.client.drop_collection(self.collection)
        if self.client.has_collection(self.collection):
            return

        # ★ auto_id=False + 显式主键，而不是 auto_id=True。
        #   原因：**upsert 必须能定位到主键，才能"更新而不是重复插入"**。
        #   用 auto_id=True 时每次 upsert 都由服务端生成新主键，
        #   于是重跑一次 seed 就会灌出一份重复数据 —— 这在跑第二遍时才会暴露。
        #   这里用 PG 里的 chunk_id 当主键，天然幂等。
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("pk", DataType.INT64, is_primary=True)
        schema.add_field("chunk_id", DataType.INT64)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("title", DataType.VARCHAR, max_length=256)
        schema.add_field("project", DataType.VARCHAR, max_length=64)
        schema.add_field("doc_type", DataType.VARCHAR, max_length=32)
        schema.add_field("version", DataType.VARCHAR, max_length=32)
        schema.add_field("doc_status", DataType.VARCHAR, max_length=16)   # approved | draft | offline
        schema.add_field("expire_at", DataType.INT64)                     # 秒级时间戳，0 = 不过期
        schema.add_field("text", DataType.VARCHAR, max_length=4096)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)

        index = self.client.prepare_index_params()
        # ⚠️ pymilvus 2.4 里索引用 add_index（不是 add_field，那个是 schema 的方法），
        #    metric_type 走 **kwargs 传进去
        index.add_index(field_name="dense", index_type="AUTOINDEX", metric_type="COSINE")
        index.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")

        try:
            self.client.create_collection(self.collection, schema=schema, index_params=index)
        except MilvusException as exc:      # 并发创建时可能撞车
            if "already exist" not in str(exc):
                raise

    # ══════════════ 写入 ══════════════
    def upsert_chunks(self, rows: list[dict[str, Any]]) -> int:
        """
        rows 每项需含：chunk_id, doc_id, title, text, project, doc_type,
                      version, doc_status, expire_at, dense, sparse
        """
        if not rows:
            return 0
        return self.client.upsert(collection_name=self.collection, data=rows)

    # ══════════════ 混合检索 ══════════════
    async def search(self, *, query_text: str, query_dense: list[float],
                     query_sparse: dict[str, float], projects: list[str] | None = None,
                     top_k: int = 40, doc_type: str | None = None) -> list[dict[str, Any]]:
        """混合检索，带**自己的超时预算**。

        ★★ 这个超时是拿真实事故换来的，删掉它之前请先读完这段 ★★

        实测某次 Milvus 混合检索返回：

            MilvusException: (code=2200, message=Retry run out of 75 retry times,
              message=incomplete query result, missing id 3, ...,
              inconsistent requery result)
            RPC start: 15:18:30  →  RPC error: 15:48:40

        **pymilvus 内部重试了 75 次，前后跨 30 分钟。** 整个过程里：

          · 用户的那一轮就挂在那里 —— 十来秒的对话变成半小时无响应；
          · 图里的降级逻辑一次都没跑（`kb_evidence` 判"证据不足" → `kb_limit`），
            因为那次调用**根本没有返回**；
          · SSE 心跳还在照常发，所以前端看起来"还在处理"，而不是出错。

        给 LLM 我们都设了超时（T_UNDERSTAND / T_GENERATE / T_REVIEW），
        却把向量检索这条同样依赖外部服务的路径漏掉了 —— 而它恰恰是唯一
        会自己闷头重试半小时的那个。

        ★ 一个必须知道的取舍：`run_in_executor` 里的阻塞调用**无法被真正取消**。
          超时之后那个线程还会把 RPC 跑完，只是结果被丢掉了。也就是说这里买到的是
          "用户不被它拖住"，不是"资源被释放"。真正的解法是给 pymilvus 配更短的重试
          策略（或换用带 deadline 的调用），那属于后续优化；但在那之前，
          这道超时保证了一轮对话不会被一次检索故障拖死。
        """
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._search_sync, query_dense, query_sparse, projects, top_k, doc_type),
                timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise RecallTimeout(
                f"检索超时（{self.timeout:.0f}s）—— Milvus 可能正在重试或不可用") from exc

    def _search_sync(self, q_dense: list[float], q_sparse: dict[str, float],
                     projects: list[str] | None, top_k: int, doc_type: str | None) -> list[dict]:
        now = int(time.time())
        expr = f'doc_status == "approved" and (expire_at == 0 or expire_at > {now})'
        if projects:
            quoted = ", ".join(f'"{p}"' for p in projects)
            expr += f" and project in [{quoted}]"
        if doc_type:
            expr += f' and doc_type == "{doc_type}"'

        reqs = [
            AnnSearchRequest(data=[q_dense], anns_field="dense",
                             param={"metric_type": "COSINE"}, limit=top_k, expr=expr),
            AnnSearchRequest(data=[q_sparse], anns_field="sparse",
                             param={"metric_type": "IP"}, limit=top_k, expr=expr),
        ]
        res = self.client.hybrid_search(
            collection_name=self.collection,
            reqs=reqs,
            ranker=RRFRanker(60),          # RRF 融合两路
            limit=top_k,
            output_fields=OUTPUT_FIELDS,
        )
        hits: list[dict] = []
        for group in res:
            for h in group:
                entity = h.get("entity", {})
                hits.append({
                    "doc_id": entity.get("doc_id"), "title": entity.get("title"),
                    "version": entity.get("version"), "doc_type": entity.get("doc_type"),
                    "project": entity.get("project"), "text": entity.get("text"),
                    "score": float(h.get("distance", 0.0)), "rrf_score": float(h.get("distance", 0.0)),
                })
        return hits

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass
