"""Exercise gateway failure handling with small local MCP child processes."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from anyio import create_task_group
from mcp import ClientSession, StdioServerParameters, stdio_client, types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from multisim_unified_mcp import TOOLS


async def fake_backend(role: str, stop_on_call: bool, omit: str, wait_for: str, close_on: str):
    calls = []
    names = TOOLS[role]
    if role == "native":
        names += ("native_handoff_context", "native_f5_logic_snapshot")

    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=[
            types.Tool(name=name, input_schema={"type": "object"}) for name in names if name != omit
        ])

    async def call_tool(_context, params):
        if stop_on_call:
            os._exit(3)
        if wait_for and role == "native":
            marker = Path(wait_for)
            marker.with_suffix(".started").touch()
            while not marker.exists():
                await asyncio.sleep(0.02)
        calls.append(params.name)
        return types.CallToolResult(content=[], structured_content={"calls": calls})

    async def ping(_context, _params):
        if wait_for and role == "native":
            marker = Path(wait_for)
            while marker.with_suffix(".started").exists() and not marker.exists():
                await asyncio.sleep(0.02)
        return types.EmptyResult()

    server = Server(role, on_list_tools=list_tools, on_call_tool=call_tool, on_ping=ping)
    close_marker = Path(close_on) if close_on and role == "native" else None
    async with stdio_server() as (reader, writer):
        run = server.run(reader, writer, server.create_initialization_options())
        if close_marker is None:
            await run
        else:
            async def exit_on_marker():
                while not close_marker.exists():
                    await asyncio.sleep(0.02)
                # This fake has no resources to release. Exit with status zero
                # to close its stdio transport without an in-flight tool call.
                os._exit(0)

            async with create_task_group() as tasks:
                tasks.start_soon(exit_on_marker)
                await run
                tasks.cancel_scope.cancel()


@asynccontextmanager
async def gateway(*, missing=None, crashes=None, omit=None, waits=None, close_on=None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = root / "config.toml"
        settings = []
        for role in TOOLS:
            command = str(root / "absent-python.exe") if role == missing else sys.executable
            args = [
                str(Path(__file__).resolve()), "backend", role, str(role == crashes),
                omit or "", str(waits) if waits else "", str(close_on) if close_on else "",
            ]
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
            before = await session.list_tools()
            error = await session.call_tool("native_runtime_status")
            self.assertTrue(error.is_error)
            self.assertEqual(error.structured_content["stage"], "disconnected")
            self.assertEqual(error.structured_content["code"], "backend_unavailable")
            state = (await session.call_tool("gateway_status")).structured_content
            self.assertFalse(state["native"]["available"])
            self.assertTrue(state["com"]["reachable"])
            self.assertFalse(state["native"]["reachable"])
            self.assertEqual(await session.list_tools(), before)
            stale = await session.call_tool("native_runtime_status")
            self.assertTrue(stale.is_error)
            self.assertEqual(stale.structured_content["code"], "backend_unavailable")
            result = await session.call_tool("com_runtime_status")
            self.assertEqual(result.structured_content, {"calls": ["runtime_status"]})

    async def test_clean_idle_disconnect_preserves_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "close"
            async with gateway(close_on=marker) as session:
                before = await session.list_tools()
                marker.touch()
                async with asyncio.timeout(5):
                    while True:
                        status = (await session.call_tool("gateway_status")).structured_content
                        if not status["native"]["available"]:
                            break
                        await asyncio.sleep(0.02)
                self.assertFalse(status["native"]["reachable"])
                self.assertEqual(status["native"]["stage"], "disconnected")
                self.assertTrue(status["com"]["reachable"])
                self.assertEqual(await session.list_tools(), before)
                error = await session.call_tool("native_check_layout")
                self.assertTrue(error.is_error)
                self.assertEqual(error.structured_content["code"], "backend_unavailable")

    async def test_missing_optional_tool_leaves_rest_of_native_available(self):
        async with gateway(omit="native_check_layout") as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            self.assertNotIn("native_check_layout", names)
            self.assertTrue({"native_place_component", "native_check_netlist", "native_read_waveform"} <= names)
            response = await session.call_tool("native_check_layout")
            self.assertTrue(response.is_error)
            self.assertEqual(response.structured_content["code"], "tool_unavailable")
            response = await session.call_tool("native_check_netlist")
            self.assertEqual(response.structured_content, {"calls": ["native_check_netlist"]})

    async def test_missing_workflow_dependency_has_actionable_error(self):
        async with gateway(omit="native_f5_logic_snapshot") as session:
            result = await session.call_tool("native_verify_f5_cases", {
                "cases": [{"name": "one", "inputs": {"A": 1}, "expected": {"S": 1}}],
            })
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content["missing_tools"], ["native_f5_logic_snapshot"])

    async def test_missing_core_interface_disables_only_its_backend(self):
        async with gateway(omit="native_get_circuit_state") as session:
            state = (await session.call_tool("gateway_status")).structured_content
            self.assertFalse(state["native"]["available"])
            self.assertEqual(state["native"]["missing_tools"], ["native_get_circuit_state"])
            self.assertTrue(state["com"]["available"])

    async def test_gateway_status_stays_available_during_stateful_call(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "release"
            async with gateway(waits=marker) as session:
                long_call = asyncio.create_task(session.call_tool("native_run_analysis"))
                try:
                    async with asyncio.timeout(5):
                        while not marker.with_suffix(".started").exists():
                            await asyncio.sleep(0.02)
                    com_call = asyncio.create_task(session.call_tool("com_open_circuit", {"path": "sample.ms14"}))
                    try:
                        async with asyncio.timeout(3):
                            response = await session.call_tool("gateway_status")
                        status = response.structured_content
                        self.assertTrue(status["native"]["available"])
                        self.assertFalse(status["native"]["reachable"])
                        self.assertTrue(status["com"]["reachable"])
                        self.assertFalse(com_call.done())
                    finally:
                        marker.touch()
                        await com_call
                finally:
                    marker.touch()
                    await long_call
                status = (await session.call_tool("gateway_status")).structured_content
                self.assertTrue(status["native"]["reachable"])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backend":
        asyncio.run(fake_backend(sys.argv[2], sys.argv[3] == "True", *sys.argv[4:7]))
    else:
        unittest.main()
