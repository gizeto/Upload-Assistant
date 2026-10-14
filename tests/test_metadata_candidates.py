# ruff: noqa: S101

from unittest.mock import AsyncMock

import httpx
import pytest

from src import imdb, tmdb


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setattr(tmdb, "default_config", {})
    monkeypatch.setattr(tmdb, "tmdb_api_key", None)
    monkeypatch.setattr(tmdb, "parser", None)
    monkeypatch.setattr(tmdb.asyncio, "sleep", AsyncMock())
    return {"DEFAULT": {"tmdb_api": "test-key", "metadata_cache_enabled": False}}


@pytest.mark.asyncio
async def test_tmdb_shortlist_keeps_best_matches_without_prompting(monkeypatch, tmp_path, config):
    manager = tmdb.TmdbManager(config)
    prompt = AsyncMock()
    monkeypatch.setattr(tmdb, "prompt_in_thread", prompt)

    async def get(_client, url, **_kwargs):
        results = [{"id": index, "title": "Unrelated Movie", "release_date": "2020-01-01"} for index in range(1, 7)]
        results.append({"id": 101, "title": "Example Movie", "release_date": "2020-01-01"})
        return httpx.Response(200, json={"results": results}, request=httpx.Request("GET", url))

    monkeypatch.setattr(tmdb.httpx.AsyncClient, "get", get)

    candidates = await manager.search_candidates("Example Movie", 2020, "MOVIE", base_dir=str(tmp_path))

    assert len(candidates) == 5
    assert candidates[0]["id"] == 101
    assert candidates[0]["category"] == "MOVIE"
    prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_tmdb_shortlist_never_prompts(monkeypatch, tmp_path, config):
    manager = tmdb.TmdbManager(config)
    prompt = AsyncMock()
    monkeypatch.setattr(tmdb, "prompt_in_thread", prompt)

    async def get(_client, url, **_kwargs):
        return httpx.Response(200, json={"results": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(tmdb.httpx.AsyncClient, "get", get)

    assert await manager.search_candidates("Example Movie", 2020, "MOVIE", base_dir=str(tmp_path)) == []
    prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_imdb_shortlist_keeps_compatible_candidates_without_prompting(monkeypatch, tmp_path, config):
    prompt = AsyncMock()
    monkeypatch.setattr(imdb, "prompt_in_thread", prompt)

    async def post(_client, url, **_kwargs):
        titles = [{"id": f"tt{index:07d}", "titleText": {"text": "Example Movie"}, "releaseYear": {"year": 2020}, "titleType": {"text": "Podcast Episode"}} for index in range(1, 7)]
        titles.append({"id": "tt1234567", "titleText": {"text": "Example Movie"}, "releaseYear": {"year": 2020}, "titleType": {"text": "Movie"}})
        data = {"data": {"advancedTitleSearch": {"edges": [{"node": {"title": title}} for title in titles]}}}
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(imdb.httpx.AsyncClient, "post", post)

    candidates = await imdb.imdb_manager.search_candidates("Example Movie", 2020, "MOVIE", base_dir=str(tmp_path), config=config)

    assert len(candidates) == 5
    assert candidates[0]["imdbID"] == "tt1234567"
    prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_failure_is_not_cached_as_an_empty_result(monkeypatch, tmp_path):
    search = AsyncMock(side_effect=RuntimeError("temporary failure"))
    monkeypatch.setattr(imdb.imdb_manager, "search_imdb", search)
    config = {"DEFAULT": {"metadata_cache_dir": "cache"}}

    with pytest.raises(RuntimeError):
        await imdb.imdb_manager.search_candidates("Example Movie", 2020, "MOVIE", base_dir=str(tmp_path), config=config)

    async def recover(*_args, candidates, **_kwargs):
        candidates.append({"imdbID": "tt1234567", "title": "Example Movie", "year": 2020, "type": "movie"})

    search.side_effect = recover
    candidates = await imdb.imdb_manager.search_candidates("Example Movie", 2020, "MOVIE", base_dir=str(tmp_path), config=config)

    assert candidates[0]["imdbID"] == "tt1234567"
    assert search.await_count == 2
