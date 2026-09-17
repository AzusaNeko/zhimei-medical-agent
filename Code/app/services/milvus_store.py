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

    def delete_pks(self, pks: list[int]) -> int:
        """按主键删除向量。

        ★ 为什么需要它：文档**变短**时（改了内容、删了一段），只 upsert 新 chunk
          是不够的 —— 旧的 chunk 行还留在集合里，于是检索会把一段**已经不存在
          于文档中**的文本召回来，并且它还会带着旧的 doc_id 出现在引用里。
          这种"幽灵证据"比检索不到更糟：它看起来有据可依，实际依据早被删了。

        ★ Milvus 的删除是**标记删除**，后续查询会过滤掉，但磁盘空间要等
          compact 才回收。对一个种子脚本来说这完全够用。

        ★ 这里踩过一个**静默**的坑，别再改回去：
          最初写的是 `client.delete(collection_name=..., expr=f"pk in [...]")`。
          pymilvus 2.4 的签名是
              delete(collection_name, ids=None, timeout=None, filter=None,
                     partition_name=None, **kwargs)
          ——参数叫 **filter**，没有 `expr`。多出来的 `expr` 被 `**kwargs` 收走，
          `filter` 保持默认 None，于是报"expr must be string, but NoneType is given"。
          更麻烦的是它**只在真正有条目要删时才炸**：文档没变短的每次 seed 都跳过这段，
          于是这个函数从写下到第一次真删之间，一直看起来是好的。
          自测要覆盖"真的删掉一条"，而不是只测"调用没报错"。
        """
        if not pks:
            return 0
        # 走 ids= 而不是自己拼 filter 表达式：pymilvus 会做参数校验，
        # 拼字符串则可能拼出一个语法合法但语义错的表达式（静默查不到）。
        res = self.client.delete(collection_name=self.collection,
                                 ids=[int(p) for p in pks])
        return int(res.get("delete_count", res.get("delete_cnt", 0)) or 0)

    def list_pk_docs(self) -> list[dict[str, Any]]:
        """列出集合里现存的 (pk, doc_id)，供"以 PG 为准"的对账使用。

        ★ 为什么需要它：只按 PG 里的文档清单去删，救不了**两边已经不一致**的情况 ——
          比如某篇文档在 PG 里被改过名，删除逻辑在旧的 doc_id 下找不到任何行，
          而 Milvus 里那批向量还留着。它们会被检索召回、出现在引用里，
          但 PG 里查无此 chunk —— 一条无法追溯的"证据"。
          所以对账的基准必须是**Milvus 实际有什么**，而不是"我以为我写过什么"。

        ★ 同时返回 doc_id 而不是只返回 pk：调用方需要判断这条向量到底属于
          "已经彻底消失的文档"（真幽灵，该删）还是"仍然存在于 PG、只是不归本脚本管"
          （人工录入的文档，删了就是事故）。只给 pk 的话，调用方无从区分。
        """
        # Milvus 单次 query 有上限（默认 16384），分批取完，避免对账对了个寂寞
        batch, out, offset = 16384, [], 0
        while True:
            rows = self.client.query(
                collection_name=self.collection, filter="pk >= 0",
                output_fields=["pk", "doc_id"], limit=batch, offset=offset)
            out.extend({"pk": int(r["pk"]), "doc_id": r.get("doc_id", "")} for r in rows)
            if len(rows) < batch:
                return out
            offset += batch

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
            # ★★ 必须把 project == "" 一起放进来，不能只写 `project in [...]` ★★
            #
            #   通用资料（通用风险与紧急信号、术后护理总则、资质核实、项目对比、
            #   费用口径、常见问题）**不属于任何单个项目**，落库时 project 存的是空串。
            #   只写 `project in ["热玛吉"]` 会把它们全部排除掉 —— 于是：
            #      用户问"热玛吉有什么风险"，而那份写着"出现下列情况请立即就医"
            #      的通用风险资料，因为不带"热玛吉"标签而被过滤掉了。
            #   这不是"少召回一条"，是把**最该出现的那条**挡在门外：
            #   检索结果看起来仍然很合理（全是热玛吉的资料），
            #   没有任何报错、没有任何日志异常，只有内容悄悄变差。
            #
            #   过滤的本意是"优先本项目的资料"，不是"只准本项目的资料"。
            expr += f' and (project in [{quoted}] or project == "")'
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
