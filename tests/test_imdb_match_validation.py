# ruff: noqa: S101

from unittest.mock import AsyncMock

import httpx
import pytest

from src.imdb import imdb_manager, imdb_match_rejection
from src.meta import Meta
from src.prep_helpers import _apply_derived_imdb_id


@pytest.mark.parametrize(
    ("title_type", "accepted"),
    [("movie", True), ("short", True), ("video", True), ("tvSpecial", True), ("tvShort", True), ("tvSeries", False), ("podcastEpisode", False), (None, False)],
)
def test_movie_match_requires_compatible_type(title_type, accepted):
    info = {"title": "Example Movie", "year": 2020, "type": title_type}

    assert (imdb_match_rejection("MOVIE", 2020, ["Example Movie"], info) is None) is accepted


@pytest.mark.parametrize(("expected_year", "imdb_year", "accepted"), [(None, 2020, True), (2020, None, False), (2020, 2017, False), (2020, 2018, True), (2020, 2023, False)])
def test_movie_match_checks_available_years(expected_year, imdb_year, accepted):
    info = {"title": "Example Movie", "year": imdb_year, "type": "movie"}

    assert (imdb_match_rejection("MOVIE", expected_year, ["Example Movie"], info) is None) is accepted


@pytest.mark.parametrize(
    ("titles", "imdb_title", "accepted"),
    [(["Vila Exemplo"], "Vila Exémplo", True), (["Different Translation", "Original Foreign Title"], "Original Foreign Title", True), (["Example Movie"], "Unrelated Movie", False), ([], "Example Movie", False)],
)
def test_movie_match_checks_titles(titles, imdb_title, accepted):
    info = {"title": imdb_title, "year": 2020, "type": "movie"}

    assert (imdb_match_rejection("MOVIE", 2020, titles, info) is None) is accepted


def test_invalid_derived_series_id_preserves_existing_imdb():
    original_info = {"imdbID": "tt1234567", "title": "Example", "year": 2023, "type": "tvSeries"}
    meta = Meta(category="TV", filename="Example", title="Example", year=2023, imdb_id=1234567, imdb_info=original_info, aka="AKA Existing")
    derived_info = {"imdbID": "tt7654321", "title": "Other", "year": 2018, "type": "podcastEpisode"}

    assert _apply_derived_imdb_id(meta, meta.filename, 7654321, derived_info) is False
    assert (meta.imdb_id, meta.imdb_info, meta.aka, meta.no_imdb) == (1234567, original_info, "AKA Existing", False)


@pytest.mark.parametrize("rejected", [False, True])
def test_valid_derived_series_id_replaces_existing_imdb(rejected):
    meta = Meta(category="TV", filename="Example", title="Example", year=2023, imdb_id=0 if rejected else 1234567, automatic_imdb_rejected=rejected)
    derived_info = {"imdbID": "tt7654321", "title": "Example", "year": 2023, "type": "tvSeries"}

    assert _apply_derived_imdb_id(meta, meta.filename, 7654321, derived_info) is True
    assert (meta.imdb_id, meta.imdb_info, meta.no_imdb, meta.automatic_imdb_rejected) == (7654321, derived_info, False, False)


def test_explicit_no_imdb_still_blocks_tvdb_id():
    meta = Meta(category="TV", filename="Example", title="Example", year=2023, no_imdb=True)
    derived_info = {"imdbID": "tt7654321", "title": "Example", "year": 2023, "type": "tvSeries"}

    assert _apply_derived_imdb_id(meta, meta.filename, 7654321, derived_info) is False
    assert (meta.imdb_id, meta.no_imdb, meta.automatic_imdb_rejected) == (None, True, False)


@pytest.fixture
def search_results(monkeypatch):
    candidates = []

    async def post(_client, url, **_kwargs):
        payload = {"data": {"advancedTitleSearch": {"edges": [{"node": {"title": candidate}} for candidate in candidates]}}}
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr("src.imdb.httpx.AsyncClient.post", post)
    return candidates


def _candidate(imdb_id, title, year, title_type):
    return {"id": f"tt{imdb_id}", "titleText": {"text": title}, "releaseYear": {"year": year}, "titleType": {"text": title_type}}


@pytest.mark.asyncio
async def test_single_incompatible_search_result_is_not_selected(search_results):
    search_results.append(_candidate(7654321, "Unrelated Podcast", 2024, "Podcast Episode"))

    assert await imdb_manager.search_imdb("Example Movie", 2023, category="MOVIE", unattended=True) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("quickie", [False, True])
async def test_unattended_search_skips_wrong_first_result(search_results, quickie):
    search_results.extend([_candidate(7654321, "Unrelated Series", 2018, "TV Series"), _candidate(1234567, "Vila Exémplo", 2021, "Short")])

    assert await imdb_manager.search_imdb("Vila Exemplo", 2021, category="MOVIE", quickie=quickie, unattended=True) == 1234567


@pytest.mark.asyncio
async def test_attended_selection_marks_incompatible_id_as_manual(monkeypatch, search_results):
    search_results.append(_candidate(7654321, "Unrelated Series", 2018, "TV Series"))
    monkeypatch.setattr("src.imdb.prompt_in_thread", AsyncMock(return_value="1"))
    selected = []

    result = await imdb_manager.search_imdb("Vila Exemplo", 2021, category="MOVIE", on_manual_selection=selected.append)

    assert result == 7654321
    assert selected == [7654321]
