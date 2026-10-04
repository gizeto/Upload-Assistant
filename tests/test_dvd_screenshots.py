# ruff: noqa: S101

import asyncio
import shutil
import subprocess
import sys

import pytest
from PIL import Image

from src import takescreens
from src.meta import Meta
from src.takescreens import discard_smallest_capture_result, dvd_screenshot_has_content


def test_discard_smallest_capture_result_only_removes_current_batch(tmp_path) -> None:
    existing = tmp_path / "disc-0.png"
    captured_large = tmp_path / "disc-1.png"
    captured_small = tmp_path / "disc-2.png"
    existing.write_bytes(b"x")
    captured_large.write_bytes(b"x" * 30)
    captured_small.write_bytes(b"x" * 20)
    capture_results = [str(captured_large), str(captured_small)]

    removed = discard_smallest_capture_result(capture_results)

    assert removed == str(captured_small)
    assert existing.exists()
    assert captured_large.exists()
    assert not captured_small.exists()
    assert capture_results == [str(captured_large)]


def test_dvd_content_check_requires_size_and_visible_pixels(tmp_path) -> None:
    visible = tmp_path / "visible.png"
    blank = tmp_path / "blank.png"
    small_visible = tmp_path / "small-visible.png"
    Image.effect_noise((854, 480), 20).save(visible)
    Image.new("L", (854, 480), 0).save(blank, compress_level=0)
    Image.linear_gradient("L").resize((854, 480)).save(small_visible)

    assert visible.stat().st_size >= 20 * 1024
    assert blank.stat().st_size >= 20 * 1024
    assert small_visible.stat().st_size < 20 * 1024
    assert dvd_screenshot_has_content(visible)
    assert not dvd_screenshot_has_content(blank)
    assert not dvd_screenshot_has_content(small_visible)


@pytest.mark.asyncio
@pytest.mark.parametrize("dvd_title", [None, 1])
async def test_dvd_capture_timeout_reaps_owned_processes_and_removes_partial_image(tmp_path, monkeypatch, dvd_title):
    output = tmp_path / "partial.png"
    owned_processes = []
    create_subprocess_exec = asyncio.create_subprocess_exec
    unrelated = await create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)")

    async def capture_process(*args, **kwargs):
        # A timed-out title attempt must be fully stopped before VOB fallback.
        assert all(process.returncode is not None for process in owned_processes)
        process = await create_subprocess_exec(*args, **kwargs)
        owned_processes.append(process)
        return process

    monkeypatch.setattr(takescreens.asyncio, "create_subprocess_exec", capture_process)
    monkeypatch.setattr(takescreens.platform, "system", lambda: "Windows")
    monkeypatch.setattr(takescreens, "default_config", {})
    monkeypatch.setattr(takescreens, "_positive_config_int", lambda *_args: 0.2)
    monkeypatch.setattr(takescreens, "overlay_filters", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        takescreens,
        "compile_ffmpeg_command",
        lambda _command: [
            sys.executable, "-c",
            "import pathlib, sys, time; pathlib.Path(sys.argv[1]).write_bytes(b'partial'); time.sleep(60)",
            str(output),
        ],
    )
    try:
        result = await takescreens.capture_dvd_screenshot((0, "concat:first.vob|second.vob", str(output), "2207.88", Meta(), 720, 576, 1, 1, dvd_title))

        assert result == (0, None)
        assert len(owned_processes) == (1 if dvd_title is None else 2)
        assert all(process.returncode is not None for process in owned_processes)
        assert not output.exists()
        assert unrelated.returncode is None
    finally:
        for process in [*owned_processes, unrelated]:
            if process.returncode is None:
                process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_dvd_capture_reports_slow_decoding_and_completes(tmp_path, monkeypatch):
    output = tmp_path / "frame.png"
    messages = []
    status_reported = asyncio.Event()

    async def slow_capture(_command):
        await status_reported.wait()
        output.write_bytes(b"png")
        return 0, b"", b""

    def report(message):
        messages.append(message)
        status_reported.set()

    monkeypatch.setattr(takescreens, "run_ffmpeg", slow_capture)
    monkeypatch.setattr(takescreens, "DVD_CAPTURE_STATUS_INTERVAL", 0.01)
    monkeypatch.setattr(takescreens.logger, "info", report)
    monkeypatch.setattr(takescreens, "default_config", {"dvd_screenshot_timeout": "1"})

    assert await takescreens.run_dvd_capture(object(), str(output), 2207.88) == (0, b"", b"")
    assert output.exists()
    assert any("Still capturing DVD screenshot frame.png at 2207.88s" in message and "1s limit" in message for message in messages)


