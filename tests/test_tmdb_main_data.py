from unittest.mock import Mock

import httpx
import pytest

import src.tmdb as tmdb
from src.meta import Meta

CONFIG = {'DEFAULT': {'tmdb_api': 'test-key'}}
MOVIE = {'title': 'Example Film', 'original_title': 'Original Film', 'release_date': '2020-01-01', 'overview': 'Example', 'original_language': 'en'}
TV = {'name': 'Example Show', 'original_name': 'Original Show', 'first_air_date': '2021-01-01', 'overview': 'Example', 'original_language': 'en'}


def mock_tmdb(monkeypatch, payload, status=200):
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if request.url.path in ('/3/movie/123', '/3/tv/123'):
            return httpx.Response(status, json=payload)
        return httpx.Response(200, json={})

    monkeypatch.setattr(tmdb.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(tmdb, 'tmdb_api_key', 'test-key')
    monkeypatch.setattr(tmdb, 'default_config', {})
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize('category,payload,expected', [
    ('MOVIE', MOVIE, ('Example Film', 'Original Film')),
    ('TV', TV, ('Example Show', 'Original Show')),
])
@pytest.mark.parametrize('title_first', [True, False])
async def test_title_and_full_metadata_share_cached_main_response(tmp_path, monkeypatch, category, payload, expected, title_first):
    requests = mock_tmdb(monkeypatch, payload)
    meta = Meta(category=category, tmdb_id=123, base_dir=str(tmp_path))

    async def read_metadata():
        return await tmdb.tmdb_other_meta(123, category=category, base_dir=str(tmp_path), config=CONFIG)

    if title_first:
        title = await tmdb.get_tmdb_primary_title(meta, CONFIG)
        metadata = await read_metadata()
    else:
        metadata = await read_metadata()
        title = await tmdb.get_tmdb_primary_title(meta, CONFIG)

    assert title == expected
    assert (metadata['tmdb_title'], metadata['original_title']) == expected
    assert metadata['year'] == (2020 if category == 'MOVIE' else 2021)
    main_requests = [request for request in requests if request.url.path.endswith('/123')]
    assert len(main_requests) == 1
    assert main_requests[0].url.params['language'] == 'en-US'
    assert main_requests[0].url.params['api_key'] == 'test-key'
    assert meta.tmdb_title == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('payload,status,error', [
    ({'status_message': 'Unavailable'}, 503, httpx.HTTPStatusError),
    ([], 200, ValueError),
])
async def test_failed_main_response_is_not_cached(tmp_path, monkeypatch, payload, status, error):
    requests = mock_tmdb(monkeypatch, payload, status)
    meta = Meta(category='MOVIE', tmdb_id=123, base_dir=str(tmp_path))
    with pytest.raises(error):
        await tmdb.get_tmdb_primary_title(meta, CONFIG)
    # Metadata preparation keeps its existing empty-result behavior on failure.
    assert await tmdb.tmdb_other_meta(123, category='MOVIE', base_dir=str(tmp_path), config=CONFIG) == {}
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_recorded_tmdb_titles_need_no_http_client(monkeypatch):
    client = Mock(side_effect=AssertionError('Recorded titles must not create an HTTP client'))
    monkeypatch.setattr(tmdb.httpx, 'AsyncClient', client)
    meta = Meta(tmdb_title='Example Film', original_title='Original Film')
    assert await tmdb.get_tmdb_primary_title(meta, {}) == ('Example Film', 'Original Film')
    client.assert_not_called()
