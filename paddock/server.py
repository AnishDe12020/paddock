"""Paddock MCP server: bounded workspace and compute access for untrusted agents."""

import asyncio
import base64
import binascii
import os
import re
import secrets
import selectors
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

ROOT = Path(os.environ.get("PADDOCK_WORKSPACE", "/workspace")).resolve()
CPU_LIMIT = int(os.environ.get("PADDOCK_CPU_LIMIT", "4"))
MEMORY_LIMIT_BYTES = int(os.environ.get("PADDOCK_MEMORY_LIMIT_BYTES", str(16 * 1024**3)))
PROCESS_LIMIT = int(os.environ.get("PADDOCK_PROCESS_LIMIT", "1024"))
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_READ_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_TIMEOUT = 300
EXEC_SLOTS = threading.BoundedSemaphore(4)
COMMAND_ENV = {
    key: value
    for key, value in os.environ.items()
    if key
    in {
        "PATH",
        "LANG",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    }
}
COMMAND_ENV["HOME"] = str(ROOT)
DEFAULT_ALLOWED_HOSTS = [
    "10.89.0.4",
    "10.89.0.4:*",
    "api",
    "api:*",
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
]
ALLOWED_HOSTS_FILE = Path(
    os.environ.get("PADDOCK_ALLOWED_HOSTS_FILE", "/etc/paddock/allowed-hosts")
)


def workspace_path(raw_path: str, *, follow_final: bool = True) -> Path:
    relative = Path(raw_path or ".")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("path must be relative and may not contain '..'")
    candidate = ROOT / relative
    if follow_final:
        candidate = candidate.resolve()
    else:
        candidate = candidate.parent.resolve() / candidate.name
    if candidate != ROOT and ROOT not in candidate.parents:
        raise ValueError("path escapes workspace")
    return candidate


def workspace_entries(target: Path, recursive: bool) -> Iterator[Path]:
    pending = [target]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as scanner:
            for entry in scanner:
                item = Path(entry.path)
                yield item
                if recursive and entry.is_dir(follow_symlinks=False):
                    pending.append(item)


def transport_allowed_hosts() -> list[str]:
    hosts = list(DEFAULT_ALLOWED_HOSTS)
    if not ALLOWED_HOSTS_FILE.exists():
        return hosts
    if not ALLOWED_HOSTS_FILE.is_file():
        raise SystemExit(f"allowed-hosts path is not a file: {ALLOWED_HOSTS_FILE}")
    for raw_line in ALLOWED_HOSTS_FILE.read_text().splitlines():
        host = raw_line.strip().lower()
        if not host or host.startswith("#"):
            continue
        labels = host.split(".")
        if len(host) > 253 or any(
            len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        ):
            raise SystemExit(f"invalid host in {ALLOWED_HOSTS_FILE}: {raw_line!r}")
        hosts.extend((host, f"{host}:*"))
    return list(dict.fromkeys(hosts))


