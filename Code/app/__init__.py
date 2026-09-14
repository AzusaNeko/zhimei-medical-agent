"""智美医美顾问 · LangGraph 多 Agent 顾问系统（MVP）。

★ 在这里统一开启控制台 UTF-8 兜底，是刻意的：
  任何一个入口（`python -m app.cli`、`python scripts/smoke.py`、uvicorn）
  都会先导入本包，于是**一处生效、处处生效**，不必在每个脚本里各写一遍
  ——那种"每个新脚本都要记得加一行"的约定迟早会被漏掉，
  而漏掉的代价是运行到某个 print 时进程直接崩。
"""

from .console import enable_utf8 as _enable_utf8

_enable_utf8()

__all__ = ["__version__"]
__version__ = "2.0.0-mvp"
