"""Exercise gateway failure handling with small local MCP child processes."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters, stdio_client, types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from multisim_unified_mcp import TOOLS


async def fake_backend(role: str, stop_on_call: bool):
    calls = []
    names = TOOLS[role]
    if role == "native":
        names += ("native_handoff_context", "native_f5_logic_snapshot")

    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=[
            types.Tool(name=name, input_schema={"type": "object"}) for name in names
        ])

    async def call_tool(_context, params):
        if stop_on_call:
            os._exit(3)
        calls.append(params.name)
        return types.CallToolResult(content=[], structured_content={"calls": calls})

    server = Server(role, on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


@asynccontextmanager
async def gateway(*, missing=None, crashes=None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = root / "config.toml"
        settings = []
        for role in TOOLS:
            command = str(root / "absent-python.exe") if role == missing else sys.executable
            args = [str(Path(__file__).resolve()), "backend", role, str(role == crashes)]
            settings.extend([
                f"[{role}]", f"command = {json.dumps(command)}", f"args = {json.dumps(args)}", "",
            ])
        config.write_text("\n".join(settings), encoding="utf-8")
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", "from multisim_unified_mcp import main; main()", "--config", str(config)],
        )
        async with stdio_client(params) as streams, ClientSession(*streams, read_timeout_seconds=30) as session:
            await session.initialize()
            yield session


class BackendIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_com_keeps_native_and_blocks_handoff_before_mutation(self):
        async with gateway(missing="com") as session:
            listed = {tool.name for tool in (await session.list_tools()).tools}
            self.assertIn("native_runtime_status", listed)
            self.assertNotIn("com_open_circuit", listed)
            state = (await session.call_tool("gateway_status")).structured_content
            self.assertTrue(state["native"]["reachable"])
            self.assertEqual(state["com"]["stage"], "launch")
            receipt = (await session.call_tool("handoff_to_com", {"path": "unused.ms14"})).structured_content
            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["stage"], "check_backends")
            self.assertFalse(receipt["native_released"])
            native = await session.call_tool("native_runtime_status")
            self.assertEqual(native.structured_content, {"calls": ["native_runtime_status"]})

    async def test_missing_native_keeps_com(self):
        async with gateway(missing="native") as session:
            listed = {tool.name for tool in (await session.list_tools()).tools}
            self.assertIn("com_runtime_status", listed)
            self.assertNotIn("native_place_component", listed)
            state = (await session.call_tool("gateway_status")).structured_content
            self.assertFalse(state["native"]["available"])
            self.assertTrue(state["com"]["reachable"])
            result = await session.call_tool("com_runtime_status")
            self.assertEqual(result.structured_content, {"calls": ["runtime_status"]})

    async def test_native_disconnect_does_not_take_down_com(self):
        async with gateway(crashes="native") as session:
            error = await session.call_tool("native_runtime_status")
            self.assertTrue(error.is_error)
            self.assertEqual(error.structured_content["stage"], "disconnected")
            state = (await session.call_tool("gateway_status")).structured_content
            self.assertFalse(state["native"]["available"])
            self.assertTrue(state["com"]["reachable"])
            result = await session.call_tool("com_runtime_status")
            self.assertEqual(result.structured_content, {"calls": ["runtime_status"]})


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backend":
        asyncio.run(fake_backend(sys.argv[2], sys.argv[3] == "True"))
    else:
        unittest.main()
