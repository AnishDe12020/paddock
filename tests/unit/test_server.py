"""Unit tests for the Paddock MCP server (no deployment required)."""

import base64
import importlib
import os
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

PRLIMIT = Path("/usr/bin/prlimit")
requires_prlimit = pytest.mark.skipif(not PRLIMIT.exists(), reason="prlimit is not available")


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """Import paddock.server against a throwaway workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("PADDOCK_WORKSPACE", str(workspace))
    import paddock.server as server_module

    module = importlib.reload(server_module)
    try:
        yield module
    finally:
        monkeypatch.undo()
        importlib.reload(server_module)


@contextmanager
def server_with_env(monkeypatch, **env):
    """Reload paddock.server with environment overrides for the block, then restore."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import paddock.server as server_module

    module = importlib.reload(server_module)
    try:
        yield module
    finally:
        monkeypatch.undo()
        importlib.reload(server_module)


# --- path containment --------------------------------------------------------


def test_rejects_absolute_path(server):
    with pytest.raises(ValueError):
        server.workspace_path("/etc/passwd")


def test_rejects_parent_escape(server):
    with pytest.raises(ValueError):
        server.workspace_path("../outside")


def test_rejects_nested_parent_escape(server):
    with pytest.raises(ValueError):
        server.workspace_path("a/../../b")


def test_rejects_empty_as_root(server):
    target = server.workspace_path(".")
    assert target == server.ROOT


def test_accepts_relative_path(server):
    (server.ROOT / "sub").mkdir()
    assert server.workspace_path("sub") == server.ROOT / "sub"


