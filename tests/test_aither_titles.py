import json
from unittest.mock import AsyncMock

import pytest

from src.get_name import NameManager
from src.meta import Meta
from src.metadata_cache import cache_for
from src.tmdb import get_tmdb_primary_title
from src.trackers.UNIT3D.aither import Aither
from src.trackers.naming import select_aka
from src.tvdb import TvdbData

CONFIG = {'DEFAULT': {}, 'TRACKERS': {'AITHER': {}}}


def release(primary, original, category='MOVIE', **kwargs):
    data = dict(category=category, type='WEBDL', tmdb_title=primary, title='Overwritten TVDB title',
                original_title=original, imdb_info={'aka': original}, year=2020, resolution='1080p',
                video_encode='H.264', audio='DD+ 5.1', tag='-RGroup', source='Web',
                language_checked=True, audio_languages=['English'], uhd='', service='')
    data.update(kwargs)
    return Meta(**data)


@pytest.mark.asyncio
@pytest.mark.parametrize('meta,expected', [
    (release('The Example Documentary', 'El documental de ejemplo', resolution='2160p', service='NF', video_encode='H.265', audio_languages=['Spanish']),
     'The Example Documentary AKA El documental de ejemplo 2020 SPANISH 2160p NF WEB-DL DD+ 5.1 H.265-RGroup'),
    (release('Example Drama', 'Przykładowy serial', 'TV', service='AMZN', season='S01', episode='E04', audio_languages=['Polish']),
     'Example Drama AKA Przykładowy serial S01E04 POLISH 1080p AMZN WEB-DL DD+ 5.1 H.264-RGroup'),
    (release('The Example Film', "L'exemple fictif", year=2013, service='AMZN', audio='DD+ 2.0', audio_languages=['French']),
     "The Example Film AKA L'exemple fictif 2013 FRENCH 1080p AMZN WEB-DL DD+ 2.0 H.264-RGroup"),
    (release("Example's Fictional B&B", 'Yesi peurogeuraem', 'TV', service='NF', season='S02', episode='E04', audio='Dual-Audio DD+ 5.1'),
     "Example's Fictional B&B AKA Yesi peurogeuraem S02E04 1080p NF WEB-DL Dual-Audio DD+ 5.1 H.264-RGroup"),
    (release('Example Romance', 'Örnek 2: Ask', year=2019, audio_languages=['Turkish']),
     'Example Romance AKA Örnek 2: Ask 2019 TURKISH 1080p WEB-DL DD+ 5.1 H.264-RGroup'),
    (release('Example Comedy', 'Título de ejemplo', year=2023, service='NF', audio_languages=['Spanish']),
     'Example Comedy AKA Título de ejemplo 2023 SPANISH 1080p NF WEB-DL DD+ 5.1 H.264-RGroup'),
])
async def test_generic_release_names(meta, expected):
    before = meta.to_dict()
    assert (await Aither(CONFIG).get_name(meta))['name'] == expected
    assert meta.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('original,expected_aka', [
    ('काल्पनिक शीर्षक', ''),
    ('架空の題名THEサンプル', ''),
    ('Exêmple fictif: Histôire imaginaire', ' AKA Exêmple fictif: Histôire imaginaire'),
    ('Titre inventé naïf', ' AKA Titre inventé naïf'),
    ('Exemple (Histoire héroïque)', ' AKA Exemple (Histoire héroïque)'),
])
async def test_aka_requires_latin_script(original, expected_aka):
    meta = release('Example Title', original)
    name = (await Aither(CONFIG).get_name(meta))['name']
    assert name == f'Example Title{expected_aka} 2020 1080p WEB-DL DD+ 5.1 H.264-RGroup'


