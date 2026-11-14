"""Pydantic Settings —— 全部配置来自环境变量 / .env。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- app ----------
    app_env: str = "dev"
    log_level: str = "INFO"
    api_key: str = ""

    # ---------- LLM ----------
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"

    classifier_model: str = "Qwen/Qwen3-8B"
    generator_model: str = "deepseek-ai/DeepSeek-V3"
    verifier_model: str = "deepseek-ai/DeepSeek-V3"

    llm_temperature: float = 0.0
    llm_timeout: float = 120.0
    llm_max_retries: int = 3
    structured_output_method: Literal[
        "function_calling", "json_mode", "json_schema"
    ] = "function_calling"
    classifier_extra_body: dict[str, Any] | None = None

    # ---------- embedding ----------
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32

    # ---------- rerank ----------
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 20

    # ---------- postgres ----------
    database_url: str = "postgresql+asyncpg://review:review@localhost:5432/review"
    langgraph_schema: str = "langgraph"
    # 启动时自动执行 alembic upgrade head（旧变量名 AUTO_CREATE_TABLES 仍兼容）
    auto_migrate: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_MIGRATE", "AUTO_CREATE_TABLES"),
    )

    # ---------- milvus ----------
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    milvus_collection: str = "kb_chunks"

    # ---------- retrieval / graph limits ----------
    retrieval_top_k: int = 8
    retrieval_candidates: int = 20
    retrieval_min_score: float = 0.30
    max_context_tokens: int = 6000
    max_retrieval_rounds: int = 2
    max_regeneration: int = 2
    # 同一会话内连续触发澄清反问的上限；达到后不再反问，直接按现有信息作答
    max_clarify_streak: int = 2

    # ---------- 会话历史（短时记忆）压缩 ----------
    # 注入 prompt 的会话历史 token 上限（最近原文 + 更早摘要）
    history_budget_tokens: int = 1500
    # 其中留给更早对话摘要的比例，其余给最近的原文消息
    history_summary_ratio: float = 0.35
    # 从数据库最多读取的历史消息条数（超出的更早消息不参与压缩）
    history_max_messages: int = 200

    # ---------- mineru（官方 API，无需本地部署） ----------
    mineru_api_key: str = ""
    mineru_api_base: str = "https://mineru.net/api/v4"
    # pipeline（快、便宜）/ vlm（版面与表格更强）
    mineru_model_version: str = "pipeline"
    mineru_is_ocr: bool = False
    # ch / en / 空=自动检测
    mineru_language: str = ""
    # 例："1-10" 或 "2,4-6"；空=全文
    mineru_page_ranges: str = ""
    # 单次 HTTP 请求超时；整体等待超时另算
    mineru_request_timeout: float = 180.0
    # 结果轮询间隔
    mineru_poll_interval: float = 3.0
    mineru_timeout: int = 1800
    mineru_output_dir: Path = Path("./data/parsed")
    upload_dir: Path = Path("./data/uploads")

    # ---------- chunking ----------
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64

    # ---------- langsmith ----------
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "reviewAgent"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # ---------- mem0（长期记忆） ----------
    mem0_api_key: str = ""
    # hosted: mem0 托管服务；self-hosted: 本地 Qdrant
    mem0_mode: Literal["hosted", "self-hosted"] = "self-hosted"
    # self-hosted 时的 Qdrant 地址
    mem0_qdrant_url: str = "http://localhost:6333"
    mem0_qdrant_api_key: str = ""

    @field_validator("classifier_extra_body", mode="before")
    @classmethod
    def _parse_extra_body(cls, v: Any) -> Any:
        if v in (None, "", "null"):
            return None
        if isinstance(v, str):
            return json.loads(v)
        return v

    @property
    def sync_database_url(self) -> str:
        """给 alembic / 同步场景使用的连接串。"""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")

    @property
    def psycopg_dsn(self) -> str:
        """langgraph-checkpoint-postgres 需要裸 libpq DSN。"""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    def model_for(self, role: str) -> str:
        return {
            "classifier": self.classifier_model,
            "generator": self.generator_model,
            "verifier": self.verifier_model,
        }.get(role, self.generator_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
