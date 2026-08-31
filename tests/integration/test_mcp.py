"""Live protocol test against a deployed Paddock API service.

Skipped unless the MCP endpoint is reachable; set MCP_URL to point elsewhere.
"""

import asyncio
import json
import os
import socket
from urllib.parse import urlparse

import pytest
from mcp import Client

MCP_URL = os.environ.get("MCP_URL", "http://10.89.0.4:8000/mcp")
TEST_DIR = ".paddock-self-test"
TEST_FILE = f"{TEST_DIR}/sample.txt"


def _server_reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _server_reachable(MCP_URL),
    reason=f"Paddock API is not reachable at {MCP_URL}",
)


def payload(result):
    if result.is_error:
        raise AssertionError(result.content)
    return result.structured_content


async def run_protocol() -> None:
    async with Client(MCP_URL, raise_exceptions=True) as client:
        discovered = await client.list_tools()
        names = {tool.name for tool in discovered.tools}
        assert names == {
            "workspace_status",
            "list_workspace",
            "read_workspace_file",
            "write_workspace_file",
            "delete_workspace_path",
            "run_workspace_command",
        }, names
        tools = {tool.name: tool for tool in discovered.tools}
        assert tools["workspace_status"].annotations.read_only_hint
        assert tools["read_workspace_file"].annotations.read_only_hint
        assert tools["write_workspace_file"].annotations.destructive_hint
        assert tools["delete_workspace_path"].annotations.destructive_hint
        assert tools["run_workspace_command"].annotations.open_world_hint

        status = payload(await client.call_tool("workspace_status"))
        assert status["workspace"] == "/workspace"
        assert status["capacity_bytes"] >= 100 * 1000**3

        try:
            await client.call_tool("delete_workspace_path", {"path": TEST_DIR, "recursive": True})
            written = payload(
                await client.call_tool(
                    "write_workspace_file",
                    {"path": TEST_FILE, "content": "first line\nsecond line\n"},
                )
            )
            assert written["size"] == 23

            refused = await client.call_tool(
                "write_workspace_file",
                {"path": TEST_FILE, "content": "must not overwrite"},
            )
            assert refused.is_error

            read = payload(
                await client.call_tool(
                    "read_workspace_file",
                    {"path": TEST_FILE, "offset_bytes": 6, "max_bytes": 4},
                )
            )
            assert read["content"] == "line"
            assert read["next_offset_bytes"] == 10

            listed = payload(
                await client.call_tool(
                    "list_workspace",
                    {"path": TEST_DIR, "recursive": True},
                )
            )
            assert any(entry["path"] == TEST_FILE for entry in listed["entries"])

            command = payload(
                await client.call_tool(
                    "run_workspace_command",
                    {"command": "pwd; printf command-ok; printf error-ok >&2"},
                )
            )
            assert command["exit_code"] == 0
            assert command["stdout"] == "/workspace\ncommand-ok"
            assert command["stderr"] == "error-ok"

            timeout = payload(
                await client.call_tool(
                    "run_workspace_command",
                    {"command": "sleep 2", "timeout_seconds": 1},
                )
            )
            assert timeout["timed_out"]

            truncated = payload(
                await client.call_tool(
                    "run_workspace_command",
                    {"command": "python3 -c 'print(\"x\" * 4096)'", "max_output_bytes": 1024},
                )
            )
            assert truncated["output_truncated"]
            assert len(truncated["stdout"].encode()) == 1024

            escaped = await client.call_tool("read_workspace_file", {"path": "../etc/passwd"})
            assert escaped.is_error
        finally:
            await client.call_tool("delete_workspace_path", {"path": TEST_DIR, "recursive": True})


def test_paddock_live_protocol():
    asyncio.run(run_protocol())
    print(json.dumps({"server": MCP_URL, "status": "passed"}))