@pytest.mark.asyncio
@pytest.mark.parametrize('primary,alternate,expected', [
    ('Example Hero: Let A Story Begin', 'Example Hero', 'Example Hero: Let A Story Begin'),
    ('Examples and Stories', 'Examples & Stories', 'Examples and Stories'),
    ('The Example Character', 'example CHARACTER', 'The Example Character'),
    ('Revisited: Example By Example', 'Example by Example', 'Revisited: Example By Example'),
    ('Example Bird: Beginning', 'example bird - beginning of story', 'Example Bird: Beginning of story'),
    ('Example Bird: Beginning', 'EXAMPLE BIRD / BEGINNING: More', 'Example Bird: Beginning: More'),
    ('An Example & A Story', 'Example and Story', 'An Example & A Story'),
    ('Example Storybook', 'Story', 'Example Storybook AKA Story'),
    ('Example Bird', 'A Story About Example Bird', 'Example Bird AKA A Story About Example Bird'),
    ('Example Bird', 'The Example Bird: More', 'Example Bird AKA The Example Bird: More'),
])
async def test_overlapping_aka_titles(primary, alternate, expected):
    meta = release(primary, alternate)
    before = meta.to_dict()
    name = (await Aither(CONFIG).get_name(meta))['name']
    assert name == f'{expected} 2020 1080p WEB-DL DD+ 5.1 H.264-RGroup'
    assert meta.to_dict() == before


@pytest.mark.parametrize('original,title,expected', [
    ('A title!', 'a TITLE', ''),
    ('The Example Documentary', 'The Example Documentary', ''),
    ('El documental de ejemplo', 'The Example Documentary', 'AKA El documental de ejemplo'),
    ('Örnek 2: Aşk', 'Ornek 2 Ask', ''),
])
def test_aka_equality(original, title, expected):
    assert select_aka(release(title, original), title) == expected


def test_aka_fallback_and_romanized_alias():
    meta = release('English', '한국어', imdb_info={'aka': 'English'})
    assert select_aka(meta, 'English') == ''
    meta.imdb_info['akas'] = [{'title': 'Hangug-eo', 'attributes': [{'text': 'romanized title'}]}]
    assert select_aka(meta, 'English') == 'AKA Hangug-eo'
    meta.no_aka = True
    assert select_aka(meta, 'English') == ''


@pytest.mark.parametrize('imdb_aka,original,prepared,anime,expected', [
    ('IMDb alternate', 'TMDB original', 'AKA Anime title', True, 'AKA IMDb alternate'),
    ('PRIMARY!', 'TMDB original', '', False, 'AKA TMDB original'),
    ('한국어', 'TMDB original', '', False, ''),
    ('한국어', 'TMDB original', 'AKA Anime title', True, 'AKA Anime title'),
    ('Primary', 'PRIMARY!', 'AKA Anime title', True, 'AKA Anime title'),
    ('Primary', 'PRIMARY!', 'AKA Anime title', False, ''),
])
def test_aka_source_priority(imdb_aka, original, prepared, anime, expected):
    meta = release('Primary', original, imdb_info={'aka': imdb_aka}, retrieved_aka=prepared, anime=anime)
    before = meta.to_dict()
    assert select_aka(meta, 'Primary') == expected
    assert meta.to_dict() == before


