import pytest

from src.meta import Meta
from src.trackers.UNIT3D.reelflix import ReelFlix


@pytest.mark.asyncio
@pytest.mark.parametrize('tag,expected_tag', [
    ('-NoGroup', '-NoGroup'),
    ('-NOGRP', '-NoGroup'),
    ('-nOgRoUp', '-NoGroup'),
    ('-UNKNOWN', '-NoGroup'),
    ('-UNK-', '-NoGroup'),
    ('', '-NoGroup'),
    ('-RGroup', '-RGroup'),
    ('-NoGroupExtra', '-NoGroupExtra'),
    ('-NOGRPExtra', '-NOGRPExtra'),
])
async def test_placeholder_release_groups(tag, expected_tag):
    meta = Meta(name=f'Example NoGroup Story 2020 1080p WEB-DL{tag}', tag=tag)
    tracker = ReelFlix({'DEFAULT': {}, 'TRACKERS': {'REELFLIX': {}}})
    before = meta.to_dict()
    assert (await tracker.get_name(meta))['name'] == f'Example NoGroup Story 2020 1080p WEB-DL{expected_tag}'
    assert meta.to_dict() == before