@pytest.mark.asyncio
async def test_cancelling_dvd_capture_awaits_worker_cleanup(monkeypatch):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def capture(_command):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(takescreens, "run_ffmpeg", capture)
    task = asyncio.create_task(takescreens.run_dvd_capture(object(), "frame.png", 100))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_dvd_title_without_image_uses_vob_fallback(tmp_path, monkeypatch):
    output = tmp_path / "frame.png"
    commands = []

    async def capture(command):
        commands.append(takescreens.compile_ffmpeg_command(command))
        if len(commands) == 2:
            output.write_bytes(b"png")
        return 0, b"", b""

    monkeypatch.setattr(takescreens, "run_ffmpeg", capture)
    monkeypatch.setattr(takescreens, "overlay_filters", lambda *_args, **_kwargs: [])
    result = await takescreens.capture_dvd_screenshot((0, "concat:first.vob|second.vob", str(output), "100", Meta(), 720, 576, 1, 1, 1))

    assert result == (0, str(output))
    assert len(commands) == 2
    assert "dvdvideo" in commands[0]
    assert "dvdvideo" not in commands[1]
    assert "trim=start=100.0:duration=1" in commands[1][commands[1].index("-filter_complex") + 1]
    assert commands[1][commands[1].index("-loglevel") + 1] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_timestamps", [False, True])
async def test_real_vob_batch_matches_sequential_seek_frames(tmp_path, monkeypatch, reset_timestamps):
    binary = shutil.which("ffmpeg")
    if binary is None:
        pytest.skip("FFmpeg is not installed")
    monkeypatch.setattr(takescreens, "default_config", {"ffmpeg_path": binary})
    monkeypatch.setattr(takescreens, "overlay_filters", lambda *_args, **_kwargs: [])
    inputs = []
    for index in range(2 if reset_timestamps else 1):
        source = tmp_path / f"source-{index}.VOB"
        await asyncio.to_thread(
            subprocess.run,
            [binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25",
             "-t", "20" if reset_timestamps else "40", "-c:v", "mpeg2video", "-g", "12", "-f", "vob", str(source)],
            check=True, timeout=30,
        )
        inputs.append(source)
    if not reset_timestamps:
        payload = inputs[0].read_bytes()
        cut = len(payload) // 2 // 2048 * 2048
        inputs = [tmp_path / "VTS_01_1.VOB", tmp_path / "VTS_01_2.VOB"]
        inputs[0].write_bytes(payload[:cut])
        inputs[1].write_bytes(payload[cut:])
    source_url = "concat:" + "|".join(str(path) for path in inputs)
    tasks = [(index, source_url, str(tmp_path / f"batch-{index}.png"), str(timestamp), Meta(), 160, 120, 1.2, 1)
             for index, timestamp in enumerate([1.2, 12.7, 25.2, 35.7])]
    launched = []
    create_subprocess_exec = asyncio.create_subprocess_exec

    async def capture_process(*args, **kwargs):
        launched.append(args)
        return await create_subprocess_exec(*args, **kwargs)

    monkeypatch.setattr(takescreens.asyncio, "create_subprocess_exec", capture_process)
    results = await takescreens.capture_dvd_batch(tasks)

    assert len(launched) == 1, "VOB screenshots must share a single FFmpeg decoder"
    assert launched[0].count("-i") == 1
    assert results == [(task[0], task[2]) for task in tasks]
    for task in tasks:
        reference = tmp_path / f"reference-{task[0]}.png"
        await asyncio.to_thread(
            subprocess.run,
            [binary, "-hide_banner", "-loglevel", "error", "-i", source_url, "-ss", task[3], "-vf", "scale=192:120",
             "-vframes", "1", "-threads", "1", "-update", "1", str(reference)],
            check=True, timeout=30,
        )
        with Image.open(task[2]) as captured, Image.open(reference) as expected:
            assert captured.size == expected.size == (192, 120)
            assert captured.tobytes() == expected.tobytes(), f"batch selected a different frame at {task[3]}s"


@pytest.mark.asyncio
async def test_failed_dvd_batch_keeps_completed_images_and_removes_partial_images(tmp_path, monkeypatch):
    completed = tmp_path / "completed.png"
    partial = tmp_path / "partial.png"
    untouched = tmp_path / "existing.png"
    untouched.write_bytes(b"existing image")

    async def capture(_command):
        Image.effect_noise((720, 576), 20).save(completed)
        partial.write_bytes(b"partial")
        return -1, b"", b"Timeout"

    monkeypatch.setattr(takescreens, "run_ffmpeg", capture)
    monkeypatch.setattr(takescreens, "overlay_filters", lambda *_args, **_kwargs: [])
    tasks = [(index, "concat:first.vob|second.vob", str(image), str(timestamp), Meta(), 720, 576, 1, 1)
             for index, (image, timestamp) in enumerate([(completed, 100), (partial, 200)])]

    assert await takescreens.capture_dvd_batch(tasks) == [(0, str(completed)), (1, None)]
    assert completed.exists()
    assert not partial.exists()
    assert untouched.read_bytes() == b"existing image"
