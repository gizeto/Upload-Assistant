# ruff: noqa: S101

from unittest.mock import AsyncMock

import httpx
import pytest

from src.metadata_requests import metadata_request


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_transient_http_errors_are_retried(monkeypatch, status):
    sleep = AsyncMock()
    monkeypatch.setattr("src.metadata_requests.asyncio.sleep", sleep)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status if len(requests) < 3 else 200, json={"title": "Example"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response = await metadata_request(client, "get", "https://example.invalid/title")

    assert response.json() == {"title": "Example"}
    assert len(requests) == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_missing_title_is_not_retried():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await metadata_request(client, "get", "https://example.invalid/title")

    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [{"errors": [{"message": "temporarily unavailable"}]}, {}, {"data": None}, {"data": {}}])
async def test_incomplete_graphql_responses_are_retried(monkeypatch, failure):
    monkeypatch.setattr("src.metadata_requests.asyncio.sleep", AsyncMock())
    requests = []

    def handle(request):
        requests.append(request)
        payload = failure if len(requests) == 1 else {"data": {"title": {"id": "tt1234567"}}}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response = await metadata_request(client, "post", "https://example.invalid/graphql", graphql=True)

    assert response.json()["data"]["title"]["id"] == "tt1234567"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_retry_after_is_honored_with_a_bounded_delay(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("src.metadata_requests.asyncio.sleep", sleep)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"}) if len(requests) == 1 else httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await metadata_request(client, "get", "https://example.invalid/title")

    sleep.assert_awaited_once_with(5.0)
