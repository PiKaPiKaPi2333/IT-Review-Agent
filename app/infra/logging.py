"""日志配置。"""

from __future__ import annotations

import logging
import sys

from app.infra.config import get_settings

_CONFIGURED = False


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, get_settings().log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        stream=sys.stdout,
        force=True,
    )
    # 降噪
    for noisy in ("httpx", "httpcore", "pymilvus", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True