@pytest.mark.parametrize('language,expected', [('Korean', 'AKA Hangug-eo'), (None, 'AKA Hangug-eo'), ('Unknown language', '')])
def test_romanization_filters_aliases_before_selection(language, expected):
    meta = release('Primary', '한국어', original_language='ko', imdb_info={'aka': 'Primary', 'akas': [
        None,
        {'title': 'Wrong language', 'language': 'Japanese', 'attributes': ['romanized title']},
        {'title': 'Unmarked alias', 'language': 'Korean'},
        {'title': '한국어', 'language': 'Korean', 'attributes': ['romanized title']},
        {'title': 'PRIMARY!', 'language': 'Korean', 'attributes': ['romanised title']},
        {'title': 'Hangug-eo', 'language': language, 'attributes': ['transliterated title']},
    ]})
    assert select_aka(meta, 'Primary') == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('title,year,other,expected', [
    ('Example Show…?', 2021, 'EXAMPLE / SHOW', '2021'),
    ('EXAMPLE / SHOW', 2019, 'Example Show…?', '2019'),
    ('Example Show', 2018, 'EXAMPLE / SHOW', '2018'),
    ('The Example Series', 1981, 'The Example Series (2009)', '1981'),
    ('The Example Series (2009)', 2009, '', '2009'),
    ('The Example Mystery', 2025, 'Another Mystery', ''),
    ('Example Character', 2026, 'Example Character and Friends', ''),
    ('Example Reality Show', 2025, 'Example Reality Show Again', ''),
])
async def test_generic_tvdb_title_variants(tmp_path, monkeypatch, title, year, other, expected):
    client = AsyncMock()
    client.search.return_value = [{'tvdb_id': '1', 'name': title}, {'tvdb_id': '2', 'name': other}]
    monkeypatch.setattr('src.tvdb._get_tvdb_or_warn', lambda _: client)
    meta = Meta(tvdb_id=1, tvdb_series_name=title, year=year, base_dir=str(tmp_path))
    handler = TvdbData(CONFIG)
    assert await handler.get_naming_year(meta) == expected
    assert await handler.get_naming_year(meta) == expected
    assert client.search.await_count == (0 if '(2009)' in title else 1)
    if client.search.await_count:
        assert 'year' not in client.search.call_args.kwargs


@pytest.mark.asyncio
async def test_distinct_ids_and_provider_failure(tmp_path, monkeypatch):
    client = AsyncMock()
    client.search.return_value = [{'tvdb_id': '1', 'name': 'Title'}] * 2
    monkeypatch.setattr('src.tvdb._get_tvdb_or_warn', lambda _: client)
    handler = TvdbData(CONFIG)
    meta = Meta(tvdb_id=1, tvdb_series_name='Title', year=2020, base_dir=str(tmp_path))
    assert await handler.get_naming_year(meta) == ''
    meta.tvdb_series_name = 'Uncached title'
    client.search.side_effect = RuntimeError('offline')
    assert await handler.get_naming_year(meta) == ''
    client.search.side_effect = None
    client.search.return_value = [{'tvdb_id': '2', 'name': 'Uncached title'}]
    assert await handler.get_naming_year(meta) == '2020'


@pytest.mark.asyncio
async def test_naming_overrides(monkeypatch):
    year_lookup = AsyncMock(return_value='2020')
    monkeypatch.setattr(TvdbData, 'get_naming_year', year_lookup)
    tracker = Aither(CONFIG)
    meta = release('Title', 'Original', 'TV', season='S01', episode='E01', manual_year=1999)
    assert (await tracker.get_name(meta))['name'].startswith('Title AKA Original 1999 S01E01')
    meta.no_year = meta.no_aka = True
    assert (await tracker.get_name(meta))['name'].startswith('Title S01E01')
    meta.manual_name = 'My exact name'
    assert (await tracker.get_name(meta))['name'] == 'My exact name'


