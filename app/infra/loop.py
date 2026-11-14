"""事件循环兼容层。

`psycopg` 的异步模式不支持 Windows 的 `ProactorEventLoop`（Python 3.12 在
Windows 上的默认值），而 uvicorn 在 Windows 上默认也用 Proactor。不处理的话
checkpointer 会一路降级到内存，且连接池刷一堆 "cannot use the 'ProactorEventLoop'"。
"""

from __future__ import annotations

import asyncio
import sys


def use_selector_event_loop() -> None:
    """把默认事件循环策略切成 Selector（仅 Windows；其他平台/新版本 Python 空转）。"""
    if sys.platform != "win32":
        return
    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_cls is None:  # Python 3.14+ 移除了策略 API
        return
    if not isinstance(asyncio.get_event_loop_policy(), policy_cls):
        asyncio.set_event_loop_policy(policy_cls())


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """给 uvicorn 用的 loop factory：

        uvicorn app.main:app --loop app.infra.loop:selector_loop_factory
    """
    return asyncio.SelectorEventLoop()
