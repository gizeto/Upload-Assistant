# ruff: noqa: S101

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src import prep_helpers, tmdb
from src.meta import Meta


@pytest.fixture(autouse=True)
def isolate_tmdb_config(monkeypatch):
    monkeypatch.setattr(tmdb, "default_config", {})
    monkeypatch.setattr(tmdb, "tmdb_api_key", None)
    monkeypatch.setattr(tmdb, "parser", None)


@pytest.mark.parametrize("primary_found", [True, False])
def test_original_title_is_searched_before_roman_numeral_fallback(monkeypatch, primary_found):
    queries = []

    async def get(_client, url, *, params):
        query = params["query"]
        queries.append(query)
        results = []
        if query == "V Example" and primary_found:
            results = [{"id": 101, "name": "V Example"}]
        elif query == "5 Example":
            results = [{"id": 202, "name": "5 Example"}]
        return httpx.Response(200, json={"results": results}, request=httpx.Request("GET", url))

    monkeypatch.setattr(tmdb.httpx.AsyncClient, "get", get)
    monkeypatch.setattr(tmdb.asyncio, "sleep", AsyncMock())

    result = asyncio.run(tmdb.get_tmdb_id("V Example", None, "TV"))

    assert result == (101 if primary_found else 202, "TV")
    assert queries == (["V Example"] if primary_found else ["V Example", "5 Example"])


