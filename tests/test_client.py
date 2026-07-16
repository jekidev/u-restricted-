from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from llm_router.client import OpenRouterClient
from llm_router.config import RouterConfig
from llm_router.errors import AllRoutesFailed


def _response(data: dict | None = None, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=data if data is not None else {})
    return resp


@pytest.fixture
def make_client(monkeypatch):
    created: list[AsyncMock] = []

    def factory(**kwargs):
        m = AsyncMock()
        m.__aenter__ = AsyncMock(return_value=m)
        m.__aexit__ = AsyncMock(return_value=None)
        created.append(m)
        return m

    monkeypatch.setattr("llm_router.client.httpx.AsyncClient", factory)

    def _make(config: RouterConfig):
        client = OpenRouterClient(config)
        return client, created

    return _make


def test_reconfigure_rebuilds_http_client(make_client):
    config = RouterConfig(api_keys=["k"], models=["m"], auto_discover=False)
    client, _ = make_client(config)
    old = client._client
    new_config = RouterConfig(
        api_keys=["k2"],
        models=["m"],
        auto_discover=False,
        base_url="https://example.com",
        timeout_seconds=120,
    )
    asyncio.run(client.reconfigure(new_config))
    assert client._client is not old
    assert client.config.base_url == "https://example.com"
    assert client.config.timeout_seconds == 120
    old.aclose.assert_awaited_once()


def test_models_handles_null_pricing_and_context_length(make_client):
    config = RouterConfig(api_keys=["k"], auto_discover=True, free_only=True)
    client, mocks = make_client(config)
    mock = mocks[-1]
    mock.get.return_value = _response({
        "data": [
            {"id": "m1", "pricing": {"prompt": 0, "completion": 0, "request": 0}, "context_length": 1000},
            {"id": "m2", "pricing": {"prompt": None, "completion": 0, "request": 0}, "context_length": 2000},
            {"id": "m3", "pricing": None, "context_length": 3000},
            {"id": "m4", "pricing": {"prompt": 0.001, "completion": 0, "request": 0}, "context_length": None},
            {"id": "m5", "pricing": {"prompt": 0, "completion": 0, "request": 0}, "context_length": None},
        ]
    })
    models = asyncio.run(client.models())
    ids = [m["id"] for m in models]
    assert ids == ["m3", "m2", "m1", "m5"]


def test_chat_success_rotates_indices(make_client):
    config = RouterConfig(api_keys=["k1", "k2"], models=["a", "b"], auto_discover=False)
    client, mocks = make_client(config)
    mock = mocks[-1]
    mock.post.return_value = _response({
        "choices": [{"message": {"content": "hello"}}],
        "model": "a",
        "usage": {},
    })
    result = asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
    assert result.content == "hello"
    assert result.model == "a"
    assert client._model_index == 1
    assert client._key_index == 1


def test_chat_cooldown_resets_after_threshold(make_client):
    config = RouterConfig(
        api_keys=["k"],
        models=["m"],
        auto_discover=False,
        max_retries_per_route=0,
        cooldown_seconds=0,
        failure_threshold=1,
    )
    client, mocks = make_client(config)
    mock = mocks[-1]
    mock.post.side_effect = httpx.TimeoutException("timeout")
    with pytest.raises(AllRoutesFailed):
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
    assert mock.post.call_count == 1
    assert client._failure_counts["m:k"] == 1

    with pytest.raises(AllRoutesFailed):
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
    assert mock.post.call_count == 2
    assert client._failure_counts["m:k"] == 1


def test_chat_no_models_raises_all_routes_failed(make_client):
    config = RouterConfig(api_keys=["k"], models=[], auto_discover=False)
    client, _ = make_client(config)
    with pytest.raises(AllRoutesFailed):
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