@pytest.mark.asyncio
async def test_manual_name_keeps_trump_and_incomplete_markers(monkeypatch):
    title_lookup = AsyncMock()
    year_lookup = AsyncMock()
    monkeypatch.setattr('src.get_name.get_tmdb_primary_title', title_lookup)
    monkeypatch.setattr(TvdbData, 'get_naming_year', year_lookup)
    meta = release('', '', 'TV', manual_name=' My exact name S01 ', trump_reason='exact_match',
                   tv_pack=True, season='S01', season_pack_incomplete=True)
    before = meta.to_dict()
    assert (await Aither(CONFIG).get_name(meta))['name'] == 'My exact name S01 INCOMPLETE - TRUMP'
    assert meta.to_dict() == before
    title_lookup.assert_not_awaited()
    year_lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('original,retrieved,existing', [
    ('日本語の題名', 'AKA Nihongo no daimei', ''),
    ('日本語の題名', None, 'AKA Nihongo no daimei'),
    ('English title', 'AKA Nihongo no daimei', ''),
])
async def test_anime_preserves_prepared_romanized_aka(original, retrieved, existing):
    meta = release('English title', original, anime=True, original_language='ja',
                   retrieved_aka=retrieved, aka=existing, imdb_info={'aka': 'English title', 'akas': []})
    before = meta.to_dict()
    assert (await Aither(CONFIG).get_name(meta))['name'].startswith('English title AKA Nihongo no daimei 2020')
    assert meta.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('use_tmdb_title,tvdb_year,aka_before_year,prefix,lookup_count', [
    (False, False, False, 'Shared title AKA Existing alternate S01', 0),
    (True, False, False, 'TMDB title AKA Original S01', 0),
    (False, True, False, 'Shared title 1981 AKA Existing alternate S01', 1),
    (True, True, True, 'TMDB title AKA Original 1981 S01', 1),
])
async def test_tracker_naming_options_are_independent(monkeypatch, use_tmdb_title, tvdb_year, aka_before_year, prefix, lookup_count):
    year_lookup = AsyncMock(return_value='1981')
    monkeypatch.setattr(TvdbData, 'get_naming_year', year_lookup)
    meta = release('TMDB title', 'Original', 'TV', title='Shared title', aka='AKA Existing alternate', season='S01')
    before = meta.to_dict()
    name, year = await NameManager(CONFIG).render_tracker_name(meta, use_tmdb_title=use_tmdb_title, tvdb_year=tvdb_year, aka_before_year=aka_before_year)
    assert name.startswith(prefix)
    assert year == ('1981' if tvdb_year else '')
    assert year_lookup.await_count == lookup_count
    assert meta.to_dict() == before


@pytest.mark.asyncio
async def test_legacy_metadata_recovers_cached_tmdb_title(tmp_path):
    meta = release('Title', 'Original', tmdb_title='', tmdb_id=123, base_dir=str(tmp_path))
    cache = cache_for(str(tmp_path), CONFIG)
    await cache.set('tmdb', 'main', json.dumps({'category': 'MOVIE', 'id': 123}, sort_keys=True), {'title': 'Verified title', 'original_title': 'Original'})
    assert (await get_tmdb_primary_title(meta, CONFIG)) == ('Verified title', 'Original')
    assert (await Aither(CONFIG).get_name(meta))['name'].startswith('Verified title AKA Original 2020')
    assert not meta.tmdb_title


@pytest.mark.asyncio
async def test_missing_tmdb_does_not_use_tvdb():
    meta = release('Title', 'Original', tmdb_title='', tvdb_series_name='TVDB title')
    with pytest.raises(ValueError, match='requires TMDB'):
        await Aither(CONFIG).get_name(meta)


@pytest.mark.asyncio
async def test_tmdb_title_errors_are_provider_specific(tmp_path, monkeypatch):
    monkeypatch.setattr('src.tmdb.tmdb_api_key', None)
    meta = release('', '', tmdb_id=123, base_dir=str(tmp_path))
    with pytest.raises(ValueError, match='^TMDB API key is missing for metadata lookup$'):
        await get_tmdb_primary_title(meta, CONFIG)
    cache = cache_for(str(tmp_path), CONFIG)
    await cache.set('tmdb', 'main', json.dumps({'category': 'MOVIE', 'id': 123}, sort_keys=True), {'title': ''})
    with pytest.raises(ValueError, match='^TMDB primary title is missing$'):
        await get_tmdb_primary_title(meta, CONFIG)


def test_shared_renderer_keeps_existing_order_and_metadata():
    meta = release('Title', 'Original', 'TV', title='Shared title', aka='AKA Shared alternate', season='S01', search_year=2020)
    before = meta.to_dict()
    assert NameManager(CONFIG).render_name(meta)[1].startswith('Shared title 2020 AKA Shared alternate S01')
    assert meta.to_dict() == before


