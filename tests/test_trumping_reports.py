# ruff: noqa: S101

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.dupe_checking import DupeChecker
from src.meta import Meta
from src.trackers.UNIT3D.lst import LST
from src.trackersetup import TrackerSetup


@pytest.fixture
def report_setup(monkeypatch):
    setup = TrackerSetup({"TRACKERS": {"AITHER": {"api_key": "test-key"}}})
    monkeypatch.setattr(
        setup,
        "_create_tracker_instance",
        lambda _tracker: SimpleNamespace(trumping_url="https://example.com/api/trumping-reports/filter"),
    )
    monkeypatch.setattr(setup, "get_tracker_trumps", AsyncMock(return_value=([], 200)))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = SimpleNamespace(status_code=201)
    monkeypatch.setattr("src.trackersetup.httpx.AsyncClient", Mock(return_value=client))
    meta = Meta(ua_name="Upload Assistant", trump_reason="trumpable_release", tracker_status={"AITHER": {"torrent_id": 456}})
    meta["AITHER_trumpable_id"] = 123
    return setup, meta, client.post


@pytest.mark.parametrize("comparison", ["n", " N ", "d", "L"])
@pytest.mark.asyncio
async def test_aither_custom_reason_with_optional_comparisons(monkeypatch, report_setup, comparison):
    setup, meta, post = report_setup
    answers = [comparison]
    if comparison == "L":
        answers.extend([" https://example.com/old.png, https://example.com/old2.png ", "https://example.com/new.png"])
    elif comparison.strip().lower() == "n":
        # Choosing no comparisons must also discard evidence from an earlier run.
        meta.screenshots_in_description = True
        meta.screenshots_reported_torrent = ["https://example.com/stale-old.png"]
        meta.screenshots_trumping_torrent = ["https://example.com/stale-new.png"]
    answers.append("  Includes the missing original audio track.  ")
    prompt = Mock(side_effect=answers)
    monkeypatch.setattr("src.trackersetup.cli_ui.ask_string", prompt)

    assert await setup.process_trumpables(meta, "AITHER")
    assert "'n'" in prompt.call_args_list[0].args[0]
    assert await setup.make_trumpable_report(meta, "AITHER")

    assert prompt.call_args.kwargs["default"] == "Upload Assistant trumpable release trump"
    post.assert_awaited_once()
    assert post.call_args.kwargs["url"] == "https://example.com/api/trumping-reports/create"
    payload = post.call_args.kwargs["json"]
    assert payload["reported_torrent_id"] == "123"
    assert payload["trumping_torrent_id"] == 456
    message = "Includes the missing original audio track."
    if comparison == "d":
        message += " - User says comparison screenshots are in description."
    assert payload["message"] == message
    if comparison == "L":
        assert payload["screenshots_reported_torrent"] == "https://example.com/old.png,https://example.com/old2.png"
        assert payload["screenshots_trumping_torrent"] == "https://example.com/new.png"
    else:
        assert "screenshots_reported_torrent" not in payload
        assert "screenshots_trumping_torrent" not in payload


@pytest.mark.parametrize("answer", ["", "   ", None, EOFError(), KeyboardInterrupt()])
@pytest.mark.asyncio
async def test_aither_empty_or_cancelled_reason_keeps_automatic_message(monkeypatch, report_setup, answer):
    setup, meta, post = report_setup
    prompt = Mock(side_effect=["n", answer])
    monkeypatch.setattr("src.trackersetup.cli_ui.ask_string", prompt)

    assert await setup.process_trumpables(meta, "AITHER")
    assert await setup.make_trumpable_report(meta, "AITHER")

    assert post.call_args.kwargs["json"]["message"] == "Upload Assistant trumpable release trump"


@pytest.mark.parametrize("answer", ["", "invalid", None, EOFError(), KeyboardInterrupt()])
@pytest.mark.asyncio
async def test_aither_skipping_comparison_prompt_still_skips_report(monkeypatch, report_setup, answer):
    setup, meta, post = report_setup
    monkeypatch.setattr("src.trackersetup.cli_ui.ask_string", Mock(side_effect=[answer]))

    assert not await setup.process_trumpables(meta, "AITHER")

    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_aither_season_pack_accepts_custom_reason(monkeypatch, report_setup):
    setup, meta, post = report_setup
    meta.tv_pack = True
    meta["AITHER_matched_episode_ids"] = [{"id": 123, "is_episode": True}]
    prompt = Mock(return_value="Replaces the individual episodes.")
    monkeypatch.setattr("src.trackersetup.cli_ui.ask_string", prompt)

    assert await setup.process_trumpables(meta, "AITHER")
    prompt.assert_not_called()
    assert await setup.make_trumpable_report(meta, "AITHER")

    prompt.assert_called_once()
    assert prompt.call_args.kwargs["default"] == "Upload Assistant season pack trump"
    assert post.call_args.kwargs["json"]["message"] == "Replaces the individual episodes."


