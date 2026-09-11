# ruff: noqa: S101
from unittest.mock import AsyncMock

import pytest

from src.meta import Meta
from src.trackers.UNIT3D import UNIT3D
from src.trackers.UNIT3D.aither import Aither
from src.trackers.UNIT3D.reelflix import ReelFlix
from src.uphelper import UploadHelper


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("filename_match", [True, False])
@pytest.mark.parametrize("same_title", [True, False])
async def test_manual_trump_uses_final_title_after_dupe_approval(monkeypatch, approved, filename_match, same_title):
    config = {"DEFAULT": {}, "TRACKERS": {"REELFLIX": {}}}
    tracker = ReelFlix(config)
    helper = UploadHelper(config)
    prompt = AsyncMock(return_value=approved)
    monkeypatch.setattr(helper, "prompt_yes_no", prompt)
    title = "Example Movie 2024 1080p WEB-DL"
    final_title = title + "-NoGroup"
    dupes = [{"name": final_title if same_title else final_title + " Different Edition"}]
    meta = Meta(
        category="MOVIE",
        name=title,
        filename_match=filename_match,
        file_count_match=filename_match,
        initial_dupes={"REELFLIX": dupes},
    )

    blocked, meta = await helper.dupe_check(dupes, meta, "REELFLIX")

    assert blocked is not approved
    prompt.assert_awaited_once_with("Upload to REELFLIX anyway?", default=False)
    expected = final_title + (" - TRUMP" if approved and same_title else "")
    assert await tracker.get_upload_name(meta) == {"name": expected}
    assert meta.name == title
    assert not meta.were_trumping
    assert meta.trumping_trackers == []

    # The shared payload builder must use the same title as the preview.
    for getter in ("get_description", "get_mediainfo", "get_bdinfo"):
        monkeypatch.setattr(tracker, getter, AsyncMock(return_value={}))
    assert (await tracker.get_data(meta))["name"] == expected


@pytest.mark.asyncio
async def test_unit3d_dupe_approval_is_tracker_specific():
    config = {"DEFAULT": {}, "TRACKERS": {"FIRST": {}, "SECOND": {}}}
    meta = Meta(name="Example release", initial_dupes={"FIRST": ["Example release"], "SECOND": ["Example release"]})
    meta["FIRST_dupe_override"] = True

    assert await UNIT3D(config, "FIRST").get_upload_name(meta) == {"name": "Example release - TRUMP"}
    assert await UNIT3D(config, "SECOND").get_upload_name(meta) == {"name": "Example release"}


@pytest.mark.asyncio
async def test_aither_shared_title_processing_does_not_repeat_suffix():
    tracker = Aither({"DEFAULT": {}, "TRACKERS": {"AITHER": {}}})
    meta = Meta(
        name="Example release",
        language_checked=True,
        were_trumping=True,
        initial_dupes={"AITHER": ["Example release", "Example release - TRUMP"]},
    )

    assert await tracker.get_upload_name(meta) == {"name": "Example release - TRUMP"}
