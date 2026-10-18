from unittest.mock import AsyncMock

import pytest

from src.meta import Meta
from src.tvdb import TvdbData

CONFIG = {'DEFAULT': {}}


@pytest.fixture
def tvdb_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr('src.tvdb._get_tvdb_or_warn', lambda _: client)
    return client


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
async def test_generic_tvdb_title_variants(tmp_path, tvdb_client, title, year, other, expected):
    tvdb_client.search.return_value = [{'tvdb_id': '1', 'name': title}, {'tvdb_id': '2', 'name': other}]
    meta = Meta(tvdb_id=1, tvdb_series_name=title, year=year, base_dir=str(tmp_path))
    handler = TvdbData(CONFIG)
    assert await handler.get_naming_year(meta) == expected
    assert await handler.get_naming_year(meta) == expected
    assert tvdb_client.search.await_count == (0 if '(2009)' in title else 1)
    if tvdb_client.search.await_count:
        assert 'year' not in tvdb_client.search.call_args.kwargs


@pytest.mark.asyncio
async def test_distinct_ids_and_provider_failure(tmp_path, tvdb_client):
    tvdb_client.search.return_value = [{'tvdb_id': '1', 'name': 'Title'}] * 2
    handler = TvdbData(CONFIG)
    meta = Meta(tvdb_id=1, tvdb_series_name='Title', year=2020, base_dir=str(tmp_path))
    assert await handler.get_naming_year(meta) == ''
    meta.tvdb_series_name = 'Uncached title'
    tvdb_client.search.side_effect = RuntimeError('offline')
    assert await handler.get_naming_year(meta) == ''
    tvdb_client.search.side_effect = None
    tvdb_client.search.return_value = [{'tvdb_id': '2', 'name': 'Uncached title'}]
    assert await handler.get_naming_year(meta) == '2020'


@pytest.mark.asyncio
async def test_tvdb_search_paginates_without_language_filter(tmp_path, tvdb_client):
    tvdb_client.search.side_effect = [
        [{'tvdb_id': str(i + 10), 'name': f'Other {i}'} for i in range(100)],
        [{'id': 'series-2', 'name': 'Titre', 'translations': {'eng': 'Title'}}],
    ]
    meta = Meta(tvdb_id=1, tvdb_series_name='Title', year=2020, base_dir=str(tmp_path))
    assert await TvdbData(CONFIG).get_naming_year(meta) == '2020'
    assert [call.kwargs['offset'] for call in tvdb_client.search.call_args_list] == [0, 100]
    assert all('language' not in call.kwargs for call in tvdb_client.search.call_args_list)


@pytest.mark.asyncio
async def test_tvdb_repeated_page_failure_is_retried(tmp_path, tvdb_client):
    page = [{'tvdb_id': str(i + 10), 'name': f'Other {i}'} for i in range(100)]
    tvdb_client.search.side_effect = [page, page, [{'tvdb_id': '2', 'name': 'Title'}]]
    meta = Meta(tvdb_id=1, tvdb_series_name='Title', year=2020, base_dir=str(tmp_path))
    handler = TvdbData(CONFIG)
    assert await handler.get_naming_year(meta) == ''
    assert await handler.get_naming_year(meta) == '2020'
    assert await handler.get_naming_year(meta) == '2020'
    assert tvdb_client.search.await_count == 3


@pytest.mark.asyncio
async def test_tvdb_can_load_selected_series(tmp_path, tvdb_client):
    tvdb_client.get_series_extended.return_value = {'name': 'Original', 'firstAired': '1981-09-10'}
    tvdb_client.get_series_translation.return_value = {'name': 'The Example Series (1981)'}
    meta = Meta(tvdb_id=1, year=2009, base_dir=str(tmp_path))
    assert await TvdbData(CONFIG).get_naming_year(meta) == '1981'
    tvdb_client.search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('title,search_count', [('Example Show (2020)', 0), ('Example Show', 1)])
async def test_tvdb_missing_translation_uses_extended_metadata(tmp_path, tvdb_client, title, search_count):
    tvdb_client.get_series_extended.return_value = {'name': title, 'firstAired': '2020-01-01'}
    tvdb_client.get_series_translation.side_effect = RuntimeError('No English translation')
    tvdb_client.search.return_value = [{'tvdb_id': '2', 'name': 'Example Show (1981)'}]
    meta = Meta(tvdb_id=1, year=1999, base_dir=str(tmp_path))
    assert await TvdbData(CONFIG).get_naming_year(meta) == '2020'
    assert tvdb_client.search.await_count == search_count
