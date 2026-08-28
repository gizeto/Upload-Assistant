# ruff: noqa: S101
import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

from rich.console import Console

import src.console as console_module
from src.console import ansi_to_html, buffer_console_logs, prompt_in_thread


def test_ansi_to_html_preserves_osc8_hyperlinks() -> None:
    stream = StringIO()
    Console(file=stream, force_terminal=True, color_system="truecolor", legacy_windows=False).print("[link=https://example.test]link[/link]")

    html = ansi_to_html(stream.getvalue())

    assert '<a href="https://example.test">link</a>' in html


def test_prompt_in_thread_returns_prompt_result() -> None:
    async def ask() -> str:
        return await prompt_in_thread(lambda prefix, value: f"{prefix}{value}", "answer-", 42)

    assert asyncio.run(ask()) == "answer-42"


def test_safe_input_restores_terminal_without_flushing(monkeypatch) -> None:
    class InteractiveInput(StringIO):
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return 42

    terminal_settings = [1, 2, 3, 4, 5, 6, [b"a", b"b"]]
    tcsetattr = Mock()
    fake_termios = SimpleNamespace(TCSANOW=0, tcsetattr=tcsetattr)
    stdin = InteractiveInput("yes\n")
    stdout = StringIO()

    monkeypatch.setattr(console_module, "termios", fake_termios)
    monkeypatch.setattr(console_module, "_initial_terminal_settings", terminal_settings)
    monkeypatch.setattr(console_module.sys, "stdin", stdin)
    monkeypatch.setattr(console_module.sys, "stdout", stdout)

    assert console_module._safe_input("> ") == "yes"
    tcsetattr.assert_called_once_with(42, fake_termios.TCSANOW, terminal_settings)
    assert stdout.getvalue() == "> "


def test_buffer_console_logs_can_contend_across_consecutive_event_loops() -> None:
    async def contend_for_buffer() -> None:
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        order: list[str] = []

        async def first_user() -> None:
            async with buffer_console_logs():
                order.append("first-entered")
                first_entered.set()
                await release_first.wait()
                order.append("first-leaving")

        async def second_user() -> None:
            await first_entered.wait()
            second_started.set()
            async with buffer_console_logs():
                order.append("second-entered")

        first_task = asyncio.create_task(first_user())
        second_task = asyncio.create_task(second_user())
        await second_started.wait()
        await asyncio.sleep(0)
        release_first.set()
        await asyncio.gather(first_task, second_task)

        assert order == ["first-entered", "first-leaving", "second-entered"]

    asyncio.run(contend_for_buffer())
    asyncio.run(contend_for_buffer())
