# ruff: noqa: S101

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src import metadata_resolution
from src.imdb import ImdbManager
from src.meta import Meta


@pytest.fixture
def case(monkeypatch, tmp_path):
    meta = Meta(category="MOVIE", search_year="2020", base_dir=str(tmp_path), unattended=True)
    tmdb_data = {
        101: {"title": "Example Movie", "year": 2020, "original_title": "Example Movie", "tmdb_imdb_id": 1234567},
        202: {"title": "Another Movie", "year": 2020, "original_title": "Another Movie", "tmdb_imdb_id": 7654321},
    }
    imdb_data = {
        1234567: {"imdbID": "tt1234567", "title": "Example Movie", "year": 2020, "type": "movie"},
        7654321: {"imdbID": "tt7654321", "title": "Another Movie", "year": 2020, "type": "movie"},
    }

    async def set_tmdb_metadata(staged, _filename, *, force):
        assert force
        staged.update({"genres": [], "overview": "Example overview", "artwork_url": "/new-poster.jpg", "tvdb_id": 0, **tmdb_data[staged.tmdb_id]})

    async def get_imdb_info(imdb_id, **_kwargs):
        return dict(imdb_data.get(imdb_id, {}))

    manager = SimpleNamespace(
        search_candidates=AsyncMock(return_value=[{"id": 101, "title": "Example Movie", "release_date": "2020-01-01"}]),
        set_tmdb_metadata=AsyncMock(side_effect=set_tmdb_metadata),
        get_tmdb_from_imdb=AsyncMock(return_value=("MOVIE", 0, "", False)),
    )
    imdb_search = AsyncMock(return_value=[imdb_data[1234567]])
    imdb_info = AsyncMock(side_effect=get_imdb_info)
    async def ask(*_args, **_kwargs):
        if prompt.await_count > 3:
            raise AssertionError("Unexpected repeated metadata selection")
        return prompt.return_value

    prompt = AsyncMock(side_effect=ask, return_value="skip")
    monkeypatch.setattr(metadata_resolution.imdb_manager, "search_candidates", imdb_search)
    monkeypatch.setattr(metadata_resolution.imdb_manager, "get_imdb_info_api", imdb_info)
    monkeypatch.setattr(metadata_resolution, "prompt_in_thread", prompt)
    return SimpleNamespace(
        meta=meta, manager=manager, filename="Example Movie", config={"DEFAULT": {}}, tmdb_data=tmdb_data,
        imdb_data=imdb_data, imdb_search=imdb_search, imdb_info=imdb_info, prompt=prompt,
    )


async def resolve(case):
    await metadata_resolution.resolve_metadata(case.meta, case.filename, case.manager, case.config)


@pytest.mark.asyncio
async def test_bad_tmdb_link_falls_back_to_independent_imdb_match(case):
    case.imdb_data[1234567]["title"] = "Unrelated Movie"
    case.imdb_data[7654321]["title"] = "Example Movie"
    case.imdb_search.return_value = [case.imdb_data[7654321]]

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (101, 7654321)
    assert case.meta.no_imdb is False
    assert case.meta.imdb_info["title"] == "Example Movie"
    assert case.meta.metadata_id_sources == {"tmdb": "search", "imdb": "search"}


@pytest.mark.asyncio
async def test_aliases_validate_translated_titles(case):
    case.tmdb_data[101]["original_title"] = "Original Foreign Title"
    case.imdb_data[1234567].update(title="Localized Release", aka="Original Foreign Title")

    await resolve(case)

    assert case.meta.imdb_id == 1234567
    assert case.meta.metadata_id_sources["imdb"] == "TMDb link"


@pytest.mark.asyncio
@pytest.mark.parametrize("imdb_year", [2015, 2020])
async def test_invalid_link_does_not_disable_future_imdb_lookups(case, imdb_year):
    case.imdb_data[1234567].update(title="Unrelated Movie", year=imdb_year)
    case.imdb_search.return_value = []

    await resolve(case)

    assert case.meta.tmdb_id == 101
    assert case.meta.imdb_id == 0
    assert case.meta.no_imdb is False
    assert case.meta.automatic_imdb_rejected is True