@pytest.mark.asyncio
async def test_tvdb_search_paginates_without_language_filter(tmp_path, monkeypatch):
    client = AsyncMock()
    client.search.side_effect = [
        [{'tvdb_id': str(i + 10), 'name': f'Other {i}'} for i in range(100)],
        [{'id': 'series-2', 'name': 'Titre', 'translations': {'eng': 'Title'}}],
    ]
    monkeypatch.setattr('src.tvdb._get_tvdb_or_warn', lambda _: client)
    meta = Meta(tvdb_id=1, tvdb_series_name='Title', year=2020, base_dir=str(tmp_path))
    assert await TvdbData(CONFIG).get_naming_year(meta) == '2020'
    assert [call.kwargs['offset'] for call in client.search.call_args_list] == [0, 100]
    assert all('language' not in call.kwargs for call in client.search.call_args_list)


@pytest.mark.asyncio
async def test_tvdb_repeated_page_failure_is_retried(tmp_path, monkeypatch):
    page = [{'tvdb_id': str(i + 10), 'name': f'Other {i}'} for i in range(100)]
    client = AsyncMock()
    client.search.side_effect = [page, page, [{'tvdb_id': '2', 'name': 'Title'}]]
    monkeypatch.setattr('src.tvdb._get_tvdb_or_warn', lambda _: client)
    meta = Meta(tvdb_id=1, tvdb_series_name='Title', year=2020, base_dir=str(tmp_path))
    handler = TvdbData(CONFIG)
    assert await handler.get_naming_year(meta) == ''
    assert await handler.get_naming_year(meta) == '2020'
    assert await handler.get_naming_year(meta) == '2020'
    assert client.search.await_count == 3


@pytest.mark.asyncio
async def test_tvdb_can_load_selected_series(tmp_path, monkeypatch):
    client = AsyncMock()
    client.get_series_extended.return_value = {'name': 'Original', 'firstAired': '1981-09-10'}
    client.get_series_translation.return_value = {'name': 'The Example Series (1981)'}
    monkeypatch.setattr('src.tvdb._get_tvdb_or_warn', lambda _: client)
    meta = Meta(tvdb_id=1, year=2009, base_dir=str(tmp_path))
    assert await TvdbData(CONFIG).get_naming_year(meta) == '1981'
    client.search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('title,search_count', [('Example Show (2020)', 0), ('Example Show', 1)])
async def test_tvdb_missing_translation_uses_extended_metadata(tmp_path, monkeypatch, title, search_count):
    client = AsyncMock()
    client.get_series_extended.return_value = {'name': title, 'firstAired': '2020-01-01'}
    client.get_series_translation.side_effect = RuntimeError('No English translation')
    client.search.return_value = [{'tvdb_id': '2', 'name': 'Example Show (1981)'}]
    monkeypatch.setattr('src.tvdb._get_tvdb_or_warn', lambda _: client)
    meta = Meta(tvdb_id=1, year=1999, base_dir=str(tmp_path))
    assert await TvdbData(CONFIG).get_naming_year(meta) == '2020'
    assert client.search.await_count == search_count


@pytest.mark.asyncio
async def test_tv_year_is_rendered_once_before_episode(monkeypatch):
    monkeypatch.setattr(TvdbData, 'get_naming_year', AsyncMock(return_value='1981'))
    meta = release('The Example Series', 'The Example Series', 'TV', year=1981, season='S01')
    name = (await Aither(CONFIG).get_name(meta))['name']
    assert name.startswith('The Example Series 1981 S01')
    assert name.count('1981') == 1
    assert 'AKA' not in name


def test_source_title_survives_metadata_serialization():
    meta = release('TMDB title', 'Original')
    restored = Meta(meta.to_dict())
    restored.title = 'TVDB title'
    assert restored.tmdb_title == 'TMDB title'
    assert Meta({'title': 'Old metadata'}).tmdb_title == ''



def test_romanized_alias_must_match_original_language():
    meta = release('English', '한국어', original_language='ko')
    meta.imdb_info['akas'] = [
        {'title': 'Japanese title', 'language': 'Japanese', 'attributes': [{'text': 'romanized title'}]},
        {'title': 'Hangug-eo', 'language': 'Korean', 'attributes': [{'text': 'romanized title'}]},
    ]
    assert select_aka(meta, 'English') == 'AKA Hangug-eo'
