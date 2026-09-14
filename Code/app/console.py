"""
控制台输出编码兜底（Windows 专属坑，但代价极低，一直留着）。

问题：Python 在 Windows 上对 stdout 有两套行为 ——
  · 直连控制台：走 UTF-16 控制台接口，什么字符都能打；
  · 被重定向（管道 `|`、`> file`、CI 捕获、父进程读管道）：退回 locale 编码，
    简中环境就是 GBK。
于是 `✓ ✗ ⛔ ⏸ ✅ 👤 •` 这些**不在 GBK 字符集里**的符号会直接
`UnicodeEncodeError` 把进程打死 —— 而"在终端里手敲一遍"完全正常。
这类 bug 的恶劣之处在于：它只在"重定向 + 恰好执行到那一行"时才复现，
本地开发几乎永远看不到，一进 CI 或写日志文件就炸。

处理：把两个流显式改成 UTF-8；`errors="replace"` 保证极端情况下
（比如流已被别的库包装、reconfigure 不被支持）也只是把打不出的字符
降级成 `?`，而不是让整个程序崩掉。
"""

from __future__ import annotations

import sys

_APPLIED = False


def enable_utf8() -> None:
    """把 stdout/stderr 切到 UTF-8（幂等，重复调用无副作用）。"""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            # 退一步：至少保证"编不出来也不崩"
            try:
                stream.reconfigure(errors="replace")
            except Exception:  # noqa: BLE001
                pass
