"""LangSmith 初始化。

langsmith 会自动读取 LANGSMITH_* 环境变量，这里做两件事：
1. 在 import 其它模块前把配置写回 os.environ（langchain-core 只认环境变量）；
2. 暴露 traceable 装饰器，供检索 / Milvus 等非 LLM 调用打点。
"""

from __future__ import annotations

import logging
import os

from app.infra.config import get_settings

logger = logging.getLogger(__name__)


def init_tracing() -> bool:
    s = get_settings()
    if not s.langsmith_tracing:
        logger.info("LangSmith tracing disabled")
        return False
    if not s.langsmith_api_key:
        logger.warning("LANGSMITH_TRACING=true but LANGSMITH_API_KEY is empty; tracing disabled")
        return False

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_API_KEY"] = s.langsmith_api_key
    os.environ["LANGCHAIN_API_KEY"] = s.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = s.langsmith_project
    os.environ["LANGCHAIN_PROJECT"] = s.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = s.langsmith_endpoint
    logger.info("LangSmith tracing enabled project=%s", s.langsmith_project)
    return True


try:  # langsmith 是可选依赖，缺失时退化为空装饰器
    from langsmith import traceable  # type: ignore
except Exception:  # pragma: no cover
    def traceable(*_args, **_kwargs):  # type: ignore
        def deco(fn):
            return fn

        if _args and callable(_args[0]):
            return _args[0]
        return deco