@pytest.mark.parametrize("tv_pack", [False, True])
@pytest.mark.parametrize("trump_reason", ["exact_match", "trumpable_release", None])
@pytest.mark.asyncio
async def test_aither_accepting_displayed_reason_does_not_duplicate_it(monkeypatch, report_setup, trump_reason, tv_pack):
    setup, meta, post = report_setup
    meta.trump_reason = trump_reason
    meta.tv_pack = tv_pack
    meta["AITHER_reported_torrent_id"] = 123
    prompt = Mock(side_effect=lambda _question, default: default)
    monkeypatch.setattr("src.trackersetup.cli_ui.ask_string", prompt)

    assert await setup.make_trumpable_report(meta, "AITHER")

    displayed_reason = prompt.call_args.kwargs["default"]
    assert displayed_reason
    assert "season pack trump" not in displayed_reason
    assert post.call_args.kwargs["json"]["message"] == displayed_reason


@pytest.mark.parametrize(
    ("reported_id", "trump_reason", "expected_reason"),
    [
        (123, "exact_match", "Upload Assistant exact filename trump"),
        (123, "trumpable_release", "Upload Assistant trumpable release trump"),
        (789, "trumpable_release", "Upload Assistant season pack trump"),
        ("789", "exact_match", "Upload Assistant season pack trump"),
    ],
)
@pytest.mark.asyncio
async def test_aither_season_pack_reason_depends_on_reported_torrent(monkeypatch, report_setup, reported_id, trump_reason, expected_reason):
    setup, meta, post = report_setup
    meta.update({"category": "TV", "tv_pack": True, "season": "S01", "episode": "", "source": "Web", "type": "WEBDL", "resolution": "1080p"})
    candidates = [
        {"id": 123, "name": "Example.Show.S01.1080p.WEB-DL-GROUP", "type": "WEB-DL", "res": "1080p", "link": "https://example.com/torrents/123"},
        {"id": 789, "name": "Example.Show.S01E01.1080p.WEB-DL-GROUP", "type": "WEB-DL", "res": "1080p", "link": "https://example.com/torrents/789"},
    ]

    await DupeChecker(setup.config).filter_dupes(candidates, meta, "AITHER")

    targets = {entry["id"]: entry for entry in meta["AITHER_matched_episode_ids"]}
    assert targets[123]["is_episode"] is False
    assert targets[789]["is_episode"] is True
    meta["AITHER_trumpable_id"] = reported_id
    meta.trump_reason = trump_reason
    prompt = Mock(side_effect=lambda _question, default: default)
    monkeypatch.setattr("src.trackersetup.cli_ui.ask_string", prompt)

    assert await setup.process_trumpables(meta, "AITHER")
    assert await setup.make_trumpable_report(meta, "AITHER")

    assert prompt.call_args.kwargs["default"] == expected_reason
    assert post.call_args.kwargs["json"]["message"] == expected_reason


@pytest.mark.parametrize("tv_pack", [False, True])
@pytest.mark.parametrize("answer", ["  Includes the missing original audio track.  ", "", "   ", None, EOFError(), KeyboardInterrupt()])
@pytest.mark.asyncio
async def test_lst_uses_shared_reason_prompt(monkeypatch, report_setup, tv_pack, answer):
    setup, meta, post = report_setup
    setup.config["TRACKERS"]["LST"] = {"api_key": "test-key"}
    meta["LST_trumpable_id"] = 123
    meta.tracker_status["LST"] = {"torrent_id": 456}
    meta.tv_pack = tv_pack
    meta["LST_matched_episode_ids"] = [{"id": 123, "is_episode": True}]
    prompt = Mock(side_effect=[answer])
    monkeypatch.setattr("src.trackersetup.cli_ui.ask_string", prompt)

    assert await setup.process_trumpables(meta, "LST")
    prompt.assert_not_called()
    assert await setup.make_trumpable_report(meta, "LST")

    default_reason = "Upload Assistant season pack trump" if tv_pack else "Upload Assistant trumpable release trump"
    prompt.assert_called_once_with(
        "Reason for the trump report on LST (press Enter to keep it, or type a replacement):",
        default=default_reason,
    )
    expected_reason = answer.strip() if isinstance(answer, str) and answer.strip() else default_reason
    assert post.call_args.kwargs["json"] == {
        "message": f"{expected_reason}: https://lst.gg/torrents/456",
    }


