from types import SimpleNamespace

import pytest

import vikingbot.providers.vlm_adapter as vlm_adapter
from vikingbot.providers.vlm_adapter import (
    VLMProviderAdapter,
    _is_retryable_rate_limit_error,
)


class _DisabledLangfuse:
    enabled = False
    _client = None


class _FakeVLM:
    def __init__(self, failures: list[Exception], result: str = "ok"):
        self.failures = list(failures)
        self.result = result
        self.calls = 0

    async def get_completion_async(self, **_kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.result


class _AsyncChunks:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _FakeStreamingCompletions:
    def __init__(self, failures: list[Exception], chunks):
        self.failures = list(failures)
        self.chunks = chunks
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return _AsyncChunks(self.chunks)


class _FakeStreamingVLM:
    provider = "volcengine"
    model = "test-model"
    thinking = False

    def __init__(self, completions: _FakeStreamingCompletions):
        self._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        )

    def get_async_client(self):
        return self._client


@pytest.mark.asyncio
async def test_chat_retries_rate_limit_until_success(monkeypatch):
    sleep_delays: list[float] = []

    async def _sleep(delay: float):
        sleep_delays.append(delay)

    monkeypatch.setattr(vlm_adapter, "_rate_limit_retry_delay", lambda attempt: attempt)
    monkeypatch.setattr(vlm_adapter.asyncio, "sleep", _sleep)

    fake_vlm = _FakeVLM(
        [
            RuntimeError("Error code: 429 - ModelAccountTpmRateLimitExceeded"),
            RuntimeError("TooManyRequests: rate limit"),
        ],
        result="done",
    )
    adapter = VLMProviderAdapter(fake_vlm, "test-model", langfuse_client=_DisabledLangfuse())

    response = await adapter.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "done"
    assert response.finish_reason == "stop"
    assert fake_vlm.calls == 3
    assert sleep_delays == [1, 2]


@pytest.mark.asyncio
async def test_chat_does_not_retry_quota_or_auth_errors(monkeypatch):
    async def _sleep(_delay: float):
        raise AssertionError("non-retryable errors must not sleep/retry")

    monkeypatch.setattr(vlm_adapter.asyncio, "sleep", _sleep)

    fake_vlm = _FakeVLM([RuntimeError("AccountQuotaExceeded 429")])
    adapter = VLMProviderAdapter(fake_vlm, "test-model", langfuse_client=_DisabledLangfuse())

    response = await adapter.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.finish_reason == "error"
    assert "AccountQuotaExceeded" in response.content
    assert fake_vlm.calls == 1


@pytest.mark.asyncio
async def test_chat_stream_retries_rate_limit_until_success(monkeypatch):
    sleep_delays: list[float] = []

    async def _sleep(delay: float):
        sleep_delays.append(delay)

    monkeypatch.setattr(vlm_adapter, "_rate_limit_retry_delay", lambda attempt: attempt)
    monkeypatch.setattr(vlm_adapter.asyncio, "sleep", _sleep)

    chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason=None,
                delta=SimpleNamespace(content="streamed", reasoning_content=None),
            )
        ],
    )
    completions = _FakeStreamingCompletions(
        [RuntimeError("Error code: 429 - ModelAccountTpmRateLimitExceeded")],
        [chunk],
    )
    adapter = VLMProviderAdapter(
        _FakeStreamingVLM(completions),
        "test-model",
        langfuse_client=_DisabledLangfuse(),
    )

    events = [
        event
        async for event in adapter.chat_stream(
            messages=[{"role": "user", "content": "hello"}],
        )
    ]

    assert completions.calls == 2
    assert sleep_delays == [1]
    assert [event.type for event in events] == ["content_delta", "response"]
    assert events[0].content == "streamed"
    assert events[1].response.content == "streamed"
    assert events[1].response.finish_reason == "stop"


def test_rate_limit_classifier_handles_target_error():
    assert _is_retryable_rate_limit_error(
        RuntimeError("Error code: 429 - ModelAccountTpmRateLimitExceeded")
    )
    assert not _is_retryable_rate_limit_error(RuntimeError("Error code: 401 Unauthorized"))
