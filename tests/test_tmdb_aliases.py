# ruff: noqa: S101

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from src import metadata_resolution, tmdb
from src.meta import Meta
from src.metadata_cache import cache_for


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch):
    monkeypatch.setattr(tmdb, "default_config", {})
    monkeypatch.setattr(tmdb, "tmdb_api_key", None)
    monkeypatch.setattr(tmdb, "parser", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["MOVIE", "TV"])
@pytest.mark.parametrize("cached", [False, True])
async def test_tmdb_alternative_titles_resolve_existing_ids_and_are_cached(monkeypatch, tmp_path, category, cached):
    config = {"DEFAULT": {"tmdb_api": "test-key", "metadata_cache_dir": "cache"}}
    manager = tmdb.TmdbManager(config)
    meta = Meta(category=category, base_dir=str(tmp_path), search_year="2020", tmdb_id=101, imdb_id=1234567)
    if category == "MOVIE":
        main = {"title": "Example Movie", "original_title": "Example Movie", "release_date": "2020-01-01"}
    else:
        main = {"name": "Example Series", "original_name": "Example Series", "first_air_date": "2020-01-01"}
    main.update(overview="Example overview", genres=[], original_language="en")
    aliases = {"titles" if category == "MOVIE" else "results": [{"title": "Alternative Title"}]}
    cache = cache_for(str(tmp_path), config)
    cache_key = json.dumps({"category": category, "id": 101}, sort_keys=True)
    if cached:
        await cache.set("tmdb", "main", cache_key, main)
    requests = []

    async def get(_client, url, *, params):
        requests.append(url)
        if url.endswith("/101"):
            assert params["append_to_response"] == "alternative_titles"
            payload = {**main, "alternative_titles": aliases}
        elif url.endswith("/alternative_titles"):
            payload = aliases
        elif url.endswith("/external_ids"):
            payload = {"imdb_id": "tt1234567"}
        else:
            payload = {}
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(metadata_resolution.imdb_manager, "get_imdb_info_api", AsyncMock(return_value={
        "imdbID": "tt1234567", "title": "Alternative Title", "year": 2019, "type": "movie" if category == "MOVIE" else "tvSeries",
    }))
    imdb_search = AsyncMock(return_value=[])
    prompt = AsyncMock(side_effect=AssertionError("Unexpected metadata chooser"))
    monkeypatch.setattr(metadata_resolution.imdb_manager, "search_candidates", imdb_search)
    monkeypatch.setattr(metadata_resolution, "prompt_in_thread", prompt)

    for _ in range(2):
        await metadata_resolution.resolve_metadata(meta, main.get("title") or main["name"], manager, config)
        assert (meta.tmdb_id, meta.imdb_id) == (101, 1234567)
        assert meta.tmdb_aliases == ["Alternative Title"]

    assert sum(url.endswith("/101") for url in requests) == (0 if cached else 1)
    assert sum(url.endswith("/alternative_titles") for url in requests) == (1 if cached else 0)
    assert (await cache.get("tmdb", "main", cache_key))["alternative_titles"] == aliases
    imdb_search.assert_not_awaited()
    prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_alias_outage_does_not_replace_cached_metadata_or_block_retry(monkeypatch, tmp_path):
    config = {"DEFAULT": {"tmdb_api": "test-key", "metadata_cache_dir": "cache"}}
    manager = tmdb.TmdbManager(config)
    cache = cache_for(str(tmp_path), config)
    cache_key = json.dumps({"category": "MOVIE", "id": 101}, sort_keys=True)
    main = {"title": "Example Movie", "original_title": "Example Movie", "release_date": "2020-01-01", "overview": "Example overview", "genres": []}
    await cache.set("tmdb", "main", cache_key, main)
    unavailable = True
    monkeypatch.setattr("src.metadata_requests.asyncio.sleep", AsyncMock())

    async def get(_client, url, **_kwargs):
        if url.endswith("/alternative_titles"):
            status = 503 if unavailable else 200
            payload = {"titles": [{"title": "Alternative Title"}]}
        else:
            status, payload = 200, {}
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)

    first = await manager.tmdb_other_meta(101, category="MOVIE", base_dir=str(tmp_path))

    assert first["title"] == "Example Movie"
    assert first["tmdb_aliases"] == []
    assert await cache.get("tmdb", "main", cache_key) == main

    unavailable = False
    second = await manager.tmdb_other_meta(101, category="MOVIE", base_dir=str(tmp_path))

    assert second["tmdb_aliases"] == ["Alternative Title"]
    assert (await cache.get("tmdb", "main", cache_key))["alternative_titles"] == {"titles": [{"title": "Alternative Title"}]}
