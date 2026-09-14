"""
模型调用统计：用 app.llm_call_log 回答两个必答题。

    python scripts/llm_stats.py                      # 最近 24 小时，按角色汇总
    python scripts/llm_stats.py --hours 168          # 最近 7 天
    python scripts/llm_stats.py --thread <thread_id> # 某一轮的逐次调用明细
    python scripts/llm_stats.py --failures           # 只看失败/重试的调用

═══ 它回答什么 ═══

  1. **一次问答花多少钱** —— 平均每轮调用几次模型、消耗多少 token。
     这是业务方第一个会问的问题，没有这张表就只能等月底账单，而账单只有一个总数，
     看不出钱花在哪个角色上。
  2. **用户等的十几秒花在哪** —— 哪个角色的平均/P95 延迟最高。
     优化要对着数字做，不是对着感觉做。

═══ 关于费用 ═══

  脚本默认**只报 token、不报金额**：模型单价会变，写死在代码里迟早变成错的数字。
  要知道钱数，把实际单价传进来（单位：元 / 百万 token）：

      python scripts/llm_stats.py --price-in 2 --price-out 8

  注意 `prompt_tokens` / `completion_tokens` 为空的行表示"没拿到用量"
  （不是 0）—— 统计时被算作未知，不计入 token 合计，但在"未知用量"一列里单独示警。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402,F401  （启用控制台 UTF-8 兜底）

from app.settings import Settings  # noqa: E402

SUMMARY_SQL = """
SELECT count(*)                                        AS calls,
       count(DISTINCT thread_id)                       AS turns,
       count(*) FILTER (WHERE NOT ok)                  AS failures,
       sum(prompt_tokens)                              AS in_tok,
       sum(completion_tokens)                          AS out_tok,
       count(*) FILTER (WHERE prompt_tokens IS NULL)   AS unknown_usage,
       round(avg(latency_ms))                          AS avg_ms
  FROM app.llm_call_log
 WHERE created_at > now() - make_interval(hours => $1::int)
"""

BY_ROLE_SQL = """
SELECT role,
       model,
       count(*)                                       AS calls,
       count(*) FILTER (WHERE NOT ok)                 AS failures,
       round(avg(latency_ms))                         AS avg_ms,
       max(latency_ms)                                AS max_ms,
       sum(prompt_tokens)                             AS in_tok,
       sum(completion_tokens)                         AS out_tok
  FROM app.llm_call_log
 WHERE created_at > now() - make_interval(hours => $1::int)
 GROUP BY role, model
 ORDER BY calls DESC
"""

BY_THREAD_SQL = """
SELECT role, model, latency_ms, ok, prompt_tokens, completion_tokens, error, created_at
  FROM app.llm_call_log
 WHERE thread_id = $1
 ORDER BY call_id
"""

FAILURES_SQL = """
SELECT created_at, thread_id, role, ok, latency_ms, left(coalesce(error, ''), 110) AS error
  FROM app.llm_call_log
 WHERE created_at > now() - make_interval(hours => $1::int)
   AND NOT ok
 ORDER BY call_id DESC
 LIMIT 40
