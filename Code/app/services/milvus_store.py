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


class MilvusHybridStore:
    def __init__(self, uri: str, collection: str, *, token: str = "") -> None:
        if MilvusClient is None:
            raise RuntimeError("未安装 pymilvus：pip install pymilvus")
        self.collection = collection
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
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._search_sync, query_dense, query_sparse, projects, top_k, doc_type)

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
