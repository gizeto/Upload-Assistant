import pytest

from src.args import Args
from src.get_name import NameManager
from src.get_source import get_source
from src.meta import Meta
from src.video import VideoManager


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["SDTV", "TVRip", "VHSRip"])
@pytest.mark.parametrize("manual", [False, True])
async def test_movie_cli_generates_capture_title(tmp_path, source, manual):
    filename = "Example.Movie.1996.AAC.AVC-GROUP.mkv" if manual else f"Example.Movie.1996.{source.lower()}.AAC.AVC-GROUP.mkv"
    path = tmp_path / filename
    overrides = ["--type", "other", "--source", source] if manual else []
    meta, _, _ = Args({"DEFAULT": {"screens": 1}}).parse(
        [str(path), "--category", "movie", *overrides],
        Meta(category="MOVIE", title="Example Movie", year=1996, resolution="480p", uhd="", audio="AAC 2.0", tag="-GROUP"),
    )
    meta.mediainfo = {"media": {"track": [{"@type": "General"}, {"@type": "Video", "Format": "AVC"}]}}
    video_manager = VideoManager()
    meta.type = await video_manager.get_type(str(path), False, "", meta)
    meta.source, meta.type = await get_source(meta.type, str(path), str(path), "", meta, "case", str(tmp_path))
    meta.video_encode, meta.video_codec, _, _ = await video_manager.get_video_encode(meta.mediainfo, meta.type, {})

    name_notag, name, clean_name, _ = await NameManager({}).get_name(meta)

    assert meta.type == "OTHER"
    assert meta.source == source
    assert name_notag == f"Example Movie 1996 480p {source} AAC 2.0 x264"
    assert name == f"Example Movie 1996 480p {source} AAC 2.0 x264-GROUP"
    assert clean_name == name


@pytest.mark.asyncio
@pytest.mark.parametrize("filename,category,anime", [
    ("[GROUP] Example Anime Title (VHS) [A1B2C3D4].mkv", "MOVIE", False),
    ("[GROUP] Example Anime Title (VHS) [A1B2C3D4].mkv", "MOVIE", True),
    ("Example.Series.S01E01.(Example).The.Making.of.an.Example.Film.1974.VHSRip.AAC2.0.x264-GROUP.mp4", "TV", False),
    ("Example.Movie.1946.VHSRIP.x264.m4v", "MOVIE", False),
])
async def test_vhsrip_release_patterns_detect_type_and_source(tmp_path, filename, category, anime):
    meta = Meta(category=category, anime=anime)

    release_type = await VideoManager().get_type(filename, False, "", meta)

    assert release_type == "OTHER"
    assert await get_source(release_type, filename, filename, "", meta, "case", str(tmp_path)) == ("VHSRip", "OTHER")


@pytest.mark.asyncio
@pytest.mark.parametrize("filename,expected", [
    ("Example.Movie.1996.HDTVRip.AAC.AVC-GROUP.mkv", "HDTV"),
    ("Example.Movie.1996.1080p.BluRay.x264-GROUP.mkv", "ENCODE"),
    ("Example.Movie.1996.1080p.WEB-DL.AAC.H.264-GROUP.mkv", "WEBDL"),
    ("Example.Movie.1996.DVDRip.x264-GROUP.mkv", "DVDRIP"),
    ("Example.Movie.1996.1080p.BluRay.x264-Example TVRip Group.mkv", "ENCODE"),
    ("Example.Movie.1996.1080p.BluRay.x264-Example VHSRip Group.mkv", "ENCODE"),
    ("Example.Movie.1996.1080p.BluRay.x264-Example SDTV Group.mkv", "ENCODE"),
    ("TVRip.Movie.1996.1080p.BluRay.x264-GROUP.mkv", "ENCODE"),
    ("VHSRip.Movie.1996.1080p.BluRay.x264-GROUP.mkv", "ENCODE"),
    ("SDTV.Movie.1996.1080p.BluRay.x264-GROUP.mkv", "ENCODE"),
    ("/TVRip/Example.Movie.1996.1080p.BluRay.x264-GROUP.mkv", "ENCODE"),
    ("/TVRip/Example.Movie.1996.x264-GROUP.mkv", "ENCODE"),
    ("/VHSRip/Example.Movie.1996.x264-GROUP.mkv", "ENCODE"),
    ("/SDTV/Example.Movie.1996.x264-GROUP.mkv", "ENCODE"),
])
async def test_capture_type_detection_preserves_other_release_types(filename, expected):
    assert await VideoManager().get_type(filename, False, "", Meta(category="MOVIE")) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_source,expected_source,expected_type", [("BluRay", "BluRay", "ENCODE"), ("WEB", "Web", "WEBRIP")])
async def test_manual_source_overrides_capture_detection(tmp_path, manual_source, expected_source, expected_type):
    filename = "Example.Movie.1996.TVRip.x264-GROUP.mkv"
    meta = Meta(category="MOVIE", manual_source=manual_source)

    release_type = await VideoManager().get_type(filename, False, "", meta)
    source, release_type = await get_source(release_type, filename, filename, "", meta, "case", str(tmp_path))

    assert source == expected_source
    assert release_type == expected_type


@pytest.mark.asyncio
@pytest.mark.parametrize("sources,expected_source", [("VHS.TVRip", "TVRip"), ("TVRip.VHS", "VHSRip")])
async def test_multiple_capture_sources_use_last_source(tmp_path, sources, expected_source):
    filename = f"Example.Movie.1996.{sources}.x264-GROUP.mkv"
    meta = Meta(category="MOVIE")

    assert await get_source("OTHER", filename, filename, "", meta, "case", str(tmp_path)) == (expected_source, "OTHER")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["SDTV", "TVRip", "VHSRip"])
