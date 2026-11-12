"""The chat client of the LLM agent: OpenAI-compatible chat completions over HTTP."""

import json
from typing import Any

import httpx2 as httpx
import pytest

from app.agent.chat import ChatError, HttpChat

KEY = "sk-test-not-a-real-key"
TOOLS = [{"type": "function", "function": {"name": "t", "description": "d"}}]
MESSAGES = [{"role": "user", "content": "hello"}]


def answer(message: dict[str, Any], finish: str = "stop") -> dict[str, Any]:
    return {
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    }


def chat(handler: Any, sleeps: list[float] | None = None, **options: Any) -> HttpChat:
    return HttpChat(
        "https://nim.test/v1",
        "some/model",
        KEY,
        transport=httpx.MockTransport(handler),
        sleep=(sleeps if sleeps is not None else []).append,
        **options,
    )


def test_a_tool_call_answer_is_parsed_and_the_request_is_well_formed() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        call = {
            "id": "c1",
            "type": "function",
            "function": {"name": "t", "arguments": '{"a":1}'},
        }
        return httpx.Response(
            200, json=answer({"content": None, "tool_calls": [call]}, "tool_calls")
        )

    reply = chat(handler).complete(MESSAGES, TOOLS)

    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("c1", "t", '{"a":1}')
    ]
    assert reply.text is None and reply.finish_reason == "tool_calls"
    assert reply.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "reasoning_tokens": 3,
    }
    [request] = seen
    assert str(request.url) == "https://nim.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "some/model" and body["tools"] == TOOLS
    assert body["messages"] == MESSAGES and body["max_tokens"] >= 1500
    assert "chat_template_kwargs" not in body


def test_a_text_answer_and_the_thinking_switch() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json=answer({"content": "Hello.", "reasoning_content": "x"})
        )

    reply = chat(handler, thinking=False).complete(MESSAGES, TOOLS)

    assert reply.text == "Hello." and reply.tool_calls == []
    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_think_tags_in_the_text_are_dropped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answer({"content": "<think>secret</think>Hi."}))

    assert chat(handler).complete(MESSAGES, TOOLS).text == "Hi."


def test_a_429_is_retried_twice_with_a_backoff_then_it_works() -> None:
    codes = iter([429, 429, 200])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(codes)
        if code == 429:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json=answer({"content": "ok"}))

    reply = chat(handler, sleeps).complete(MESSAGES, TOOLS)

    assert reply.text == "ok" and reply.rate_limited == 2
    assert len(sleeps) == 2 and 0 < sleeps[0] <= sleeps[1] <= 10


def test_three_429_in_a_row_are_an_error_that_counts_them() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    with pytest.raises(ChatError) as e:
        chat(handler).complete(MESSAGES, TOOLS)

    assert calls == 3 and e.value.rate_limited == 3 and "429" in str(e.value)


def test_a_retry_after_header_sets_the_wait_up_to_ten_seconds() -> None:
    codes = iter([429, 429, 200])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if next(codes) == 429:
            return httpx.Response(429, headers={"retry-after": "4"})
        return httpx.Response(200, json=answer({"content": "ok"}))

    chat(handler, sleeps).complete(MESSAGES, TOOLS)
    assert sleeps == [4, 4]


@pytest.mark.parametrize("code", [400, 401, 500, 503])
def test_other_http_errors_are_not_retried(code: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(code, text=f"the prompt said hello {KEY}")

    with pytest.raises(ChatError) as e:
        chat(handler).complete(MESSAGES, TOOLS)

    assert calls == 1 and str(code) in str(e.value)
    assert "hello" not in str(e.value) and KEY not in str(e.value)  # no body, no key


def test_a_timeout_and_a_network_error_are_chat_errors() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(ChatError, match="timeout"):
        chat(timeout).complete(MESSAGES, TOOLS)
    with pytest.raises(ChatError):
        chat(down).complete(MESSAGES, TOOLS)


@pytest.mark.parametrize(
    "payload", [{"choices": []}, {"nope": 1}, {"choices": [{"message": "x"}]}]
)
def test_an_answer_of_the_wrong_shape_is_a_chat_error(payload: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(ChatError):
        chat(handler).complete(MESSAGES, TOOLS)


def test_arguments_that_arrive_as_an_object_become_a_json_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        call = {"id": "c1", "function": {"name": "t", "arguments": {"a": 1}}}
        return httpx.Response(200, json=answer({"tool_calls": [call]}, "tool_calls"))

    [call] = chat(handler).complete(MESSAGES, TOOLS).tool_calls
    assert json.loads(call.arguments) == {"a": 1}


def test_from_env_needs_the_key_and_the_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("NVIDIA_KEY", "AGENT_BASE_URL", "AGENT_MODEL", "AGENT_THINKING"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="NVIDIA_KEY"):
        HttpChat.from_env()
    monkeypatch.setenv("NVIDIA_KEY", KEY)
    with pytest.raises(RuntimeError, match="AGENT_BASE_URL"):
        HttpChat.from_env()
    monkeypatch.setenv("AGENT_BASE_URL", "https://nim.test/v1/")
    with pytest.raises(RuntimeError, match="AGENT_MODEL"):
        HttpChat.from_env()
    monkeypatch.setenv("AGENT_MODEL", "some/model")
    c = HttpChat.from_env()
    assert c.model == "some/model" and c.base_url == "https://nim.test/v1"
    assert c.thinking is None
    monkeypatch.setenv("AGENT_THINKING", "off")
    assert HttpChat.from_env().thinking is False
    monkeypatch.setenv("AGENT_THINKING", "maybe")
    with pytest.raises(RuntimeError, match="AGENT_THINKING"):
        HttpChat.from_env()


def test_the_key_is_not_in_the_repr() -> None:
    assert KEY not in repr(chat(lambda r: httpx.Response(200)))
