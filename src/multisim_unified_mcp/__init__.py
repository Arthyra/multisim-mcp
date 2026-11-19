"""Expose selected Multisim tools through one persistent stdio MCP session."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from importlib.metadata import version
import json
from pathlib import Path
import tomllib

from mcp import ClientSession, StdioServerParameters, stdio_client, types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .handoff import HANDOFF_TOOL, handoff_to_com
from .logic_cases import VERIFY_LOGIC_TOOL, verify_logic_cases


TOOLS = {
    "native": (
        "native_runtime_status",
        "native_open_circuit",
        "native_create_circuit",
        "native_attach",
        "native_list_projects",
        "native_get_circuit_state",
        "native_search_library",
        "native_place_component",
        "native_move_component",
        "native_connect_endpoints",
        "native_label_wire",
        "native_add_text",
        "native_delete_object",
        "native_edit_batch",
        "native_save_source",
        "native_save_copy",
        "native_close_copy",
        "native_control_interactive",
        "native_interactive_status",
        "native_export_interactive_image",
        "native_set_logic_input",
        "native_read_logic_indicators",
    ),
    "com": (
        "runtime_status",
        "connect",
        "disconnect",
        "open_circuit",
        "enum_components",
        "enum_inputs",
        "enum_outputs",
        "run_transient",
        "run_ac_sweep",
        "run_dc_operating_point",
        "report_netlist",
        "decode_ms14",
    ),
}


async def serve(config: dict) -> None:
    async with AsyncExitStack() as stack:
        tools = {}
        routes = {}
        sessions = {}

        for backend, allowed in TOOLS.items():
            params = StdioServerParameters.model_validate(config[backend])
            if backend == "native":
                worker = Path(__file__).resolve().with_name("native_worker.py")
                params = params.model_copy(update={"args": [*params.args, str(worker)]})
            try:
                streams = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(
                    ClientSession(*streams, read_timeout_seconds=20)
                )
                await session.initialize()
                available = {}
                cursor = None
                while True:
                    page = await session.list_tools(
                        params=types.PaginatedRequestParams(cursor=cursor) if cursor else None
                    )
                    available.update((tool.name, tool) for tool in page.tools)
                    cursor = page.next_cursor
                    if cursor is None:
                        break
            except Exception as error:
                raise RuntimeError(f"Could not start the {backend} backend: {error}") from error

            missing = set(allowed) - available.keys()
            if backend == "native" and "native_handoff_context" not in available:
                missing.add("native_handoff_context")
            if missing:
                raise RuntimeError(f"{backend} backend is missing tools: {', '.join(sorted(missing))}")

            for name in allowed:
                public_name = name if backend == "native" else f"com_{name}"
                tools[public_name] = available[name].model_copy(update={"name": public_name})
                routes[public_name] = (session, name)
            sessions[backend] = session

        tools[VERIFY_LOGIC_TOOL.name] = VERIFY_LOGIC_TOOL
        tools[HANDOFF_TOOL.name] = HANDOFF_TOOL

        call_lock = asyncio.Lock()

        async def list_tools(_context, _params):
            return types.ListToolsResult(tools=list(tools.values()))

        async def call_tool(_context, params):
            # Both backends work on stateful Multisim sessions.
            async with call_lock:
                if params.name == VERIFY_LOGIC_TOOL.name:
                    result = await verify_logic_cases(sessions["com"], params.arguments or {})
                elif params.name == HANDOFF_TOOL.name:
                    result = await handoff_to_com(
                        sessions["native"], sessions["com"], params.arguments or {},
                    )
                elif params.name in routes:
                    session, upstream_name = routes[params.name]
                    return await session.call_tool(
                        upstream_name,
                        params.arguments,
                        read_timeout_seconds=600,
                    )
                else:
                    return types.CallToolResult(
                        is_error=True,
                        content=[types.TextContent(type="text", text=f"Unknown tool: {params.name}")],
                    )
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                    structured_content=result,
                )

        def input_schema(name):
            return tools[name].input_schema if name in tools else None

        server = Server(
            "multisim-mcp",
            version=version("multisim-unified-mcp"),
            instructions=(
                "Use native tools for library search, schematic editing and interactive simulation. "
                "Use handoff_to_com to stop, save and release the active native document, then open "
                "that exact saved file in COM. Close the COM connection before native edits. "
                "During F5, set stock logic inputs with native_set_logic_input and read "
                "red logic indicators with native_read_logic_indicators after the outputs settle."
            ),
            on_list_tools=list_tools,
            on_call_tool=call_tool,
            get_tool_input_schema=input_schema,
        )

        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified local MCP for NI Multisim")
    parser.add_argument("--config", type=Path, required=True, help="Backend configuration TOML")
    args = parser.parse_args()
    with args.config.open("rb") as file:
        config = tomllib.load(file)
    for backend in TOOLS:
        if backend not in config:
            parser.error(f"Missing [{backend}] in {args.config}")
    asyncio.run(serve(config))