@pytest.mark.parametrize("prefix", ["", " "])
async def test_anime_group_does_not_supply_capture_source(tmp_path, source, prefix):
    filename = f"{prefix}[Example {source} Group] Example Anime Title (1996) [A1B2C3D4].mkv"
    meta = Meta(category="MOVIE", anime=True)

    release_type = await VideoManager().get_type(filename, False, "", meta)

    assert release_type == "ENCODE"
    assert await get_source(release_type, filename, filename, "", meta, "case", str(tmp_path)) == ("BluRay", "ENCODE")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["TVRip", "VHSRip"])
@pytest.mark.parametrize("folder_option", ["tv_pack", "keep_folder"])
async def test_capture_group_in_folder_does_not_supply_source(tmp_path, source, folder_option):
    directory = tmp_path / "Example.Series.S01-GROUP"
    directory.mkdir()
    video = directory / f"Example.Series.S01E01.x264-Example {source} Group.mkv"
    video.touch()
    meta = Meta(category="TV", path=str(directory), uuid=directory.name, **{folder_option: True})

    release_type = await VideoManager().get_type(str(video), False, "", meta)

    assert release_type == "ENCODE"
    assert await get_source(release_type, str(video), str(directory), "", meta, "case", str(tmp_path)) == ("BluRay", "ENCODE")


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["MOVIE", "TV"])
async def test_other_title_preserves_mpeg2_capture_codec(category):
    meta = Meta(
        category=category, title="Example Title", year=1996, search_year="1996",
        type="OTHER", source="SDTV", resolution="480i", season="S01", episode="E02", uhd="", audio="MP2 2.0", tag="-GROUP",
        mediainfo={"media": {"track": [{"@type": "General"}, {"@type": "Video", "Format": "MPEG Video", "Format_Version": "2"}]}},
    )
    meta.video_encode, meta.video_codec, _, _ = await VideoManager().get_video_encode(meta.mediainfo, meta.type, {})

    _, name, _, _ = await NameManager({}).get_name(meta)

    episode = " S01E02" if category == "TV" else ""
    assert name == f"Example Title 1996{episode} 480i SDTV MP2 2.0 MPEG-2-GROUP"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["SDTV", "TVRip", "VHSRip"])
async def test_capture_directory_supplies_source_for_generic_episode_files(tmp_path, source):
    directory = tmp_path / f"Example.Series.S01.{source}-GROUP"
    directory.mkdir()
    video = directory / "episode.mkv"
    video.touch()
    meta = Meta(category="TV", path=str(directory), isdir=True, tv_pack=True, uuid=directory.name)

    release_type = await VideoManager().get_type(str(video), False, "", meta)

    assert release_type == "OTHER"
    assert await get_source(release_type, str(video), str(directory), "", meta, "case", str(tmp_path)) == (source, "OTHER")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["SDTV", "TVRip", "VHSRip"])
async def test_capture_type_detection_respects_manual_type_and_discs(source):
    filename = f"Example.Movie.1996.{source}.AAC.AVC-GROUP.mkv"
    manager = VideoManager()

    assert await manager.get_type(filename, False, "", Meta(manual_type="ENCODE")) == "ENCODE"
    assert await manager.get_type(filename, False, "BDMV", Meta()) == "DISC"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["SDTV", "TVRip", "VHSRip"])
async def test_manual_source_detects_capture_type(source):
    filename = "Example.Movie.1996.mkv"
    meta, _, _ = Args({"DEFAULT": {"screens": 1}}).parse([filename, "--source", source], Meta())

    assert await VideoManager().get_type(filename, False, "", meta) == "OTHER"


@pytest.mark.asyncio
@pytest.mark.parametrize("release_type", ["OTHER", "ENCODE"])
@pytest.mark.parametrize("category", ["MOVIE", "TV"])
@pytest.mark.parametrize("source", ["TVRip", "", None])
async def test_other_and_encode_names_keep_metadata_and_encoder(category, release_type, source):
    meta = Meta(
        category=category,
        type=release_type,
        title="Example Title",
        year=1996,
        search_year="1996",
        season="S01",
        episode="E02",
        manual_episode_title="Example Episode",
        edition="Extended",
        repack="REPACK",
        resolution="576p",
        uhd="",
        source=source,
        audio="AAC 2.0",
        video_codec="HEVC",
        video_encode="x265",
        tag="-GROUP",
    )

    _, name, _, _ = await NameManager({}).get_name(meta)

    episode = " S01E02 Example Episode" if category == "TV" else ""
    source_label = f"{source} " if source else ""
    assert name == f"Example Title 1996{episode} Extended REPACK 576p {source_label}AAC 2.0 x265-GROUP"


@pytest.mark.asyncio
async def test_other_type_respects_exact_name_override(tmp_path):
    release_name = "Movie.Title.1996.TVRip.AAC.AVC-GROUP"
    path = tmp_path / f"{release_name}.mkv"
    meta, _, _ = Args({"DEFAULT": {"screens": 1}}).parse(
        [str(path), "--category", "movie", "--type", "other", "--name", release_name], Meta()
    )
    meta.type = "OTHER"
    meta.tag = "-GROUP"

    name_notag, name, clean_name, _ = await NameManager({}).get_name(meta)

    assert name_notag == release_name
    assert name == release_name
    assert clean_name == release_name
