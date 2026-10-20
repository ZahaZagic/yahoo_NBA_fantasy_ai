"""LangChain-backed LLM client for the strategy analysis chain.

The project talks to an OpenAI-compatible endpoint (OpenRouter by default, which
proxies Gemini). Using ``langchain_openai.ChatOpenAI`` instead of hand-rolled
``requests`` calls gives us, for free:

* automatic retries with exponential backoff on 429/5xx responses,
* a single place to manage timeouts and request headers,
* standardised token accounting via ``usage_metadata``,
* message objects (system/user) instead of string concatenation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when the LLM cannot be configured or the call fails."""


@dataclass
class LLMResponse:
    """Normalised response from the strategy model."""

    text: str
    usage: Dict[str, int] = field(default_factory=dict)
    model: str = ""


def _normalise_usage(raw: Any) -> Dict[str, int]:
    """Map LangChain ``usage_metadata`` onto the legacy token_usage keys."""
    if not raw:
        return {}
    if hasattr(raw, "get"):
        getter = raw.get
    else:  # pragma: no cover - defensive
        return {}
    return {
        "prompt_tokens": int(getter("input_tokens") or 0),
        "completion_tokens": int(getter("output_tokens") or 0),
        "total_tokens": int(getter("total_tokens") or 0),
    }


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first JSON object out of a model response.

    Handles fenced ```json blocks, bare fences, and prose-wrapped objects. The
    brace matcher is string-aware so braces inside quoted values do not confuse
    the scan.

    Fenced blocks are tried **newest first**. The output contract puts the real
    answer in the final block (section H), so if the model also echoes the schema
    example earlier in its prose, we take the later, authoritative block rather
    than the echoed placeholder.
    """
    if not text:
        return None

    if not isinstance(text, str):
        return None

    fenced = re.findall(
        r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE
    )
    candidates = [block.strip() for block in reversed(fenced)]
    candidates.append(text.strip())

    for candidate in candidates:
        parsed = _parse_first_object(candidate)
        if parsed is not None:
            return parsed
    return None


def _parse_first_object(candidate: str) -> Optional[Dict[str, Any]]:
    """Find and parse the first balanced ``{...}`` object in ``candidate``."""
    start = candidate.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(candidate)):
            char = candidate[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    snippet = candidate[start : index + 1]
                    try:
                        parsed = json.loads(snippet)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = candidate.find("{", start + 1)
    return None


class StrategyLLM:
    """Thin wrapper around the chat model used by the strategy node."""

    def __init__(
        self,
        api_key: Optional[str],
        model: str,
        base_url: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4000,
        timeout_seconds: float = 90.0,
        max_retries: int = 3,
        app_name: str = "fantasy-nba-agent",
        app_url: Optional[str] = None,
    ) -> None:
        if not api_key:
            raise LLMError(
                "No LLM API key configured. Set LLM_API_KEY (or "
                "OPENROUTER_API_KEY) in your .env file."
            )

        # OpenRouter uses these for optional attribution. Neither is required to
        # make a request, so a fork should not inherit someone else's URL.
        headers = {"X-Title": app_name}
        resolved_url = app_url or os.getenv("LLM_APP_URL")
        if resolved_url:
            headers["HTTP-Referer"] = resolved_url

        self.model = model
        self._client = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url or None,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout_seconds,
            max_retries=max_retries,
            default_headers=headers,
        )

    def invoke(self, system_prompt: str, human_prompt: str) -> LLMResponse:
        """Run a single system+human completion.

        Raises:
            LLMError: on any transport, auth, or provider error.
        """
        try:
            response = self._client.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=human_prompt),
                ]
            )
        except Exception as exc:  # noqa: BLE001 - surface a uniform error type
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc

        content = response.content
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(
                block if isinstance(block, str) else str(block.get("text", ""))
                for block in content
            )

        usage = _normalise_usage(getattr(response, "usage_metadata", None))
        model_name = getattr(response, "response_metadata", {}).get("model_name", self.model)

        return LLMResponse(text=content or "", usage=usage, model=model_name or self.model)


def build_llm(llm_config: Dict[str, Any]) -> StrategyLLM:
    """Construct a :class:`StrategyLLM` from a resolved ``config['llm']`` block."""
    return StrategyLLM(
        api_key=llm_config.get("api_key"),
        model=llm_config.get("model") or "google/gemini-2.0-flash-001",
        base_url=llm_config.get("base_url"),
        temperature=float(llm_config.get("temperature", 0.2)),
        max_tokens=int(llm_config.get("max_tokens", 4000)),
        timeout_seconds=float(llm_config.get("timeout_seconds", 90)),
        max_retries=int(llm_config.get("max_retries", 3)),
    )