@pytest.mark.asyncio
async def test_details_for_a_different_id_are_rejected(case, monkeypatch):
    async def post(_client, url, **_kwargs):
        title = {"id": "tt7654321", "titleText": {"text": "Example Movie"}, "releaseYear": {"year": 2020}, "titleType": {"id": "movie"}}
        return httpx.Response(200, json={"data": {"title": title}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(metadata_resolution.imdb_manager, "get_imdb_info_api", ImdbManager().get_imdb_info_api)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    await resolve(case)

    assert case.meta.imdb_id == 0
    assert case.meta.no_imdb is False


@pytest.mark.asyncio
async def test_temporary_failure_keeps_valid_tmdb_and_allows_retry(case):
    case.imdb_info.side_effect = httpx.ReadTimeout("temporary timeout")

    await resolve(case)

    assert case.meta.tmdb_id == 101
    assert case.meta.imdb_id == 0
    assert case.meta.no_imdb is False
    assert case.meta.automatic_imdb_rejected is False


@pytest.mark.asyncio
async def test_explicit_skip_disables_imdb(case):
    case.meta.unattended = False
    case.imdb_data[1234567]["title"] = "Unrelated Movie"
    case.imdb_search.return_value = []

    await resolve(case)

    case.prompt.assert_awaited_once()
    assert case.meta.tmdb_id == 101
    assert case.meta.imdb_id == 0
    assert case.meta.no_imdb is True


@pytest.mark.asyncio
async def test_no_imdb_does_not_query_imdb(case):
    case.meta.no_imdb = True

    await resolve(case)

    assert case.meta.tmdb_id == 101
    assert case.meta.imdb_id == 0
    case.imdb_search.assert_not_awaited()
    case.imdb_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflicting_explicit_ids_fail_clearly_when_unattended(case):
    case.meta.tmdb_manual = 101
    case.meta.imdb_manual = 7654321

    with pytest.raises(ValueError, match="Explicit TMDb and IMDb IDs describe different titles"):
        await resolve(case)

    assert (case.meta.tmdb_manual, case.meta.imdb_manual) == (101, 7654321)
    assert case.meta.tmdb_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "expected"), [("1", (101, 1234567)), ("2", (202, 7654321)), ("keep", (101, 7654321))])
async def test_user_can_resolve_or_confirm_explicit_conflict(case, answer, expected):
    case.meta.unattended = False
    case.meta.tmdb_manual = 101
    case.meta.imdb_manual = 7654321
    case.prompt.return_value = answer
    case.manager.get_tmdb_from_imdb.return_value = ("MOVIE", 202, "en", False)

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == expected
    case.prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_selection_does_not_commit_partial_metadata(case):
    case.meta.unattended = False
    case.meta.tmdb_manual = 101
    case.meta.imdb_manual = 7654321
    before = case.meta.to_dict()
    case.prompt.side_effect = EOFError

    with pytest.raises(EOFError):
        await resolve(case)

    assert case.meta.to_dict() == before


@pytest.mark.asyncio
async def test_changed_title_clears_other_provider_data(case):
    case.meta.tmdb_id = 202
    case.meta.tvdb_id = 303
    case.meta.tvmaze_id = 404
    case.meta.tmdb_episode_data = {"name": "Old Episode"}
    case.meta.tmdb_season_data = {"name": "Old Season"}
    case.meta.tmdb_localized_data = {"title": "Old Title"}
    case.meta.tvdb_episode_name = "Old Episode"
    case.meta.we_checked_tvdb = case.meta.we_asked_tvmaze = case.meta.we_checked_tmdb = True

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (101, 1234567)
    assert (case.meta.tvdb_id, case.meta.tvmaze_id) == (0, 0)
    assert case.meta.tmdb_episode_data is case.meta.tmdb_season_data is None
    assert case.meta.tmdb_localized_data == {}
    assert case.meta.tvdb_episode_name is None
    assert not any((case.meta.we_checked_tvdb, case.meta.we_asked_tvmaze, case.meta.we_checked_tmdb))
    assert case.meta.artwork_url == "/new-poster.jpg"


@pytest.mark.asyncio
async def test_manual_tvdb_and_tvmaze_are_preserved(case):
    case.meta.tmdb_id = 202
    case.meta.tvdb_id = case.meta.tvdb_manual = 303
    case.meta.tvmaze_id = case.meta.tvmaze_manual = 404

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (101, 1234567)
    assert (case.meta.tvdb_id, case.meta.tvmaze_id) == (303, 404)


