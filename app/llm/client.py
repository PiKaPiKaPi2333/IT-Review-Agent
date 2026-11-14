"""LLM 客户端封装（硅基流动，OpenAI 兼容协议）+ structured_llm_call。"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.infra.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(RuntimeError):
    """结构化输出在重试后仍不合法。"""


def build_chat_model(
    role: str,
    *,
    settings: Settings | None = None,
    temperature: float | None = None,
) -> ChatOpenAI:
    s = settings or get_settings()
    extra: dict[str, Any] = {}
    if role == "classifier" and s.classifier_extra_body:
        extra["extra_body"] = s.classifier_extra_body
    return ChatOpenAI(
        model=s.model_for(role),
        api_key=s.siliconflow_api_key or "EMPTY",
        base_url=s.siliconflow_base_url,
        temperature=s.llm_temperature if temperature is None else temperature,
        timeout=s.llm_timeout,
        max_retries=0,  # 重试由下面统一控制
        **extra,
    )


def _coerce_json(text: str) -> str:
    """从可能带 ```json 围栏 / 前后杂质的文本里抠出 JSON。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        t = t.strip()
        if t.startswith("json"):
            t = t[4:].strip()
    if t.startswith("{"):
        return t
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        return t[start : end + 1]
    return t


@retry(
    retry=retry_if_exception_type((LLMOutputError, ValidationError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.8, max=8),
    reraise=True,
)
def _structured_via_native(
    llm: ChatOpenAI, schema: type[T], messages: list[BaseMessage], method: str
) -> T:
    structured = llm.with_structured_output(schema, method=method)  # type: ignore[arg-type]
    result = structured.invoke(messages)
    if isinstance(result, schema):
        return result
    if isinstance(result, dict):
        return schema.model_validate(result)
    raise LLMOutputError(f"结构化输出类型异常：{type(result)!r}")


def _structured_via_json(
    llm: ChatOpenAI, schema: type[T], messages: list[BaseMessage], max_attempts: int
) -> T:
    """JSON mode 降级：手工解析 + 校验错误回喂（ReAct 式自修复）。"""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    convo: list[BaseMessage] = [
        *messages,
        HumanMessage(
            content=(
                "只输出一个 JSON 对象，不要任何解释或 Markdown 围栏。"
                f"必须严格符合以下 JSON Schema：\n{schema_json}"
            )
        ),
    ]
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = llm.invoke(convo)
        except Exception as e:  # 网络/限流，交给外层 tenacity
            raise
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        try:
            return schema.model_validate_json(_coerce_json(text))
        except ValidationError as e:
            last_err = e
            logger.warning("structured output 校验失败 (attempt %d): %s", attempt, e)
            convo = [
                *convo,
                resp,
                HumanMessage(
                    content=(
                        "上面的输出未通过 Pydantic 校验，错误如下：\n"
                        f"{e}\n请修正后重新输出完整 JSON。"
                    )
                ),
            ]
    raise LLMOutputError(f"结构化输出连续 {max_attempts} 次校验失败：{last_err}")


def structured_llm_call(
    llm: ChatOpenAI,
    schema: type[T],
    *,
    system: str,
    user: str,
    max_retries: int = 2,
) -> T:
    """统一结构化输出入口（架构 §7）。

    1. 优先模型原生 structured output（默认 function_calling，硅基流动最稳）；
    2. 原生调用失败则降级 JSON mode + Pydantic 校验；
    3. 校验失败把错误回喂模型自修复；
    4. 全部失败抛 LLMOutputError，由 Graph 层捕获走降级路径。
    """
    s = get_settings()
    messages: list[BaseMessage] = [SystemMessage(content=system), HumanMessage(content=user)]

    native_error: Exception | None = None
    try:
        return _structured_via_native(
            llm, schema, messages, s.structured_output_method
        )
    except (LLMOutputError, ValidationError) as e:
        native_error = e
        logger.info("原生结构化输出失败，降级 JSON mode：%s", e)
    except Exception as e:  # 供应商不支持该方法（400）等
        native_error = e
        logger.info("原生结构化输出不可用，降级 JSON mode：%s", e)

    try:
        return _structured_via_json(llm, schema, messages, max_retries + 1)
    except Exception as e:
        raise LLMOutputError(f"结构化输出失败（native={native_error}; json={e}）") from e