def test_symlink_escape_is_blocked(server, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (server.ROOT / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        server.workspace_path("link", follow_final=True)
    with pytest.raises(ValueError):
        server.workspace_path("link/secret.txt", follow_final=True)
    # Even without following the final component, a symlinked parent escapes.
    with pytest.raises(ValueError):
        server.workspace_path("link/secret.txt", follow_final=False)


# --- file tools ----------------------------------------------------------------


def test_write_read_overwrite_delete_cycle(server):
    written = server.write_workspace_file("docs/note.txt", "hello")
    assert written == {"path": "docs/note.txt", "size": 5, "overwritten": False}

    read = server.read_workspace_file("docs/note.txt")
    assert read["content"] == "hello"
    assert read["bytes_read"] == 5
    assert read["next_offset_bytes"] is None

    with pytest.raises(ValueError):
        server.write_workspace_file("docs/note.txt", "must not overwrite")

    overwritten = server.write_workspace_file("docs/note.txt", "other!", overwrite=True)
    assert overwritten["overwritten"] is True
    assert server.read_workspace_file("docs/note.txt")["content"] == "other!"

    deleted = server.delete_workspace_path("docs/note.txt")
    assert deleted == {"deleted": "docs/note.txt", "recursive": False}
    with pytest.raises(ValueError):
        server.read_workspace_file("docs/note.txt")


def test_delete_requires_recursive_for_trees(server):
    server.write_workspace_file("tree/inner/file.txt", "data")
    with pytest.raises(OSError):
        server.delete_workspace_path("tree")
    deleted = server.delete_workspace_path("tree", recursive=True)
    assert deleted["recursive"] is True
    assert not (server.ROOT / "tree").exists()


def test_base64_roundtrip(server):
    data = b"\x00\x01binary"
    server.write_workspace_file(
        "bin/blob", base64.b64encode(data).decode("ascii"), encoding="base64"
    )
    read = server.read_workspace_file("bin/blob", encoding="base64")
    assert base64.b64decode(read["content"]) == data


def test_write_rejects_oversized_content(server):
    with pytest.raises(ValueError):
        server.write_workspace_file("big.bin", "x" * (server.MAX_FILE_BYTES + 1))


def test_read_rejects_bad_ranges(server):
    server.write_workspace_file("f.txt", "data")
    with pytest.raises(ValueError):
        server.read_workspace_file("f.txt", offset_bytes=-1)
    with pytest.raises(ValueError):
        server.read_workspace_file("f.txt", max_bytes=0)
    with pytest.raises(ValueError):
        server.read_workspace_file("f.txt", max_bytes=server.MAX_READ_BYTES + 1)


def test_delete_rejects_root_and_missing(server):
    with pytest.raises(ValueError):
        server.delete_workspace_path(".")
    with pytest.raises(ValueError):
        server.delete_workspace_path("nope.txt")


def test_write_rejects_root_and_directories(server):
    (server.ROOT / "dir").mkdir()
    with pytest.raises(ValueError):
        server.write_workspace_file(".", "x")
    with pytest.raises(ValueError):
        server.write_workspace_file("dir", "x")


def test_list_marks_truncated_result(server):
    server.write_workspace_file("one.txt", "1")
    server.write_workspace_file("two.txt", "2")
    result = server.list_workspace(max_entries=1)
    assert len(result["entries"]) == 1
    assert result["truncated"] is True


# --- command execution -----------------------------------------------------------


@requires_prlimit
def test_command_output_and_streams(server):
    result = server.execute("printf out; printf err >&2", 10, 65536)
    assert result["exit_code"] == 0
    assert result["stdout"] == "out"
    assert result["stderr"] == "err"
    assert result["timed_out"] is False
    assert result["output_truncated"] is False


@requires_prlimit
def test_command_runs_in_workspace(server):
    result = server.execute("pwd", 10, 65536)
    assert result["stdout"].strip() == str(server.ROOT)


@requires_prlimit
def test_command_stdout_truncation(server):
    result = server.execute("head -c 4096 /dev/zero | tr '\\0' x", 10, 1024)
    assert result["output_truncated"] is True
    assert len(result["stdout"].encode()) == 1024


@requires_prlimit
def test_command_exact_output_is_not_truncated(server):
    result = server.execute("head -c 1024 /dev/zero | tr '\\0' x", 10, 1024)
    assert result["output_truncated"] is False
    assert len(result["stdout"].encode()) == 1024


@requires_prlimit
def test_command_stderr_truncation(server):
    result = server.execute("head -c 2048 /dev/zero | tr '\\0' y >&2", 10, 1024)
    assert result["output_truncated"] is True
    assert len(result["stderr"].encode()) == 1024
    assert result["stdout"] == ""


@requires_prlimit
def test_command_timeout_kills_process_group(server):
    result = server.execute("sleep 30", 1, 4096)
    assert result["timed_out"] is True
    assert result["exit_code"] == -9  # SIGKILL


@requires_prlimit
def test_timeout_returns_when_detached_child_holds_pipes(server):
    started = time.monotonic()
    result = server.execute(
        "setsid sh -c 'sleep 60 & echo $! > detached.pid'",
        1,
        4096,
    )
    elapsed = time.monotonic() - started
    detached_pid = int((server.ROOT / "detached.pid").read_text())
    try:
        os.kill(detached_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    assert result["timed_out"] is True
    assert elapsed < 3


@requires_prlimit
def test_command_env_is_minimal(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with server_with_env(
        monkeypatch,
        PADDOCK_WORKSPACE=str(workspace),
        PADDOCK_UNIT_TEST_SECRET="must-not-leak",
    ) as module:
        assert "PADDOCK_UNIT_TEST_SECRET" not in module.COMMAND_ENV
        assert module.COMMAND_ENV["HOME"] == str(module.ROOT)
        leaked = module.execute("printenv PADDOCK_UNIT_TEST_SECRET", 10, 4096)
        assert leaked["exit_code"] == 1
        assert leaked["stdout"] == ""
        home = module.execute("printenv HOME", 10, 4096)
        assert home["stdout"].strip() == str(module.ROOT)


@requires_prlimit
def test_command_slot_limit(server, monkeypatch):
    monkeypatch.setattr(server, "EXEC_SLOTS", threading.BoundedSemaphore(0))
    with pytest.raises(RuntimeError, match="too many concurrent commands"):
        server.execute("true", 5, 1024)


# --- informational limits -------------------------------------------------------


def test_workspace_status_reports_limits(server):
    status = server.workspace_status()
    assert status["workspace"] == str(server.ROOT)
    assert status["cpu_limit"] == server.CPU_LIMIT
    assert status["memory_limit_bytes"] == server.MEMORY_LIMIT_BYTES
    assert status["process_limit"] == server.PROCESS_LIMIT
    assert status["command_timeout_max_seconds"] == server.MAX_TIMEOUT == 300
    assert status["capacity_bytes"] > 0


def test_limit_env_overrides(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with server_with_env(
        monkeypatch,
        PADDOCK_WORKSPACE=str(workspace),
        PADDOCK_CPU_LIMIT="2",
        PADDOCK_MEMORY_LIMIT_BYTES=str(8 * 1024**3),
        PADDOCK_PROCESS_LIMIT="64",
    ) as module:
        assert module.CPU_LIMIT == 2
        assert module.MEMORY_LIMIT_BYTES == 8 * 1024**3
        assert module.PROCESS_LIMIT == 64


def test_limit_env_defaults(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with server_with_env(monkeypatch, PADDOCK_WORKSPACE=str(workspace)) as module:
        assert module.CPU_LIMIT == 4
        assert module.MEMORY_LIMIT_BYTES == 16 * 1024**3
        assert module.PROCESS_LIMIT == 1024


def test_transport_allowed_hosts_loads_operator_hosts(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    allowed_hosts = tmp_path / "allowed-hosts"
    allowed_hosts.write_text("# ingress names\npaddock.example.ts.net\nMCP.EXAMPLE.COM\n")
    with server_with_env(
        monkeypatch,
        PADDOCK_WORKSPACE=str(workspace),
        PADDOCK_ALLOWED_HOSTS_FILE=str(allowed_hosts),
    ) as module:
        hosts = module.transport_allowed_hosts()
        assert "10.89.0.4:*" in hosts
        assert "paddock.example.ts.net" in hosts
        assert "paddock.example.ts.net:*" in hosts
        assert "mcp.example.com" in hosts


def test_transport_allowed_hosts_rejects_urls(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    allowed_hosts = tmp_path / "allowed-hosts"
    allowed_hosts.write_text("https://mcp.example.com\n")
    with (
        server_with_env(
            monkeypatch,
            PADDOCK_WORKSPACE=str(workspace),
            PADDOCK_ALLOWED_HOSTS_FILE=str(allowed_hosts),
        ) as module,
        pytest.raises(SystemExit, match="invalid host"),
    ):
        module.transport_allowed_hosts()


def test_transport_allowed_hosts_rejects_malformed_names(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    allowed_hosts = tmp_path / "allowed-hosts"
    allowed_hosts.write_text("bad..example.com\n")
    with (
        server_with_env(
            monkeypatch,
            PADDOCK_WORKSPACE=str(workspace),
            PADDOCK_ALLOWED_HOSTS_FILE=str(allowed_hosts),
        ) as module,
        pytest.raises(SystemExit, match="invalid host"),
    ):
        module.transport_allowed_hosts()


def test_transport_allowed_hosts_rejects_directory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    allowed_hosts = tmp_path / "allowed-hosts"
    allowed_hosts.mkdir()
    with (
        server_with_env(
            monkeypatch,
            PADDOCK_WORKSPACE=str(workspace),
            PADDOCK_ALLOWED_HOSTS_FILE=str(allowed_hosts),
        ) as module,
        pytest.raises(SystemExit, match="not a file"),
    ):
        module.transport_allowed_hosts()


def test_main_refuses_root(server, monkeypatch):
    monkeypatch.setattr(server.os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit, match="must not run as root"):
        server.main()


def test_stdio_main_refuses_root(server, monkeypatch):
    monkeypatch.setattr(server.os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit, match="must not run as root"):
        server.main_stdio()


def test_stdio_main_selects_stdio_transport(server, monkeypatch):
    calls = []
    monkeypatch.setattr(server.os, "geteuid", lambda: 11000)
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.append(kwargs))
    server.main_stdio()
    assert calls == [{"transport": "stdio"}]
