# ruff: noqa: S101
import asyncio

import pytest

from src.meta import Meta
from src.trackers.UNIT3D.aither import Aither


def _name(meta: Meta) -> str:
    config = {"DEFAULT": {}, "TRACKERS": {"AITHER": {}}}
    return asyncio.run(Aither(config).get_name(meta))["name"]


def test_aither_preserves_space_before_aka_when_tv_year_is_omitted() -> None:
    meta = Meta(
        category="TV",
        year=2024,
        search_year="",
        name="Example Show AKA Alternate Show Title S17 1080p WEB-DL",
        aka="AKA Alternate Show Title",
        language_checked=True,
    )

    assert _name(meta) == "Example Show AKA Alternate Show Title S17 1080p WEB-DL"


def test_aither_moves_aka_before_a_present_year() -> None:
    meta = Meta(
        category="MOVIE",
        year=2024,
        name="Example Movie 2024 AKA Alternate Movie Title 1080p Blu-ray",
        aka="AKA Alternate Movie Title",
        language_checked=True,
    )

    assert _name(meta) == "Example Movie AKA Alternate Movie Title 2024 1080p Blu-ray"


@pytest.mark.parametrize("trump_reason", ["exact_match", "trumpable_release", None])
@pytest.mark.parametrize("same_title", [True, False])
def test_aither_trump_suffix_depends_on_torrent_title(trump_reason, same_title) -> None:
    title = "Example Movie AKA Alternate Movie Title 2024 1080p Blu-ray"
    meta = Meta(
        category="MOVIE",
        year=2024,
        name="Example Movie 2024 AKA Alternate Movie Title 1080p Blu-ray",
        aka="AKA Alternate Movie Title",
        language_checked=True,
        were_trumping=True,
        trump_reason=trump_reason,
        initial_dupes={"AITHER": [{"name": title if same_title else title + "-OtherGroup"}]},
    )

    assert _name(meta) == title + (" - TRUMP" if same_title else "")
    assert meta.name == "Example Movie 2024 AKA Alternate Movie Title 1080p Blu-ray"


@pytest.mark.parametrize(
    ("were_trumping", "initial_dupes", "suffix"),
    [
        (True, {"AITHER": [{"name": " example movie 2024 1080p web-dl "}]}, " - TRUMP"),
        (True, {"AITHER": ["Example Movie 2024 1080p WEB-DL"]}, " - TRUMP"),
        (False, {"AITHER": [{"name": "Example Movie 2024 1080p WEB-DL"}]}, ""),
        (True, {"LST": [{"name": "Example Movie 2024 1080p WEB-DL"}]}, ""),
        (True, {}, ""),
    ],
)
def test_aither_title_collision_only_affects_trump_uploads_on_aither(were_trumping, initial_dupes, suffix) -> None:
    title = "Example Movie 2024 1080p WEB-DL"
    meta = Meta(name=title, language_checked=True, were_trumping=were_trumping, initial_dupes=initial_dupes)

    assert _name(meta) == title + suffix
