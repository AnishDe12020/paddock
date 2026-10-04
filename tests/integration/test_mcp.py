"""Live protocol test against a deployed Paddock API service.

Opt in with MCP_URL after checking the endpoint's deployment identity. An
explicit endpoint that cannot be reached fails rather than silently skipping.
"""

import asyncio
import json
import os
import uuid

import pytest
from mcp import Client

MCP_URL = os.environ.get("MCP_URL")
MIN_CAPACITY_BYTES = int(os.environ.get("MCP_MIN_CAPACITY_BYTES", str(100 * 1000**3)))
pytestmark = pytest.mark.skipif(not MCP_URL, reason="Set MCP_URL to opt in to a live test")


def payload(result):
    if result.is_error:
        raise AssertionError(result.content)
    return result.structured_content


async def run_protocol() -> None:
    test_dir = f".paddock-self-test-{uuid.uuid4().hex}"
    test_file = f"{test_dir}/sample.txt"
    created = False
    async with Client(MCP_URL, raise_exceptions=True) as client:
        assert client.server_info is not None and client.server_info.name == "Paddock"
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
        assert status["capacity_bytes"] >= MIN_CAPACITY_BYTES

        try:
            written = payload(
                await client.call_tool(
                    "write_workspace_file",
                    {"path": test_file, "content": "first line\nsecond line\n"},
                )
            )
            created = True
            assert written["size"] == 23

            refused = await client.call_tool(
                "write_workspace_file",
                {"path": test_file, "content": "must not overwrite"},
            )
            assert refused.is_error

            read = payload(
                await client.call_tool(
                    "read_workspace_file",
                    {"path": test_file, "offset_bytes": 6, "max_bytes": 4},
                )
            )
            assert read["content"] == "line"
            assert read["next_offset_bytes"] == 10

            listed = payload(
                await client.call_tool(
                    "list_workspace",
                    {"path": test_dir, "recursive": True},
                )
            )
            assert any(entry["path"] == test_file for entry in listed["entries"])

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

            closed_output = payload(
                await client.call_tool(
                    "run_workspace_command",
                    {"command": "exec 1>&- 2>&-; sleep 4", "timeout_seconds": 1},
                )
            )
            assert closed_output["timed_out"]

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
            if created:
                payload(
                    await client.call_tool(
                        "delete_workspace_path", {"path": test_dir, "recursive": True}
                    )
                )


def test_paddock_live_protocol():
    asyncio.run(run_protocol())
    print(json.dumps({"server": MCP_URL, "status": "passed"}))