@pytest.mark.asyncio
@pytest.mark.parametrize("attended", [False, True])
async def test_ambiguous_matches_require_selection(case, attended):
    case.meta.unattended = not attended
    case.imdb_search.return_value = []
    case.tmdb_data[202] = dict(case.tmdb_data[101])
    case.manager.search_candidates.return_value.append({"id": 202, "title": "Example Movie", "release_date": "2020-01-01"})
    case.prompt.return_value = "2"

    await resolve(case)

    assert case.meta.tmdb_id == (202 if attended else 0)
    if attended:
        assert case.meta.tmdb_manual == 202
        case.prompt.assert_awaited_once()
    else:
        case.prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_can_search_again_from_the_combined_chooser(case):
    case.meta.unattended = False
    case.tmdb_data[202].update(title="Alternative Movie", original_title="Alternative Movie", year=2018)
    case.imdb_data[7654321].update(title="Alternative Movie", year=2018)
    case.imdb_search.return_value = []
    case.prompt.return_value = "search Alternative Movie 2018"

    async def search(title, _year, _category, **_kwargs):
        return [{"id": 202, "title": title, "release_date": "2018-01-01"}] if title == "Alternative Movie" else []

    case.manager.search_candidates.side_effect = search

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (202, 7654321)
    assert case.meta.year == 2018
    case.prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_valid_imdb_is_kept_when_tmdb_cannot_be_resolved(case):
    case.manager.search_candidates.return_value = []

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (0, 1234567)
    assert case.meta.title == "Example Movie"
    assert case.meta.no_imdb is False


@pytest.mark.asyncio
async def test_imdb_only_fallback_clears_unrelated_tmdb_fields(case):
    case.meta.tmdb_id = 202
    case.meta.release_date = "2004-01-01"
    case.meta.networks = "Old Network"
    case.meta.production_companies = [{"name": "Old company"}]
    case.manager.search_candidates.return_value = []
    case.imdb_data[1234567].update(genres="Drama, Mystery", cover="/imdb-poster.jpg")

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (0, 1234567)
    assert case.meta.release_date == ""
    assert case.meta.networks == ""
    assert case.meta.production_companies == []
    assert case.meta.genres == ["Drama", "Mystery"]
    assert case.meta.artwork_url == "/imdb-poster.jpg"


@pytest.mark.asyncio
async def test_tv_uses_series_premiere_year_instead_of_season_year(case):
    case.meta.category = "TV"
    case.meta.search_year = "2024"
    case.filename = "Example Series"
    case.manager.search_candidates.return_value = [{"id": 101, "category": "TV", "name": "Example Series", "first_air_date": "2016-01-01"}]
    case.tmdb_data[101].update(title="Example Series", original_title="Example Series", year=2016)
    case.imdb_data[1234567].update(title="Example Series", year=2016, type="tvSeries")

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id, case.meta.year) == (101, 1234567, 2016)


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [False, True])
async def test_tv_episode_imdb_is_normalized_to_parent_series(case, monkeypatch, manual):
    case.meta.category = "TV"
    case.filename = "Example Series"
    case.meta.imdb_manual = 1234567 if manual else 0
    case.manager.search_candidates.return_value = [{"id": 101, "category": "TV", "name": "Example Series", "first_air_date": "2016-01-01"}]
    case.manager.get_tmdb_from_imdb.return_value = ("TV", 101, "en", False)
    case.tmdb_data[101].update(title="Example Series", original_title="Example Series", year=2016)
    case.imdb_data[1234567].update(title="Example Episode", year=2024, type="tvEpisode")
    case.imdb_data[7654321].update(title="Example Series", year=2016, type="tvSeries")
    monkeypatch.setattr(metadata_resolution.imdb_manager, "get_imdb_from_episode", AsyncMock(return_value={"series": {"series_id": "tt7654321"}}))

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (101, 7654321)
    assert case.meta.imdb_manual == (7654321 if manual else 0)


@pytest.mark.asyncio
async def test_lookup_does_not_change_original_metadata_before_commit(case):
    case.meta.title = "Carried Title"

    async def lookup(imdb_id, **_kwargs):
        assert case.meta.title == "Carried Title"
        assert case.meta.tmdb_id is None
        assert case.meta.imdb_id is None
        return case.imdb_data[imdb_id]

    case.imdb_info.side_effect = lookup

    await resolve(case)

    assert case.meta.title == "Example Movie"


@pytest.mark.asyncio
async def test_validated_cached_metadata_survives_a_tmdb_outage(case):
    case.meta.tmdb_id = 101
    case.meta.title = "Example Movie"
    case.meta.year = 2020
    case.meta.tmdb_imdb_id = 1234567
    case.meta.metadata_identity = {"category": "MOVIE", "tmdb_id": 101, "imdb_id": 1234567}
    case.manager.set_tmdb_metadata.side_effect = httpx.ReadTimeout("temporary outage")

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (101, 1234567)
    assert case.meta.title == "Example Movie"


