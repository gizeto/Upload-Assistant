# ruff: noqa: S101, S603
import contextlib
import io
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest

import web_ui.server as server


def test_execute_missing_path_preserves_running_session_process(monkeypatch) -> None:
    class RunningProcess:
        def poll(self):
            return None

    process = RunningProcess()
    terminated_processes = []
    session_id = "replace-running-process-test"

    monkeypatch.setattr(server, "_is_authenticated", lambda: True)
    monkeypatch.setattr(server, "_verify_csrf_header", lambda: True)
    monkeypatch.setattr(server, "_terminate_process_tree", terminated_processes.append)
    with server.active_processes_lock:
        server.active_processes[session_id] = {"mode": "subprocess", "process": process}

    try:
        response = server.app.test_client().post("/api/execute", json={"path": "", "session_id": session_id})

        assert response.status_code == 400
        assert terminated_processes == []
        assert server.active_processes[session_id]["process"] is process
    finally:
        with server.active_processes_lock:
            server.active_processes.pop(session_id, None)


def test_kill_endpoint_stops_upload_controller_and_worker(monkeypatch) -> None:
    try:
        psutil.Process().children(recursive=True)
    except (psutil.AccessDenied, PermissionError) as exc:
        pytest.skip(f"Process discovery is not permitted: {exc}")

    child_code = "import time; time.sleep(60)"
    controller_code = f"import subprocess, sys, time; worker = subprocess.Popen([sys.executable, '-c', {child_code!r}]); print(worker.pid, flush=True); time.sleep(60)"
    controller = subprocess.Popen(
        [sys.executable, "-c", controller_code],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert controller.stdout is not None
    worker_pid = int(controller.stdout.readline().strip())

    try:
        terminate_process_tree = server._terminate_process_tree
        monkeypatch.setattr(server, "_terminate_process_tree", lambda process: terminate_process_tree(process, timeout=0.5))
        monkeypatch.setattr(server, "_get_bearer_from_header", lambda: None)
        monkeypatch.setattr(server, "_is_authenticated", lambda: True)
        monkeypatch.setattr(server, "_verify_csrf_header", lambda: True)
        monkeypatch.setattr(server, "_verify_same_origin", lambda: True)
        session_id = "kill-tree-test"
        with server.active_processes_lock:
            server.active_processes[session_id] = {"mode": "subprocess", "process": controller}

        response = server.app.test_client().post("/api/kill", json={"session_id": session_id})

        assert response.status_code == 200
        assert response.get_json() == {"success": True, "message": "Process terminated"}
        controller.wait(timeout=5)

        deadline = time.monotonic() + 5
        while psutil.pid_exists(worker_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not psutil.pid_exists(worker_pid)
    finally:
        with server.active_processes_lock:
            server.active_processes.pop("kill-tree-test", None)
        with contextlib.suppress(psutil.NoSuchProcess):
            psutil.Process(worker_pid).kill()
        if controller.poll() is None:
            controller.kill()
            controller.wait(timeout=5)
        controller.stdout.close()


def test_terminate_process_tree_stops_controller_and_worker(monkeypatch) -> None:
    process = Mock(pid=1234)
    process.poll.side_effect = [None, 0]
    root = Mock()
    worker = Mock()
    root.children.return_value = [worker]
    monkeypatch.setattr(server.psutil, "Process", Mock(return_value=root))
    monkeypatch.setattr(server.psutil, "wait_procs", Mock(return_value=([worker, root], [])))

    assert server._terminate_process_tree(process) is True
    worker.terminate.assert_called_once()
    root.terminate.assert_called_once()


@pytest.mark.parametrize(
    "discovery_error",
    [psutil.AccessDenied(pid=1234), PermissionError("Process discovery is not permitted"), OSError("Process discovery failed")],
    ids=["access-denied", "permission-error", "os-error"],
)
def test_terminate_process_tree_keeps_controller_when_discovery_fails(monkeypatch, discovery_error) -> None:
    process = Mock(pid=1234)
    process.poll.side_effect = [None, 0]
    root = Mock()
    root.children.side_effect = discovery_error
    monkeypatch.setattr(server.psutil, "Process", Mock(return_value=root))
    monkeypatch.setattr(server.psutil, "wait_procs", Mock(return_value=([], [])))

    assert server._terminate_process_tree(process) is False
    root.terminate.assert_not_called()
    process.wait.assert_not_called()


@pytest.mark.parametrize(
    "discovery_error",
    [psutil.AccessDenied(pid=1234), PermissionError("Process discovery is not permitted"), OSError("Process discovery failed")],
    ids=["access-denied", "permission-error", "os-error"],
)
def test_kill_preserves_session_io_and_can_retry_when_process_discovery_fails(monkeypatch, discovery_error) -> None:
    process = Mock(pid=1234, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    process.poll.return_value = None
    root = Mock()
    root.children.side_effect = discovery_error
    session_id = "failed-kill-test"

    monkeypatch.setattr(server, "_get_bearer_from_header", lambda: None)
    monkeypatch.setattr(server, "_is_authenticated", lambda: True)
    monkeypatch.setattr(server, "_verify_csrf_header", lambda: True)
    monkeypatch.setattr(server, "_verify_same_origin", lambda: True)
    monkeypatch.setattr(server.psutil, "Process", Mock(return_value=root))
    monkeypatch.setattr(server.psutil, "wait_procs", Mock(return_value=([root], [])))
    with server.active_processes_lock:
        server.active_processes[session_id] = {"mode": "subprocess", "process": process}

    try:
        client = server.app.test_client()
        response = client.post("/api/kill", json={"session_id": session_id})

        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to terminate process tree", "success": False}
        assert server.active_processes[session_id]["process"] is process
        root.terminate.assert_not_called()
        assert all(not stream.closed for stream in (process.stdin, process.stdout, process.stderr))

        input_response = client.post("/api/input", json={"session_id": session_id, "input": "yes"})
        assert input_response.status_code == 200
        assert process.stdin.getvalue() == "yes\n"

        root.children.side_effect = None
        root.children.return_value = []
        process.poll.side_effect = [None, 0]
        retry_response = client.post("/api/kill", json={"session_id": session_id})

        assert retry_response.status_code == 200
        assert retry_response.get_json() == {"success": True, "message": "Process terminated"}
        root.terminate.assert_called_once()
        assert session_id not in server.active_processes
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
    finally:
        with server.active_processes_lock:
            server.active_processes.pop(session_id, None)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def test_sse_disconnect_terminates_running_process_tree(tmp_path, monkeypatch) -> None:
    class RunningProcess:
        pid = 1
        stdin = io.StringIO()
        stdout = io.StringIO()
        stderr = io.StringIO()

        def poll(self):
            return None

    process = RunningProcess()
    terminate = Mock(return_value=True)
    session_id = "sse-disconnect-test"

    monkeypatch.setattr(server, "_is_authenticated", lambda: True)
    monkeypatch.setattr(server, "_verify_csrf_header", lambda: True)
    monkeypatch.setattr(server, "_validate_execution_path", lambda *_args, **_kwargs: str(tmp_path))
    monkeypatch.setattr(server, "_assert_safe_resolved_path", lambda _: None)
    monkeypatch.setattr(server, "_validate_upload_assistant_args", lambda args: args)
    monkeypatch.setattr(server, "_spawn_webui_upload_process", lambda *_args: (process, "subprocess"))

    monkeypatch.setattr(server, "_terminate_process_tree", terminate)

    response = server.app.test_client().post("/api/execute", json={"path": str(tmp_path), "session_id": session_id}, buffered=False)
    try:
        next(response.response)  # Initial system event, before process startup.
        next(response.response)  # Starts the controller, then emits a keepalive.
        response.close()

        terminate.assert_called_once_with(process)
        assert session_id not in server.active_processes
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
    finally:
        response.close()
        with server.active_processes_lock:
            server.active_processes.pop(session_id, None)


@pytest.fixture
def running_controller(tmp_path, monkeypatch):
    content = tmp_path / "Fictional.Release.mkv"
    content.touch()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    process = Mock(pid=1234, stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
    process.poll.return_value = None
    root = Mock()
    root.children.side_effect = psutil.AccessDenied(pid=1234)
    spawn = Mock(return_value=(process, "subprocess"))

    monkeypatch.setattr(server, "STATE_DIR", state_dir)
    monkeypatch.setattr(server, "active_processes", {})
    monkeypatch.setattr(server, "_generated_queues", {})
    monkeypatch.setattr(server, "_get_browse_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr(server, "_get_bearer_from_header", lambda: None)
    monkeypatch.setattr(server, "_is_authenticated", lambda: True)
    monkeypatch.setattr(server, "_verify_csrf_header", lambda: True)
    monkeypatch.setattr(server, "_verify_same_origin", lambda: True)
    monkeypatch.setattr(server.limiter, "enabled", False)
    monkeypatch.setattr(server.psutil, "Process", Mock(return_value=root))
    monkeypatch.setattr(server.psutil, "wait_procs", Mock(return_value=([root], [])))
    monkeypatch.setattr(server, "_spawn_webui_upload_process", spawn)

    yield server.app.test_client(), content, process, root, spawn

    for stream in (process.stdin, process.stdout, process.stderr):
        stream.close()


def test_queue_cleanup_can_retry_after_file_removal_fails(running_controller, monkeypatch):
    client, content, _, _, _ = running_controller
    queue_response = client.post("/api/save_queue", json={"items": [{"path": str(content)}]})
    queue_path = Path(queue_response.json["path"])
    state = server._make_process_state(str(queue_path), "")
    remove_queue = server._remove_generated_queue_file
    monkeypatch.setattr(server, "_remove_generated_queue_file", Mock(side_effect=PermissionError("Queue file is busy")))

    server._cleanup_execution_queue(state)

    assert queue_path.is_file()
    assert server._is_generated_queue(str(queue_path))

    monkeypatch.setattr(server, "_remove_generated_queue_file", remove_queue)
    server._cleanup_execution_queue(state)

    assert not queue_path.exists()
    assert not server._generated_queues


@pytest.mark.parametrize("stream_error", [False, True], ids=["disconnect", "stream-error"])
def test_failed_stream_cleanup_preserves_controller_and_queue_for_kill_retry(running_controller, monkeypatch, stream_error):
    client, content, process, root, _ = running_controller
    queue_response = client.post("/api/save_queue", json={"items": [{"path": str(content)}]})
    queue_path = Path(queue_response.json["path"])
    session_id = "failed-stream-cleanup"
    response = client.post("/api/execute", json={"path": str(queue_path), "session_id": session_id}, buffered=False)
    try:
        next(response.response)
        next(response.response)
        state = server.active_processes[session_id]
        server.set_execution_preview_target(session_id, str(state["run_token"]), str(content))
        if stream_error:
            state["output_queue"].put(("stdout", "\n"))
            monkeypatch.setattr(server, "_should_flush_subprocess_output", Mock(side_effect=RuntimeError("Stream failed")))
            event = json.loads(next(response.response).decode().removeprefix("data: "))
            assert event["type"] == "error"
        response.close()

        assert server.active_processes[session_id] is state
        root.terminate.assert_not_called()
        assert all(not stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        assert queue_path.is_file()
        assert server._is_generated_queue(str(queue_path))
        assert client.post("/api/input", json={"session_id": session_id, "input": "yes"}).status_code == 200
        assert process.stdin.getvalue() == "yes\n"

        root.children.side_effect = None
        root.children.return_value = []
        root.terminate.side_effect = lambda: setattr(process.poll, "return_value", 0)
        assert client.post("/api/kill", json={"session_id": session_id}).status_code == 200
        assert session_id not in server.active_processes
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        assert not queue_path.exists()
        assert not server._generated_queues
    finally:
        response.close()


@pytest.mark.parametrize("discovery_error", [psutil.AccessDenied(pid=1234), RuntimeError("Discovery failed")], ids=["denied", "unexpected-error"])
def test_failed_session_replacement_preserves_original_controller_and_queue(running_controller, discovery_error):
    client, content, process, root, spawn = running_controller
    root.children.side_effect = discovery_error
    queue_response = client.post("/api/save_queue", json={"items": [{"path": str(content)}]})
    queue_path = Path(queue_response.json["path"])
    session_id = "failed-replacement"
    state = server._make_process_state(str(queue_path), "")
    state.update({"mode": "subprocess", "process": process})
    server.active_processes[session_id] = state

    response = client.post("/api/execute", json={"path": str(queue_path), "session_id": session_id}, buffered=False)
    try:
        event = json.loads(next(response.response).decode().removeprefix("data: "))
        assert event["type"] == "error"
        response.close()
        assert server.active_processes[session_id] is state
        spawn.assert_not_called()
        assert all(not stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        assert queue_path.is_file()
        assert server._is_generated_queue(str(queue_path))
        assert client.post("/api/input", json={"session_id": session_id, "input": "yes"}).status_code == 200
    finally:
        response.close()


def test_session_replacement_waits_for_startup(running_controller):
    client, content, _, _, spawn = running_controller
    session_id = "replacement-during-startup"
    state = server._make_process_state(str(content), "")
    server.active_processes[session_id] = state
    response = client.post("/api/execute", json={"path": str(content), "session_id": session_id}, buffered=False)
    try:
        event = json.loads(next(response.response).decode().removeprefix("data: "))
        assert event["type"] == "error"
        response.close()
        assert server.active_processes[session_id] is state
        spawn.assert_not_called()
    finally:
        response.close()


def test_failed_termination_after_startup_cancellation_preserves_controller(running_controller):
    client, content, process, root, spawn = running_controller
    session_id = "cancel-during-startup"

    def cancel_during_spawn(*_args):
        assert client.post("/api/kill", json={"session_id": session_id}).status_code == 200
        return process, "subprocess"

    spawn.side_effect = cancel_during_spawn
    response = client.post("/api/execute", json={"path": str(content), "session_id": session_id}, buffered=False)
    try:
        next(response.response)
        event = json.loads(next(response.response).decode().removeprefix("data: "))
        assert event["type"] == "error"
        response.close()
        assert server.active_processes[session_id]["process"] is process
        root.terminate.assert_not_called()
        assert all(not stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        assert client.post("/api/kill", json={"session_id": session_id}).status_code == 500
    finally:
        response.close()


def test_disconnect_before_spawn_cancels_startup(running_controller):
    client, content, _, _, spawn = running_controller
    session_id = "disconnect-before-spawn"
    response = client.post("/api/execute", json={"path": str(content), "session_id": session_id}, buffered=False)
    next(response.response)
    response.close()
    assert session_id not in server.active_processes
    spawn.assert_not_called()


def test_kill_before_spawn_cancels_startup(running_controller):
    client, content, _, _, spawn = running_controller
    session_id = "kill-before-spawn"
    response = client.post("/api/execute", json={"path": str(content), "session_id": session_id}, buffered=False)
    try:
        next(response.response)
        assert client.post("/api/kill", json={"session_id": session_id}).status_code == 200
        with pytest.raises(StopIteration):
            next(response.response)
        assert session_id not in server.active_processes
        spawn.assert_not_called()
    finally:
        response.close()


def test_replacement_does_not_overwrite_a_newer_session(running_controller):
    client, content, process, root, spawn = running_controller
    session_id = "concurrent-replacement"
    old_state = server._make_process_state(str(content), "")
    old_state["process"] = process
    newer_state = server._make_process_state(str(content), "")
    server.active_processes[session_id] = old_state
    root.children.side_effect = None
    root.children.return_value = []

    def finish_previous_controller():
        process.poll.return_value = 0
        server.active_processes[session_id] = newer_state

    root.terminate.side_effect = finish_previous_controller
    response = client.post("/api/execute", json={"path": str(content), "session_id": session_id}, buffered=False)
    try:
        event = json.loads(next(response.response).decode().removeprefix("data: "))
        assert event["type"] == "error"
        response.close()
        assert server.active_processes[session_id] is newer_state
        spawn.assert_not_called()
    finally:
        response.close()
