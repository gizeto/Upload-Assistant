"""Regression tests for release source detection."""

import json

import pytest

from src.get_name import NameManager
from src.get_source import detect_source, get_source
from src.meta import Meta
from src.video import VideoManager


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["SDTV", "TVRip", "VHSRip"])
@pytest.mark.parametrize("extension", ["", ".mkv"])
async def test_capture_term_in_release_group_is_ignored(source, extension):
    filename = f"Example.Movie.1996.x264-Example {source} Group{extension}"

    assert await detect_source(filename, Meta(category="MOVIE")) == "BluRay"


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution,expected_source,expected_type", [
    ("480p", "", "OTHER"),
    ("480i", "", "OTHER"),
    ("540p", "", "OTHER"),
    ("540i", "", "OTHER"),
    ("576p", "", "OTHER"),
    ("576i", "", "OTHER"),
    ("719p", "", "OTHER"),
    ("720p", "BluRay", "ENCODE"),
    ("1080p", "BluRay", "ENCODE"),
    ("1080i", "BluRay", "ENCODE"),
    ("2160p", "BluRay", "ENCODE"),
    ("OTHER", "BluRay", "ENCODE"),
    ("", "BluRay", "ENCODE"),
])
async def test_unmarked_release_defaults_follow_resolution(tmp_path, resolution, expected_source, expected_type):
    filename = "Example Video - An Example Story.avi"
    meta = Meta(category="MOVIE", resolution=resolution)

    release_type = await VideoManager().get_type(filename, False, "", meta)

    assert release_type == expected_type
    assert await get_source(release_type, filename, filename, "", meta, "case", str(tmp_path)) == (expected_source, expected_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("source,expected_type", [
    ("BluRay", "ENCODE"),
    ("DVD", "DVDRIP"),
    ("TVRip", "OTHER"),
])
async def test_sd_resolution_does_not_override_known_source(tmp_path, manual, source, expected_type):
    filename = "Example.Movie.1996.x264-GROUP.mkv" if manual else f"Example.Movie.1996.{source}.x264-GROUP.mkv"
    meta = Meta(category="MOVIE", resolution="480p", manual_source=source if manual else None)

    release_type = await VideoManager().get_type(filename, False, "", meta)

    assert await get_source(release_type, filename, filename, "", meta, "case", str(tmp_path)) == (source, expected_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["", ".mkv"])
async def test_sd_bdrip_is_detected_as_bluray_encode(tmp_path, extension):
    filename = f"Movie.1964.FRENCH.BDRip.AC3.x264{extension}"
    meta = Meta(category="MOVIE", resolution="480p")

    release_type = await VideoManager().get_type(filename, False, "", meta)

    assert release_type == "ENCODE"
    assert await get_source(release_type, filename, filename, "", meta, "case", str(tmp_path)) == ("BluRay", "ENCODE")


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_type,is_disc,category,expected_source,expected_type", [
    ("ENCODE", "", "MOVIE", "", "ENCODE"),
    ("OTHER", "", "MOVIE", "", "OTHER"),
    ("DVDRIP", "", "MOVIE", "DVD", "DVDRIP"),
    ("REMUX", "", "MOVIE", "DVD", "REMUX"),
    (None, "BDMV", "MOVIE", "Blu-ray", "DISC"),
    (None, "DVD", "MOVIE", "DVD", "DISC"),
    (None, "", "XXX", "Web", "WEBDL"),
])
async def test_sd_defaults_preserve_manual_types_discs_and_xxx(tmp_path, manual_type, is_disc, category, expected_source, expected_type):
    filename = "Example.Video.avi"
    meta = Meta(category=category, resolution="480p", manual_type=manual_type, is_disc=is_disc)

    release_type = await VideoManager().get_type(filename, False, is_disc, meta)

    assert release_type == expected_type
    assert await get_source(release_type, filename, filename, is_disc, meta, "case", str(tmp_path)) == (expected_source, expected_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("framerate,expected_source", [("25.000", "PAL"), ("23.976", "NTSC")])
async def test_dvdrip_uses_dvd_standard_from_mediainfo(tmp_path, framerate, expected_source):
    directory = tmp_path / "tmp" / "case"
    directory.mkdir(parents=True)
    mi = {"media": {"track": [{"@type": "General"}, {"@type": "Video", "FrameRate": framerate}]}}
    (directory / "MediaInfo.json").write_text(json.dumps(mi), encoding="utf-8")
    filename = "Example.Video.DVDRip.avi"
    meta = Meta(category="MOVIE", resolution="480p")

    release_type = await VideoManager().get_type(filename, False, "", meta)

    assert await get_source(release_type, filename, filename, "", meta, "case", str(tmp_path)) == (expected_source, "DVDRIP")


@pytest.mark.asyncio
@pytest.mark.parametrize("filename,expected_details", [
    ("Example.Series.S01E02.avi", "480p AAC 2.0 XviD"),
    ("Example.Series.S01E02.DVDRip.XviD-GROUP.avi", "DVD DVDRip AAC 2.0 XviD"),
])
async def test_sd_titles_preserve_episode_details(tmp_path, filename, expected_details):
    meta = Meta(
        category="TV", title="Example Series", year=1996, search_year="1996",
        resolution="480p", season="S01", episode="E02", manual_episode_title="Example Episode",
        video_encode="XviD", audio="AAC 2.0", uhd="", tag="-GROUP",
    )
    meta.type = await VideoManager().get_type(filename, False, "", meta)
    meta.source, meta.type = await get_source(meta.type, filename, filename, "", meta, "case", str(tmp_path))

    _, name, _, _ = await NameManager({}).get_name(meta)

    assert name == f"Example Series 1996 S01E02 Example Episode {expected_details}-GROUP"


@pytest.mark.asyncio
async def test_release_group_named_vhs_does_not_override_remux_source(tmp_path):
    filename = "Example Movie 1995 (Alternate Title) 1080p Remux AVC FLAC 2.0-Example VHS Group.mkv"
    meta = Meta(category="MOVIE", uuid=filename)

    source, release_type = await get_source("REMUX", filename, filename, "", meta, "case", str(tmp_path))

    assert source == "BluRay"
    assert release_type == "REMUX"

    name_meta = Meta(
        category="MOVIE",
        type=release_type,
        source=source,
        title="Example Movie",
        aka="AKA Alternate Title",
        year=1995,
        resolution="1080p",
        uhd="",
        video_codec="AVC",
        audio="FLAC 2.0",
        tag="-Example VHS Group",
    )
    _name_notag, name, _clean_name, _potential_missing = await NameManager({}).get_name(name_meta)

    assert name == "Example Movie AKA Alternate Title 1995 1080p BluRay REMUX AVC FLAC 2.0-Example VHS Group"


@pytest.mark.asyncio
async def test_vhs_source_before_a_distinct_release_group_is_preserved(tmp_path):
    filename = "Movie.1995.1080p.VHS.Remux.AVC.FLAC.2.0-GROUP.mkv"
    meta = Meta(category="MOVIE", uuid=filename)

    source, release_type = await get_source("REMUX", filename, filename, "", meta, "case", str(tmp_path))

    assert source == "VHS"
    assert release_type == "REMUX"


@pytest.mark.asyncio
async def test_vhs_title_token_alongside_the_real_source_resolves_to_the_real_source(tmp_path):
    filename = "VHS.Example.Movie.1996.1080p.BluRay.x264-GROUP.mkv"
    meta = Meta(category="MOVIE", uuid=filename)

    source, release_type = await get_source("ENCODE", filename, filename, "", meta, "case", str(tmp_path))

    assert source == "BluRay"
    assert release_type == "ENCODE"
