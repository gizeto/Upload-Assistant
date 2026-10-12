"""Regression tests for LST-specific upload payloads."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.meta import Meta
from src.trackers.UNIT3D.lst import LST


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["MOVIE", "TV"])
@pytest.mark.parametrize("source,type_id", [
    ("SDTV", "16"),
    ("TVRip", "15"),
    ("VHSRip", "15"),
    ("", "15"),
])
async def test_lst_capture_upload_automatically_uses_correct_type(monkeypatch, category, source, type_id):
    episode = " S01E02" if category == "TV" else ""
    source_label = f"{source} " if source else ""
    release_name = f"Example Title 1996{episode} 480p {source_label}AAC 2.0 x264-GROUP"
    meta = Meta(
        category=category, type="OTHER", source=source, name=release_name, resolution="480p",
    )
    tracker = LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}})
    for method in ("get_description", "get_mediainfo", "get_bdinfo"):
        monkeypatch.setattr(tracker, method, AsyncMock(return_value={}))

    data = await tracker.get_data(meta)

    assert data["name"] == release_name
    assert data["type_id"] == type_id
    assert data["resolution_id"] == "8"
    assert meta.type == "OTHER"


@pytest.mark.asyncio
async def test_lst_sdtv_mapping_preserves_explicit_type():
    meta = Meta(category="TV", type="OTHER", source="SDTV")
    tracker = LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}})

    assert (await tracker.get_type_id(meta, reverse=True))["16"] == "SDTV"
    assert (await tracker.get_type_id(meta, mapping_only=True))["SDTV"] == "16"
    assert await tracker.get_type_id(meta, media_type="OTHER") == {"type_id": "15"}


@pytest.mark.asyncio
@pytest.mark.parametrize("category,release_type,source,expected", [
    ("TV", "OTHER", "sdtv", "16"),
    ("MOVIE", "ENCODE", "SDTV", "3"),
    ("MOVIE", "HDTV", "SDTV", "6"),
    ("MUSIC", "OTHER", "SDTV", "15"),
])
async def test_lst_sdtv_selection_preserves_other_types(category, release_type, source, expected):
    meta = Meta(category=category, type=release_type, source=source)
    tracker = LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}})

    assert await tracker.get_type_id(meta) == {"type_id": expected}


def test_lst_music_payload_includes_discogs_release_and_master_ids():
    meta = Meta(
        category="MUSIC",
        music_release={"external_ids": {"discogs_release": "https://www.discogs.com/release/12345-example", "discogs_master": "master/67890"}},
    )

    data = asyncio.run(LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}}).get_additional_data(meta))

    assert data["release_exists_on_discogs"] == "1"
    assert data["discogs"] == "12345"
    assert data["discogs_master_id"] == "67890"
    assert data["extra_discogs_ids"] == ""
    assert data["extra_discogs_master_ids"] == ""


def test_lst_music_payload_omits_discogs_existence_flag_for_invalid_ids():
    meta = Meta(category="MUSIC", music_release={"external_ids": {"discogs_release": "not-an-id"}})

    data = asyncio.run(LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}}).get_additional_data(meta))

    assert "release_exists_on_discogs" not in data


def test_lst_music_name_uses_technical_fields_for_lossless_releases():
    meta = Meta(
        category="MUSIC",
        tag="-FiVE0",
        music_release={
            "fields": {
                "artist": {"value": "Example Artist"},
                "album": {"value": "Sample Album"},
                "release_year": {"value": "2012"},
                "media": {"value": "WEB"},
            },
            "tracks": [{"codec": "FLAC", "bit_depth": 16, "sample_rate": 44100}],
        },
    )

    name = asyncio.run(LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}}).get_name(meta))["name"]

    assert name == "Example Artist - Sample Album 2012 WEB FLAC 16-bit 44.1 kHz-FiVE0"


def test_lst_music_name_ignores_invalid_sample_rate():
    meta = Meta(
        category="MUSIC",
        music_release={
            "fields": {
                "artist": {"value": "Artist"},
                "album": {"value": "Album"},
                "release_year": {"value": "2000"},
                "media": {"value": "CD"},
                "nfo_sample_rate": {"value": "unknown"},
            },
            "tracks": [{"codec": "FLAC", "bit_depth": 16}],
        },
    )

    name = asyncio.run(LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}}).get_name(meta))["name"]

    assert name == "Artist - Album 2000 CD FLAC 16-bit"


def test_lst_audiobook_name_omits_lossy_technical_fields():
    meta = Meta(category="BOOK", audiobook=True, author="Sample Author", title="Invented Audiobook", year=2011, source="WEB", type="M4B", tag="zeno")

    name = asyncio.run(LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}}).get_name(meta))["name"]

    assert name == "Sample Author - Invented Audiobook 2011 WEB M4B-zeno"


def test_lst_ebook_name_includes_edition_type_and_isbn():
    meta = Meta(
        category="BOOK",
        author="Example Author",
        title="Fictional Book",
        edition="Revised Edition",
        year=2008,
        type="PDF",
        ocr=True,
        isbn="978-0123456472",
        tag="-GROUP",
    )

    name = asyncio.run(LST({"DEFAULT": {}, "TRACKERS": {"LST": {}}}).get_name(meta))["name"]

    assert name == "Example Author - Fictional Book Revised Edition 2008 PDF OCR 9780123456472-GROUP"