def execute(command: str, timeout: int, output_limit: int) -> dict[str, object]:
    if not EXEC_SLOTS.acquire(blocking=False):
        raise RuntimeError("too many concurrent commands")
    try:
        process = subprocess.Popen(
            [
                "/usr/bin/prlimit",
                "--core=0",
                "--nofile=256",
                "--",
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                command,
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=COMMAND_ENV,
            start_new_session=True,
        )
        streams = {process.stdout: bytearray(), process.stderr: bytearray()}
        truncated = {process.stdout: False, process.stderr: False}
        with selectors.DefaultSelector() as selector:
            for stream in streams:
                selector.register(stream, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            timed_out = False
            drain_deadline = None
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0 and not timed_out:
                    timed_out = True
                    drain_deadline = time.monotonic() + 1
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                if timed_out and drain_deadline is not None and time.monotonic() >= drain_deadline:
                    for key in list(selector.get_map().values()):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    break
                events = selector.select(0.1 if timed_out else max(0, remaining))
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    output = streams[key.fileobj]
                    remaining_capacity = max(0, output_limit - len(output))
                    output.extend(chunk[:remaining_capacity])
                    if len(chunk) > remaining_capacity:
                        truncated[key.fileobj] = True
        return_code = process.wait()
        return {
            "exit_code": return_code,
            "timed_out": timed_out,
            "stdout": bytes(streams[process.stdout]).decode("utf-8", "replace"),
            "stderr": bytes(streams[process.stderr]).decode("utf-8", "replace"),
            "output_truncated": any(truncated.values()),
        }
    finally:
        EXEC_SLOTS.release()


mcp = MCPServer(
    "Paddock",
    instructions=(
        f"Persistent files live under {ROOT}. Inspect files before overwriting or deleting them. "
        f"Shell commands start in {ROOT}, run with bounded resources and time, and can access only "
        "HTTP/HTTPS through a filtered proxy. Write large command results to files and read them "
        "in chunks."
    ),
)


@mcp.tool(
    title="Workspace status",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
)
def workspace_status() -> dict[str, object]:
    """Report persistent workspace capacity and the compute limits exposed to this server."""
    stats = os.statvfs(ROOT)
    return {
        "workspace": str(ROOT),
        "capacity_bytes": stats.f_blocks * stats.f_frsize,
        "available_bytes": stats.f_bavail * stats.f_frsize,
        "cpu_limit": CPU_LIMIT,
        "memory_limit_bytes": MEMORY_LIMIT_BYTES,
        "process_limit": PROCESS_LIMIT,
        "command_timeout_max_seconds": MAX_TIMEOUT,
        "network": "HTTP/HTTPS only through a filtered proxy",
    }


@mcp.tool(
    title="List workspace",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
)
def list_workspace(
    path: str = ".", recursive: bool = False, max_entries: int = 200
) -> dict[str, object]:
    """List files and directories below a relative workspace path."""
    if max_entries < 1 or max_entries > 1000:
        raise ValueError("max_entries must be between 1 and 1000")
    target = workspace_path(path)
    if not target.is_dir():
        raise ValueError("path is not a directory")
    entries = []
    truncated = False
    for item in workspace_entries(target, recursive):
        if len(entries) >= max_entries:
            truncated = True
            break
        info = item.lstat()
        entries.append(
            {
                "path": str(item.relative_to(ROOT)),
                "type": "symlink"
                if item.is_symlink()
                else "directory"
                if item.is_dir()
                else "file",
                "size": info.st_size if item.is_file() else None,
                "modified_unix": int(info.st_mtime),
            }
        )
    return {"path": str(target.relative_to(ROOT)), "entries": entries, "truncated": truncated}


@mcp.tool(
    title="Read workspace file",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
)
def read_workspace_file(
    path: str,
    offset_bytes: int = 0,
    max_bytes: int = 65536,
    encoding: Literal["text", "base64"] = "text",
) -> dict[str, object]:
    """Read a byte range from a workspace file as UTF-8 text or base64."""
    if offset_bytes < 0:
        raise ValueError("offset_bytes must be non-negative")
    if max_bytes < 1 or max_bytes > MAX_READ_BYTES:
        raise ValueError(f"max_bytes must be between 1 and {MAX_READ_BYTES}")
    target = workspace_path(path)
    if not target.is_file():
        raise ValueError("path is not a file")
    size = target.stat().st_size
    with target.open("rb") as handle:
        handle.seek(offset_bytes)
        data = handle.read(max_bytes)
    content = (
        data.decode("utf-8", "replace")
        if encoding == "text"
        else base64.b64encode(data).decode("ascii")
    )
    return {
        "path": str(target.relative_to(ROOT)),
        "size": size,
        "offset_bytes": offset_bytes,
        "bytes_read": len(data),
        "next_offset_bytes": offset_bytes + len(data) if offset_bytes + len(data) < size else None,
        "encoding": encoding,
        "content": content,
    }


@mcp.tool(
    title="Write workspace file",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False),
)
def write_workspace_file(
    path: str,
    content: str,
    encoding: Literal["text", "base64"] = "text",
    overwrite: bool = False,
) -> dict[str, object]:
    """Atomically create a workspace file; set overwrite only when replacing an existing file is intended."""
    target = workspace_path(path, follow_final=False)
    if target == ROOT:
        raise ValueError("a file path is required")
    existed = target.exists() or target.is_symlink()
    if existed and not overwrite:
        raise ValueError("file exists; set overwrite=true to replace it")
    if target.is_dir():
        raise ValueError("path is a directory")
    try:
        data = (
            content.encode("utf-8")
            if encoding == "text"
            else base64.b64decode(content, validate=True)
        )
    except (UnicodeError, binascii.Error) as error:
        raise ValueError(f"invalid {encoding} content") from error
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES} byte tool limit")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{secrets.token_hex(8)}")
    try:
        temporary.write_bytes(data)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(target.relative_to(ROOT)), "size": len(data), "overwritten": existed}


@mcp.tool(
    title="Delete workspace path",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False),
)
def delete_workspace_path(path: str, recursive: bool = False) -> dict[str, object]:
    """Delete a file, symlink, empty directory, or a directory tree when recursive is explicitly true."""
    target = workspace_path(path, follow_final=False)
    if target == ROOT or (not target.exists() and not target.is_symlink()):
        raise ValueError("path does not exist or refers to the workspace root")
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif recursive:
        shutil.rmtree(target)
    else:
        target.rmdir()
    return {"deleted": str(target.relative_to(ROOT)), "recursive": recursive}


@mcp.tool(
    title="Run workspace command",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True),
)
async def run_workspace_command(
    command: str,
    timeout_seconds: int = 60,
    max_output_bytes: int = 65536,
) -> dict[str, object]:
    """Run Bash in the workspace with bounded time/output and filtered web-only network access."""
    if not command.strip():
        raise ValueError("command must be non-empty")
    if timeout_seconds < 1 or timeout_seconds > MAX_TIMEOUT:
        raise ValueError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT}")
    if max_output_bytes < 1024 or max_output_bytes > MAX_OUTPUT_BYTES:
        raise ValueError(f"max_output_bytes must be between 1024 and {MAX_OUTPUT_BYTES}")
    return await asyncio.to_thread(execute, command, timeout_seconds, max_output_bytes)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok", "protocol": "mcp-streamable-http"})


def main() -> None:
    refuse_root()
    security = TransportSecuritySettings(
        allowed_hosts=transport_allowed_hosts(),
        allowed_origins=[],
    )
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
        json_response=True,
        transport_security=security,
    )


def refuse_root() -> None:
    if os.geteuid() == 0:
        raise SystemExit(
            "paddock-server must not run as root; run it as the unprivileged workspace uid"
        )


def main_stdio() -> None:
    refuse_root()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
