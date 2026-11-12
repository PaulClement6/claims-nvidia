"""A small client for OpenAI-compatible chat completions (the hosted NVIDIA endpoint).

No vendor SDK. The key comes from the environment only (`NVIDIA_KEY`); it goes in the
`Authorization` header and nowhere else: not in an error text, not in the repr.
Nothing here logs: prompts and answers go to pipeline events only (medical text).
"""

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx2 as httpx

MAX_TOKENS = 2000  # room for the reasoning tokens of a reasoning model
RETRIES_429 = 2
MAX_WAIT = 10.0  # seconds; a Retry-After above this is not waited for
THINK = re.compile(r"<think>.*?</think>", re.S)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # a JSON string, as the model wrote it: it may be broken


@dataclass
class ChatReply:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    rate_limited: int = 0  # how many 429 this call met


class ChatError(Exception):
    """The model could not be reached or gave an unusable answer."""

    def __init__(self, message: str, rate_limited: int = 0) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


class Chat(Protocol):
    """What the LLM agent needs. Tests pass a fake."""

    model: str

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatReply: ...


class HttpChat:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        *,
        thinking: bool | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.thinking = thinking  # None: the model default; False: reasoning off
        self._key = api_key
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def __repr__(self) -> str:
        return f"HttpChat({self.base_url!r}, {self.model!r})"

    @classmethod
    def from_env(cls) -> "HttpChat":
        env = os.environ
        if not env.get("NVIDIA_KEY"):
            raise RuntimeError("CLAIMS_MODE=nim needs NVIDIA_KEY (see .env.example)")
        for name in ("AGENT_BASE_URL", "AGENT_MODEL"):
            if not env.get(name):
                raise RuntimeError(f"CLAIMS_MODE=nim needs {name} (see .env.example)")
        thinking = {"": None, "on": True, "off": False}.get(
            env.get("AGENT_THINKING", ""), "bad"
        )
        if thinking == "bad":
            raise RuntimeError("AGENT_THINKING must be on, off or empty")
        return cls(
            env["AGENT_BASE_URL"],
            env["AGENT_MODEL"],
            env["NVIDIA_KEY"],
            thinking=thinking,  # type: ignore[arg-type]
        )

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatReply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "max_tokens": MAX_TOKENS,
        }
        if self.thinking is not None:
            # NVIDIA model cards: chat_template_kwargs.enable_thinking
            body["chat_template_kwargs"] = {"enable_thinking": self.thinking}
        limited = 0
        for attempt in range(RETRIES_429 + 1):
            try:
                res = self._http.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._key}"},
                )
            except httpx.TimeoutException:
                raise ChatError("timeout", limited) from None
            except httpx.HTTPError as e:
                raise ChatError(f"network error: {type(e).__name__}", limited) from None
            if res.status_code != 429:
                break
            limited += 1
            if attempt == RETRIES_429:
                raise ChatError("HTTP 429 after retries", limited)
            self._sleep(_wait(res, attempt))
        if res.status_code != 200:
            # the body can echo the prompt: the status code is all we keep
            raise ChatError(f"HTTP {res.status_code}", limited)
        return _parse(res, limited)


def _wait(res: httpx.Response, attempt: int) -> float:
    try:
        wait = float(res.headers["retry-after"])
    except (KeyError, ValueError):
        wait = 1.0 * 2**attempt  # 1 s, then 2 s
    return min(wait, MAX_WAIT)


def _parse(res: httpx.Response, limited: int) -> ChatReply:
    try:
        data = res.json()
        choice = data["choices"][0]
        message = choice["message"]
        text = message.get("content")
        calls = [
            ToolCall(
                str(c.get("id") or f"call_{i}"),
                c["function"]["name"],
                a
                if isinstance(a := c["function"].get("arguments"), str)
                else json.dumps(a or {}),
            )
            for i, c in enumerate(message.get("tool_calls") or [])
        ]
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return ChatReply(
            text=THINK.sub("", text).strip() if isinstance(text, str) else None,
            tool_calls=calls,
            finish_reason=choice.get("finish_reason"),
            usage={
                "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0,
                "reasoning_tokens": details.get("reasoning_tokens") or 0,
            },
            rate_limited=limited,
        )
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ChatError("unreadable answer from the model", limited) from None
