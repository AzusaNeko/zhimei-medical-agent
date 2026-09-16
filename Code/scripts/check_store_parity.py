"""
存储层一致性检查：PgStore 与 FakePg 的方法签名必须逐字对齐。

    python scripts/check_store_parity.py        （在 Code 目录下执行）

═══ 为什么需要它 ═══

这两个类会互换：fake 档位跑测试、real 档位跑生产。只要有一处不同形，
就会出现最难查的一类 bug —— **在 fake 下全绿、上真实库就崩**，或者反过来
（真实库正常、fake 档位冒出个假 bug）。

已经真发生过两次，都是靠人肉发现的：

  1. `FakePg` **根本没有 `ensure_session`**（真实档位有）。`normalize` 里用
     `getattr(pg, "ensure_session", None)` 兜住了，所以不报错 —— 代价是会话的
     channel / last_active_at 在 fake 下永远缺失，"按最近活动排序""按渠道过滤"
     这类行为根本测不出来。
  2. 补 `ensure_session` 时顺手把 channel / user_id 也覆盖了，而真实库的
     `ON CONFLICT` 只更新 last_active_at —— 于是"按 channel 过滤会话"
     在 fake 下永远找不到，成了个**只存在于 fake 档位**的假 bug。

两次都不是"忘了写"，而是"两边悄悄长得不一样"。编译器不会提醒，测试也未必覆盖。
所以这里用脚本静态比一遍：**只比签名（方法名 + 参数），不比实现** ——
实现本来就可以不同（一个走 SQL、一个走内存），但接口必须一致。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402,F401

from app.services.fakes import FakePg  # noqa: E402
from app.services.pg import PgStore  # noqa: E402

#: 允许只在真实实现里存在的方法 —— **必须逐个写明理由**，
#: 否则这个检查会退化成"看到红就加白名单"，等于没有。
ALLOWED_ONLY_REAL = {
    # fake 档位没有数据库可连，Deps.startup 用 getattr(pg, "connect", None) 跳过。
    # 语义上它属于"连接外部资源"，不是接口约定的一部分。
    "connect": "fake 无外部连接；调用方已用 getattr 兜底",
}

#: 这些是内部辅助，不参与接口约定（以下划线开头的一律跳过）
def public_methods(cls: type) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("_") or not inspect.iscoroutinefunction(fn):
            continue
        sig = inspect.signature(fn)
        # 去掉 self，并把注解抹平（两边写法可能不同但语义相同）
        params = [(p.name, p.kind, p.default is not inspect.Parameter.empty)
                  for p in list(sig.parameters.values())[1:]]
        out[name] = str(params)
    return out


def main() -> int:
    real, fake = public_methods(PgStore), public_methods(FakePg)
    fail = 0

    only_real = sorted(set(real) - set(fake) - set(ALLOWED_ONLY_REAL))
    only_fake = sorted(set(fake) - set(real))

    print(f"PgStore {len(real)} 个异步方法 / FakePg {len(fake)} 个\n")

    allowed = sorted(set(real) & set(ALLOWED_ONLY_REAL))
    if allowed:
        print("（已豁免，理由写在校验脚本里）")
        for n in allowed:
            print(f"    {n} —— {ALLOWED_ONLY_REAL[n]}")
        print()

    if only_real:
        fail += len(only_real)
        print("✗ 只有 PgStore 有（fake 档位会走 getattr 兜底，于是静默失效）：")
        for n in only_real:
            print(f"    {n}{real[n]}")
    if only_fake:
        fail += len(only_fake)
        print("✗ 只有 FakePg 有（real 档位根本调不到）：")
        for n in only_fake:
            print(f"    {n}{fake[n]}")

    diff = [n for n in sorted(set(real) & set(fake)) if real[n] != fake[n]]
    if diff:
        fail += len(diff)
        print("✗ 同名方法但签名不同：")
        for n in diff:
            print(f"    {n}\n      PgStore: {real[n]}\n      FakePg : {fake[n]}")

    if not fail:
        print("✓ 两边接口逐字一致")

    print("\n" + "═" * 62)
    print(f"失败 {fail} 项")
    print("═" * 62)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