@pytest.fixture
def show_search(monkeypatch, tmp_path):
    config = {"DEFAULT": {"tmdb_api": "test-key", "metadata_cache_enabled": False}}
    manager = tmdb.TmdbManager(config)
    manager.search_candidates = AsyncMock(return_value=[{"id": 101, "category": "TV", "name": "Unrelated Series", "first_air_date": "2004-01-01"}])
    manager.get_tmdb_from_imdb = AsyncMock(wraps=manager.get_tmdb_from_imdb)
    prep = SimpleNamespace(config=config, tmdb_manager=manager)
    meta = Meta(
        base_dir=str(tmp_path),
        path="Example.Series.S02.1080p.WEB-DL-ExampleGroup",
        category="TV",
        skip_trackers=True,
        tvmaze_id=404,
        tvdb_id=303,
        tmdb_episode_data={"name": "Unrelated Episode"},
        tmdb_season_data={"name": "Unrelated Season"},
        we_checked_tmdb=True,
    )
    responses = {
        "/find/tt1234567": {"tv_results": [{"id": 202, "original_language": "en"}]},
        "/tv/101": {
            "name": "Unrelated Series", "first_air_date": "2004-01-01", "genres": [],
            "overview": "Unrelated overview", "original_language": "ko", "poster_path": "/wrong.jpg",
        },
        "/tv/202": {
            "name": "Example Series", "first_air_date": "2016-01-01", "genres": [{"id": 18, "name": "Drama"}],
            "overview": "Correct overview", "original_language": "en", "poster_path": "/correct.jpg",
        },
        "/tv/202/external_ids": {"imdb_id": "tt1234567"},
        "/tv/101/external_ids": {"tvdb_id": 303},
    }

    async def get(_client, url, **_kwargs):
        payload = responses.get(url.removeprefix(tmdb.TMDB_BASE_URL), {})
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(tmdb.httpx.AsyncClient, "get", get)
    monkeypatch.setattr(prep_helpers.video_manager, "get_type", AsyncMock(return_value="WEBDL"))
    monkeypatch.setattr(prep_helpers.video_manager, "get_video_duration", AsyncMock(return_value=45))
    monkeypatch.setattr(prep_helpers.imdb_manager, "search_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr("src.metadata_resolution.prompt_in_thread", AsyncMock(side_effect=["tt1234567"]))
    monkeypatch.setattr(
        prep_helpers.imdb_manager, "get_imdb_info_api",
        AsyncMock(return_value={"imdbID": "tt1234567", "title": "Example Series", "year": 2016, "type": "tvSeries"}),
    )

    return prep, meta


def _search_metadata(prep, meta):
    asyncio.run(prep_helpers.search_metadata(prep, meta, "Example Series", meta.path, meta.path, "", "", False, False, False, None, {}, {"media": {"track": [{}]}}))


@pytest.mark.parametrize(("manual_tvdb", "manual_tvmaze"), [(0, 0), (303, 0), (0, 404), (303, 404)])
def test_imdb_selection_replaces_automatic_tmdb_metadata(show_search, manual_tvdb, manual_tvmaze):
    prep, meta = show_search
    meta.tvdb_manual = manual_tvdb
    meta.tvmaze_manual = manual_tvmaze

    _search_metadata(prep, meta)

    assert meta.imdb_id == meta.imdb_manual == 1234567
    assert meta.tmdb_id == 202
    assert (meta.title, meta.year, meta.overview, meta.genres, meta.original_language) == ("Example Series", 2016, "Correct overview", ["Drama"], "en")
    assert meta.artwork_url == "https://image.tmdb.org/t/p/original/correct.jpg"
    assert (meta.tvdb_id, meta.tvmaze_id) == (manual_tvdb, manual_tvmaze)
    assert meta.imdb_mismatch is False
    assert meta.tmdb_episode_data is None
    assert meta.tmdb_season_data is None
    assert meta.we_checked_tmdb is False
    assert prep.tmdb_manager.get_tmdb_from_imdb.call_args.args == (1234567, manual_tvdb or None)


def test_conflicting_manual_ids_can_be_confirmed(show_search, monkeypatch):
    prep, meta = show_search
    meta.tmdb_manual = 101
    monkeypatch.setattr("src.metadata_resolution.prompt_in_thread", AsyncMock(side_effect=["tt1234567", "keep"]))

    _search_metadata(prep, meta)

    assert meta.imdb_id == meta.imdb_manual == 1234567
    assert meta.tmdb_id == 101
    assert (meta.tvdb_id, meta.tvmaze_id) == (303, 0)
    assert meta.tmdb_episode_data is None
    assert meta.tmdb_season_data is None
    assert meta.we_checked_tmdb is False
    prep.tmdb_manager.get_tmdb_from_imdb.assert_not_awaited()


def test_manual_imdb_clears_automatic_ids_before_combined_metadata_search(show_search):
    prep, meta = show_search
    meta.imdb_manual = 1234567
    meta.imdb_info = {"imdbID": "tt7654321", "title": "Unrelated Series"}
    meta.tvdb_id = 303
    prep.metadata_searching_manager = SimpleNamespace(all_ids=AsyncMock(return_value=meta))

    _search_metadata(prep, meta)

    assert meta.tmdb_id == 202
    assert (meta.tvdb_id, meta.tvmaze_id) == (0, 0)
    assert meta.imdb_info["imdbID"] == "tt1234567"
    prep.metadata_searching_manager.all_ids.assert_not_awaited()
    prep.tmdb_manager.get_tmdb_from_imdb.assert_awaited_once()


def test_manual_imdb_overrides_cached_id(show_search):
    prep, meta = show_search
    meta.imdb_manual = "tt1234567"
    meta.imdb_id = 7654321
    meta.tmdb_id = 101
    prep.tmdb_manager.get_tmdb_from_imdb.return_value = ("TV", 202, "en", False)

    _search_metadata(prep, meta)

    assert meta.imdb_id == 1234567
    assert prep.tmdb_manager.get_tmdb_from_imdb.call_args.args[0] == 1234567


def test_unresolved_imdb_selection_does_not_keep_unrelated_tmdb(show_search, monkeypatch):
    prep, meta = show_search
    prep.tmdb_manager.get_tmdb_from_imdb.return_value = ("TV", 0, "", False)
    monkeypatch.setattr("src.metadata_resolution.prompt_in_thread", AsyncMock(side_effect=["tt1234567", "skip"]))

    _search_metadata(prep, meta)

    assert meta.tmdb_id == 0
    assert meta.imdb_id == 1234567
    assert meta.no_imdb is False


def test_cancelled_search_does_not_commit_partial_release_metadata(show_search, monkeypatch):
    prep, meta = show_search
    before = meta.to_dict()
    monkeypatch.setattr("src.metadata_resolution.prompt_in_thread", AsyncMock(side_effect=EOFError))

    with pytest.raises(EOFError):
        _search_metadata(prep, meta)

    assert meta.to_dict() == before


@pytest.mark.parametrize("linked_imdb_id", [1234567, 7654321, 0])
def test_parallel_search_prefers_tmdb_linked_imdb(show_search, monkeypatch, linked_imdb_id):
    prep, meta = show_search
    tmdb_started = asyncio.Event()
    imdb_started = asyncio.Event()
    meta.imdb_info = {"imdbID": "tt7654321", "title": "Example Series", "year": 2016, "type": "tvSeries"}
    meta.tmdb_id = 0
    meta.tvdb_id = 0

    async def search_tmdb(*_args, **_kwargs):
        tmdb_started.set()
        await asyncio.wait_for(imdb_started.wait(), timeout=1)
        return [{"id": 101, "category": "TV", "name": "Example Series", "first_air_date": "2016-01-01"}]

    async def search_imdb(*_args, **_kwargs):
        imdb_started.set()
        await asyncio.wait_for(tmdb_started.wait(), timeout=1)
        return [meta.imdb_info]

    prep.tmdb_manager.search_candidates = AsyncMock(side_effect=search_tmdb)
    monkeypatch.setattr(prep_helpers.imdb_manager, "search_candidates", search_imdb)
    monkeypatch.setattr(tmdb, "tmdb_other_meta", AsyncMock(return_value={
        "title": "Example Series", "year": 2016, "genres": [], "overview": "Example overview",
        "imdb_id": 7654321, "tmdb_imdb_id": linked_imdb_id,
    }))

    _search_metadata(prep, meta)

    expected_imdb_id = linked_imdb_id or 7654321
    assert meta.tmdb_id == 101
    assert meta.imdb_id == expected_imdb_id
    assert meta.imdb_info["imdbID"] == f"tt{expected_imdb_id}"
    prep.tmdb_manager.get_tmdb_from_imdb.assert_not_awaited()


@pytest.mark.parametrize("manual_imdb", [0, 7654321])
def test_cached_tmdb_link_respects_manual_imdb(show_search, manual_imdb):
    prep, meta = show_search
    meta.tmdb_manual = 101
    meta.imdb_manual = manual_imdb
    meta.imdb_id = 7654321
    meta.imdb_info = {"imdbID": "tt7654321", "title": "Example Series", "year": 2016, "type": "tvSeries"}
    meta.tmdb_imdb_id = 1234567
    meta.quickie_search = True
    meta.title, meta.year, meta.genres, meta.overview = "Example Series", 2016, [], "Example overview"
    meta.metadata_identity = {"category": "TV", "tmdb_id": 101, "imdb_id": 7654321}
    prep.tmdb_manager.set_tmdb_metadata = AsyncMock()

    _search_metadata(prep, meta)

    expected_imdb_id = manual_imdb or 1234567
    assert meta.imdb_id == expected_imdb_id
    assert meta.imdb_info["imdbID"] == f"tt{expected_imdb_id}"
    prep.tmdb_manager.get_tmdb_from_imdb.assert_not_awaited()


def test_manual_imdb_is_not_overridden_by_quick_tmdb_match(monkeypatch, tmp_path):
    tmdb.TmdbManager({"DEFAULT": {"tmdb_api": "test-key", "metadata_cache_enabled": False}})
    meta = Meta(base_dir=str(tmp_path), category="TV", tmdb_id=101, imdb_id=1234567, imdb_manual=1234567, quickie_search=True)

    async def get(_client, url, **_kwargs):
        if url.endswith("/tv/101"):
            payload = {"name": "Example Series", "first_air_date": "2016-01-01", "overview": "Example overview"}
        elif url.endswith("/external_ids"):
            payload = {"imdb_id": "tt7654321"}
        else:
            payload = {}
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(tmdb.httpx.AsyncClient, "get", get)

    asyncio.run(tmdb.set_tmdb_metadata(meta))

    assert meta.imdb_id == 1234567
    assert meta.imdb_mismatch is False
    assert meta.mismatched_imdb_id == 0