"""


def _tok(v: int | None) -> str:
    return "—" if v is None else f"{v:,}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24, help="统计最近多少小时（默认 24）")
    ap.add_argument("--thread", default=None, help="只看某一轮（thread_id）的逐次调用")
    ap.add_argument("--failures", action="store_true", help="只列失败/重试的调用")
    ap.add_argument("--price-in", type=float, default=None, help="输入单价（元/百万 token）")
    ap.add_argument("--price-out", type=float, default=None, help="输出单价（元/百万 token）")
    args = ap.parse_args()

    s = Settings()
    conn = await asyncpg.connect(s.pg_dsn)
    try:
        # ── 单轮明细 ──
        if args.thread:
            rows = await conn.fetch(BY_THREAD_SQL, args.thread)
            if not rows:
                print(f"没有找到 thread_id = {args.thread} 的调用记录。")
                print("提示：thread_id 为空的记录说明那一轮没有设置追踪上下文 ——")
                print("      CLI/API 入口会自动设置；直接调 graph.ainvoke 的脚本需要自己包 turn_scope。")
                return 1
            print(f"轮次 {args.thread}：共 {len(rows)} 次模型调用\n")
            print(f"{'#':>3}  {'role':<22}{'耗时ms':>8}  {'入tok':>8}  {'出tok':>8}  状态")
            print("─" * 68)
            total_ms = 0
            for i, r in enumerate(rows, 1):
                total_ms += r["latency_ms"] or 0
                print(f"{i:>3}  {r['role']:<22}{r['latency_ms'] or 0:>8}  "
                      f"{_tok(r['prompt_tokens']):>8}  {_tok(r['completion_tokens']):>8}  "
                      f"{'ok' if r['ok'] else 'FAIL'}")
                if not r["ok"] and r["error"]:
                    print(f"       └─ {r['error'][:100]}")
            print("─" * 68)
            print(f"模型调用累计耗时 {total_ms:,} ms（串行相加，并行分支会重复计入）")
            print("★ 与用户实际等待时间的差额 = 检索/数据库/图调度等非模型开销 ——")
            print("  这个差额比总耗时更有用：它告诉你瓶颈在模型还是在别处。")
            return 0

        # ── 失败明细 ──
        if args.failures:
            rows = await conn.fetch(FAILURES_SQL, args.hours)
            if not rows:
                print(f"最近 {args.hours} 小时没有失败或重试的调用。")
                return 0
            print(f"最近 {args.hours} 小时的失败/重试（最多 40 条）：\n")
            for r in rows:
                print(f"  {r['created_at']:%m-%d %H:%M:%S}  {r['role']:<22}"
                      f"{r['latency_ms'] or 0:>6}ms  {r['error']}")
            print("\n★ 大量 'schema(attempt …)' 说明那个角色的输出结构对不上 —— "
                  "要么改 prompt，要么改 schema。")
            return 0

        # ── 汇总 ──
        summary = await conn.fetchrow(SUMMARY_SQL, args.hours)
        if not summary or not summary["calls"]:
            print(f"最近 {args.hours} 小时没有任何模型调用记录。")
            print("   · 如果确实跑过对话 → 说明 llm_call_log 没接上；")
            print("   · 如果刚跑过 fake 档位 → 那条记录落在内存里，不会进库（这是有意的）。")
            return 1

        calls = summary["calls"]
        turns = summary["turns"] or 0
        in_tok = summary["in_tok"] or 0
        out_tok = summary["out_tok"] or 0
        print(f"最近 {args.hours} 小时：{calls} 次模型调用，"
              f"覆盖 {turns} 轮对话，失败 {summary['failures']} 次\n")

        if summary["unknown_usage"]:
            print(f"⚠ 有 {summary['unknown_usage']} 条没拿到 token 用量（计为未知，未计入合计）\n")

        print(f"{'role':<24}{'model':<18}{'次数':>6}{'失败':>6}{'均耗时':>8}{'最慢':>8}"
              f"{'入tok':>10}{'出tok':>10}")
        print("─" * 96)
        rows = await conn.fetch(BY_ROLE_SQL, args.hours)
        for r in rows:
            print(f"{r['role']:<24}{(r['model'] or ''):<18}{r['calls']:>6}{r['failures']:>6}"
                  f"{r['avg_ms'] or 0:>8}{r['max_ms'] or 0:>8}"
                  f"{_tok(r['in_tok']):>10}{_tok(r['out_tok']):>10}")
        print("─" * 96)
        print(f"{'合计':<24}{'':<18}{calls:>6}{summary['failures']:>6}"
              f"{summary['avg_ms'] or 0:>8}{'':>8}{in_tok:>10,}{out_tok:>10,}")

        if turns:
            print(f"\n每轮平均：{calls / turns:.1f} 次模型调用，"
                  f"{(in_tok + out_tok) / turns:,.0f} tokens")
            if args.price_in is not None:
                print(f"  输入成本 ≈ {in_tok / 1e6 * args.price_in:.4f} 元"
                      f"（按 {args.price_in} 元/百万）")
            if args.price_out is not None:
                print(f"  输出成本 ≈ {out_tok / 1e6 * args.price_out:.4f} 元"
                      f"（按 {args.price_out} 元/百万）")
            if args.price_in is None and args.price_out is None:
                print("  （未提供单价，故不报金额：--price-in / --price-out，单位 元/百万 token）")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