@pytest.mark.asyncio
@pytest.mark.parametrize(("title", "linked_title"), [("Example Movie II", "Example Movie III"), ("V Example", "5 Example")])
async def test_different_numbered_title_is_rejected_even_when_tmdb_links_it(case, title, linked_title):
    case.filename = title
    case.manager.search_candidates.return_value[0]["title"] = title
    case.tmdb_data[101].update(title=title, original_title=title)
    case.imdb_data[1234567]["title"] = linked_title
    case.imdb_search.return_value = []

    await resolve(case)

    assert case.meta.tmdb_id == 101
    assert case.meta.imdb_id == 0
    assert case.meta.no_imdb is False


@pytest.mark.asyncio
async def test_replaced_tvdb_link_clears_cached_episode_data(case):
    case.meta.tmdb_id = 101
    case.meta.tvdb_id = 303
    case.meta.tvdb_episode_name = "Old Episode"
    case.meta.we_checked_tvdb = True
    case.tmdb_data[101]["tvdb_id"] = 505

    await resolve(case)

    assert case.meta.tvdb_id == 505
    assert case.meta.tvdb_episode_name is None
    assert case.meta.we_checked_tvdb is False


@pytest.mark.asyncio
async def test_manual_tmdb_change_does_not_reuse_previous_identity_caches(case):
    case.meta.tmdb_id = case.meta.tmdb_manual = 202
    case.meta.metadata_identity = {"category": "MOVIE", "tmdb_id": 101, "imdb_id": 1234567}
    case.meta.tmdb_episode_data = {"name": "Old Episode"}
    case.meta.tmdb_localized_data = {"title": "Old Title"}

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (202, 7654321)
    assert case.meta.tmdb_episode_data is None
    assert case.meta.tmdb_localized_data == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["tmdb", "imdb"])
async def test_unverified_explicit_ids_are_not_silently_replaced(case, provider):
    if provider == "imdb":
        case.meta.tmdb_id = 101
        case.meta.imdb_manual = 7654321
        case.imdb_data[7654321] = {}
    else:
        case.meta.tmdb_manual = 202
        del case.tmdb_data[202]

    with pytest.raises(ValueError, match="Unable to verify an explicit metadata ID"):
        await resolve(case)

    assert getattr(case.meta, f"{provider}_manual") == (202 if provider == "tmdb" else 7654321)


@pytest.mark.asyncio
async def test_manual_imdb_input_accepts_leading_zeroes(case):
    case.meta.unattended = False
    case.imdb_data[1234567]["title"] = "Unrelated Movie"
    case.imdb_data[1234] = {"imdbID": "tt0001234", "title": "Example Movie", "year": 2020, "type": "movie"}
    case.imdb_search.return_value = []
    case.prompt.return_value = "tt0001234"

    await resolve(case)

    assert case.meta.imdb_id == case.meta.imdb_manual == 1234


@pytest.mark.asyncio
async def test_manual_imdb_category_is_used_for_tmdb_title_fallback(case):
    case.meta.tmdb_id = 101
    case.meta.imdb_manual = 1234567
    case.imdb_data[1234567].update(title="Example Series", year=2016, type="tvSeries")
    case.tmdb_data[101].update(title="Example Series", original_title="Example Series", year=2016)

    async def search(_title, _year, category, **_kwargs):
        assert category == "TV"
        return [{"id": 101, "category": "TV", "name": "Example Series", "first_air_date": "2016-01-01"}]

    case.manager.search_candidates.side_effect = search

    await resolve(case)

    assert (case.meta.category, case.meta.tmdb_id, case.meta.imdb_id) == ("TV", 101, 1234567)


@pytest.mark.asyncio
async def test_unsupported_manual_imdb_type_requires_correction(case):
    case.meta.imdb_manual = 1234567
    case.imdb_data[1234567]["type"] = "podcastEpisode"

    with pytest.raises(ValueError, match="Unable to verify an explicit metadata ID"):
        await resolve(case)

    assert case.meta.imdb_manual == 1234567


@pytest.mark.asyncio
async def test_search_can_recover_from_conflicting_explicit_ids(case):
    case.meta.unattended = False
    case.meta.tmdb_manual = 101
    case.meta.imdb_manual = 7654321
    case.prompt.return_value = "search Example Movie 2020"

    await resolve(case)

    assert (case.meta.tmdb_id, case.meta.imdb_id) == (101, 1234567)
    case.prompt.assert_awaited_once()
