import json
from unittest.mock import AsyncMock

import pytest

from src.get_name import NameManager
from src.meta import Meta
from src.metadata_cache import cache_for
from src.tmdb import get_tmdb_primary_title
from src.trackers.UNIT3D.aither import Aither
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
@pytest.mark.parametrize('source', ['PAL DVD', 'NTSC DVD', 'DVD'])
@pytest.mark.parametrize('category,no_year,season', [('MOVIE', True, ''), ('TV', True, 'S01'), ('TV', False, 'S01')])
async def test_dvd_remux_keeps_foreign_language_without_year(source, category, no_year, season):
    meta = release('Example Show', 'Example Show', category, type='REMUX', source=source,
                   resolution='576p', video_codec='MPEG-2', audio_languages=['French'], no_year=no_year, season=season)
    before = meta.to_dict()
    prefix = f'Example Show {season}'.strip()
    assert (await Aither(CONFIG).get_name(meta))['name'] == f'{prefix} FRENCH 576p {source} REMUX MPEG-2 DD+ 5.1-RGroup'
    assert meta.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('title', ['DVD Story', '2001: Example Story', 'A DVD REMUX Story', 'A 576p Story', 'DD+ 5.1 Story'])
@pytest.mark.parametrize('category,season', [('MOVIE', ''), ('TV', 'S01')])
async def test_dvd_remux_formats_release_details_without_changing_title(title, category, season):
    meta = release(title, title, category, type='REMUX', source='DVD', year=2001,
                   manual_year=2001, season=season, resolution='576p', video_codec='MPEG-2', audio_languages=['French'])
    before = meta.to_dict()
    prefix = f'{title} 2001 {season}'.strip()
    assert (await Aither(CONFIG).get_name(meta))['name'] == f'{prefix} FRENCH 576p DVD REMUX MPEG-2 DD+ 5.1-RGroup'
    assert meta.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('tag,expected_tag', [
    ('-NoGroup', ''),
    ('-NOGRP', ''),
    ('-nOgRoUp', ''),
    ('-nogrp', ''),
    ('-UNKNOWN', ''),
    ('-UNK-', ''),
    (' - NoGroup ', ''),
    ('-RGroup', '-RGroup'),
    ('-NoGroupExtra', '-NoGroupExtra'),
    ('-NOGRPExtra', '-NOGRPExtra'),
    ('', ''),
])
@pytest.mark.parametrize('name_type', ['WEBDL', ''])
async def test_placeholder_release_groups(tag, expected_tag, name_type):
    meta = release('Example NoGroup Story', 'Example NoGroup Story', type=name_type, tag=tag,
                   name=f'Example NoGroup Story 2020 1080p WEB-DL DD+ 5.1 H.264{tag}')
    before = meta.to_dict()
    assert (await Aither(CONFIG).get_name(meta))['name'] == f'Example NoGroup Story 2020 1080p WEB-DL DD+ 5.1 H.264{expected_tag}'
    assert meta.to_dict() == before


@pytest.mark.asyncio
async def test_placeholder_release_group_with_trump_and_incomplete_markers():
    meta = release('Example Show', 'Example Show', 'TV', tag='-NOGRP', season='S01',
                   tv_pack=True, season_pack_incomplete=True, trump_reason='exact_match')
    assert (await Aither(CONFIG).get_name(meta))['name'] == 'Example Show S01 INCOMPLETE 1080p WEB-DL DD+ 5.1 H.264 - TRUMP'


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
    year_lookup.assert_not_awaited()


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
    name = await NameManager(CONFIG).render_tracker_name(meta, use_tmdb_title=use_tmdb_title, tvdb_year=tvdb_year, aka_before_year=aka_before_year)
    assert name.startswith(prefix)
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


@pytest.mark.parametrize('search_year,manual_year,no_year,expected_year', [
    (2020, 0, False, '2020'),
    ('', 1999, False, '1999'),
    (2020, 1999, False, '1999'),
    (2020, 1999, True, ''),
])
def test_shared_renderer_keeps_order_and_honors_year_flags(search_year, manual_year, no_year, expected_year):
    meta = release('Title', 'Original', 'TV', title='Shared title', aka='AKA Shared alternate', season='S01',
                   search_year=search_year, manual_year=manual_year, no_year=no_year)
    before = meta.to_dict()
    prefix = ' '.join(part for part in ('Shared title', expected_year, 'AKA Shared alternate', 'S01') if part)
    assert NameManager(CONFIG).render_name(meta)[1].startswith(prefix)
    assert meta.to_dict() == before


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