@pytest.mark.parametrize("is_disc", ["", "BDMV"])
@pytest.mark.parametrize("answer", ["", "Includes the missing subtitles."])
@pytest.mark.asyncio
async def test_lst_api_reason_is_displayed_and_can_be_replaced(monkeypatch, report_setup, is_disc, answer):
    setup, meta, post = report_setup
    setup.config["TRACKERS"]["LST"] = {"api_key": "test-key"}
    tracker = LST(setup.config)
    monkeypatch.setattr(setup, "_create_tracker_instance", lambda _tracker: tracker)
    response = Mock(status_code=200)
    response.json.return_value = {
        "data": [
            {"id": 999, "attributes": {"name": "Other.Show.S01", "trumpable": True, "trump_reason": "Unrelated reason"}},
            {"id": 123, "attributes": {"name": "Example.Show.S01", "trumpable": True, "trump_reason": "  Missing subtitles  "}},
        ],
    }
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response
    client.post = post
    monkeypatch.setattr("src.trackersetup.httpx.AsyncClient", Mock(return_value=client))
    meta.update({"category": "TV", "tmdb": 12, "season": "S01", "tv_pack": True, "resolution": "1080p", "type": "WEBDL", "is_disc": is_disc})
    # TrackerStatus retains the original API search results before filtering.
    meta.initial_dupes["LST"] = await tracker.search_existing(meta)
    meta["LST_trumpable_id"] = "123"
    meta.tracker_status["LST"] = {"torrent_id": 456}
    meta.trump_reason = "exact_match"
    prompt = Mock(side_effect=lambda _question, default: answer or default)
    monkeypatch.setattr("src.trackersetup.cli_ui.ask_string", prompt)

    assert await setup.process_trumpables(meta, "LST")
    assert await setup.make_trumpable_report(meta, "LST")

    assert prompt.call_args.kwargs["default"] == "Missing subtitles"
    assert post.call_args.kwargs["url"] == "https://lst.gg/api/reports/torrents/123/trump"
    assert post.call_args.kwargs["json"] == {"message": f"{answer or 'Missing subtitles'}: https://lst.gg/torrents/456"}
    assert meta.trump_reason == "exact_match"


@pytest.mark.parametrize(
    "candidate",
    [
        {"id": 123, "trumpable": True},
        {"id": 123, "trumpable": True, "trump_reason": None},
        {"id": 123, "trumpable": True, "trump_reason": "   "},
        {"id": 123, "trumpable": True, "trump_reason": 42},
        {"id": 999, "trumpable": True, "trump_reason": "Unrelated reason"},
        {"id": 123, "trumpable": False, "trump_reason": "Outdated reason"},
        "Example.Show.S01",
    ],
)
@pytest.mark.asyncio
async def test_lst_falls_back_without_a_reason_for_the_reported_trumpable(monkeypatch, report_setup, candidate):
    setup, meta, post = report_setup
    setup.config["TRACKERS"]["LST"] = {"api_key": "test-key"}
    meta["LST_reported_torrent_id"] = "123"
    meta.tracker_status["LST"] = {"torrent_id": 456}
    meta.initial_dupes = {
        "LST": [candidate],
        "AITHER": [{"id": 123, "trumpable": True, "trump_reason": "Reason from another tracker"}],
    }
    prompt = Mock(side_effect=lambda _question, default: default)
    monkeypatch.setattr("src.trackersetup.cli_ui.ask_string", prompt)

    assert await setup.make_trumpable_report(meta, "LST")

    assert prompt.call_args.kwargs["default"] == "Upload Assistant trumpable release trump"
    assert post.call_args.kwargs["json"]["message"] == "Upload Assistant trumpable release trump: https://lst.gg/torrents/456"
