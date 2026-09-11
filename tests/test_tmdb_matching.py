"""Regression coverage for ambiguous TMDb title searches."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from src import tmdb


def mock_tmdb(monkeypatch, results, details=None):
    requests = []
    messages = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(request.url.path)
        if "/search/" in request.url.path:
            return httpx.Response(200, json={"results": results})
        movie_id = int(request.url.path.rsplit("/", 1)[1])
        detail = (details or {}).get(movie_id, {})
        if isinstance(detail, Exception):
            raise detail
        if isinstance(detail, int):
            return httpx.Response(detail)
        return httpx.Response(200, json=detail)

    monkeypatch.setattr(tmdb.httpx, "AsyncClient", lambda: client_type(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(tmdb, "prompt_in_thread", AsyncMock(return_value="1"))
    monkeypatch.setattr(tmdb, "get_tmdb_translations", AsyncMock(return_value="Test Movie"))
    monkeypatch.setattr(tmdb.logger, "info", messages.append)
    return requests, messages


def movie(movie_id, **extra):
    return {"id": movie_id, "title": "Test Movie", "original_title": "Test Movie", "release_date": "2025-01-01", **extra}


@pytest.mark.parametrize("duration,expected_id", [(99, 2), (100, 2), (101, 2), (98, 1), (102, 1), (None, 1), (0, 1), (-1, 1)])
def test_movie_runtime_changes_ranking_only_within_one_minute(monkeypatch, duration, expected_id):
    requests, _ = mock_tmdb(monkeypatch, [movie(1), movie(2)], {1: {"runtime": 150}, 2: {"runtime": 100}})
    manager = tmdb.TmdbManager({"DEFAULT": {"tmdb_api": "test-key"}})

    result = asyncio.run(manager.get_tmdb_id("Test Movie", 2025, "MOVIE", duration=duration, unattended=True))

    assert result == (expected_id, "MOVIE")
    assert sorted(requests) == ["/3/movie/1", "/3/movie/2", "/3/search/movie"]


@pytest.mark.parametrize("runtime", [None, 0, -1, "unknown"])
def test_unknown_movie_runtime_does_not_change_ranking(monkeypatch, runtime):
    mock_tmdb(monkeypatch, [movie(1), movie(2)], {2: {"runtime": runtime}})

    assert asyncio.run(tmdb.get_tmdb_id("Test Movie", None, "MOVIE", duration=1, unattended=True)) == (1, "MOVIE")


def test_movie_choices_include_country_original_title_and_runtime(monkeypatch):
    _, messages = mock_tmdb(
        monkeypatch,
        [movie(1, original_title="Film Original"), movie(2)],
        {
            1: {"runtime": 100, "origin_country": ["PL", "DE"]},
            2: {"runtime": 100, "production_countries": [{"iso_3166_1": "US"}]},
        },
    )

    assert asyncio.run(tmdb.get_tmdb_id("Test Movie", None, "MOVIE", duration=100)) == (1, "MOVIE")

    choices = [message for message in messages if "[yellow]ID:" in message]
    assert len(choices) == 2
    assert "Country:[/yellow] PL, DE" in choices[0]
    assert "Original title:[/yellow] Film Original" in choices[0]
    assert "Duration:[/yellow] 100 min" in choices[0]
    assert "Country:[/yellow] US" in choices[1]
    assert "Original title:" not in choices[1]
    assert "Duration:[/yellow] 100 min" in choices[1]
    assert "similarity: 1.10" in choices[0]


@pytest.mark.parametrize("failure", [503, httpx.ConnectError("offline"), None])
def test_movie_details_failure_keeps_choices_available(monkeypatch, failure):
    _, messages = mock_tmdb(monkeypatch, [movie(1), movie(2)], {1: failure, 2: failure})

    assert asyncio.run(tmdb.get_tmdb_id("Test Movie", None, "MOVIE", duration=100)) == (1, "MOVIE")

    choices = [message for message in messages if "[yellow]ID:" in message]
    assert len(choices) == 2
    assert all("Country:[/yellow] Unknown" in choice and "Duration:[/yellow] Unknown" in choice for choice in choices)


def test_tv_choices_show_country_and_original_name_without_runtime_boost(monkeypatch):
    requests, messages = mock_tmdb(monkeypatch, [
        {"id": 1, "name": "Test Movie", "original_name": "Test Movie", "origin_country": ["GB"]},
        {"id": 2, "name": "Test Movie", "original_name": "Original Show", "origin_country": ["PL"], "runtime": 100},
    ])

    assert asyncio.run(tmdb.get_tmdb_id("Test Movie", None, "TV", duration=100)) == (1, "TV")

    choices = [message for message in messages if "[yellow]ID:" in message]
    assert len(choices) == 2
    assert "Country:[/yellow] GB" in choices[0]
    assert "Country:[/yellow] PL" in choices[1]
    assert "Original title:[/yellow] Original Show" in choices[1]
    assert "similarity: 1.00" in choices[1]
    assert all("Duration:" not in choice for choice in choices)
    assert requests == ["/3/search/tv"]


@pytest.mark.parametrize("results", [[movie(1)], [movie(1), movie(2, title="Another Film")]])
def test_unambiguous_search_does_not_fetch_movie_details(monkeypatch, results):
    requests, _ = mock_tmdb(monkeypatch, results)

    assert asyncio.run(tmdb.get_tmdb_id("Test Movie", 2025, "MOVIE", duration=100)) == (1, "MOVIE")
    assert requests == ["/3/search/movie"]


def test_imdb_fallback_passes_measured_duration_to_title_search(monkeypatch):
    mock_tmdb(monkeypatch, [])
    lookup = AsyncMock(return_value=(42, "MOVIE"))
    monkeypatch.setattr(tmdb, "get_tmdb_id", lookup)
    manager = tmdb.TmdbManager({"DEFAULT": {"tmdb_api": "test-key"}})

    result = asyncio.run(manager.get_tmdb_from_imdb(123, imdb_info={"title": "Test Movie", "year": 2025}, duration=100))

    assert result[1] == 42
    assert lookup.call_args.kwargs["duration"] == 100
