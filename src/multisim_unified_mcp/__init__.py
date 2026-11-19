"""Expose selected Multisim tools through one persistent stdio MCP session."""

from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import sys
import tomllib

from mcp import types
from mcp.shared.exceptions import MCPError
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .backends import Backend, start_backends
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


STATUS_TOOL = types.Tool(
    name="gateway_status",
    description="Report backend availability and startup stage. Does not open a Multisim circuit.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)


def configured_backends(config: dict) -> dict[str, Backend]:
    return {
        "native": Backend("native", config.get("native"), (*TOOLS["native"], "native_handoff_context")),
        "com": Backend("com", config.get("com"), TOOLS["com"]),
    }


def tool_result(result, *, is_error=False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
        structured_content=result,
        is_error=is_error,
    )


async def serve(backends: dict[str, Backend]) -> None:
    async with start_backends(backends, sys.stderr):
        builtins = {tool.name: tool for tool in (STATUS_TOOL, HANDOFF_TOOL, VERIFY_LOGIC_TOOL)}
        routes = {
            name if role == "native" else f"com_{name}": (backends[role], name)
            for role, names in TOOLS.items() for name in names
        }
        tools = {
            public: backend.tools[upstream].model_copy(update={"name": public})
            for public, (backend, upstream) in routes.items()
            if upstream in backend.tools and backend.session is not None
        }

        call_lock = asyncio.Lock()

        async def list_tools(_context, _params):
            available = [tool for name, tool in tools.items() if routes[name][0].session is not None]
            return types.ListToolsResult(tools=[*builtins.values(), *available])

        async def call_tool(_context, params):
            # Both backends work on stateful Multisim sessions.
            async with call_lock:
                arguments = params.arguments or {}
                if params.name == STATUS_TOOL.name:
                    reachable = await asyncio.gather(*(backend.ping() for backend in backends.values()))
                    return tool_result({
                        name: {**backend.status(), "reachable": connected}
                        for (name, backend), connected in zip(backends.items(), reachable)
                    })
                if params.name == VERIFY_LOGIC_TOOL.name:
                    if not await backends["com"].ping():
                        return tool_result({"error": "COM backend unavailable", "backend": "com",
                                            "stage": backends["com"].stage}, is_error=True)
                    return tool_result(await verify_logic_cases(backends["com"], arguments))
                if params.name == HANDOFF_TOOL.name:
                    return tool_result(await handoff_to_com(backends["native"], backends["com"], arguments))
                if params.name not in routes:
                    return tool_result({"error": f"Unknown tool: {params.name}"}, is_error=True)
                backend, upstream = routes[params.name]
                try:
                    return await backend.call_tool(
                        upstream, params.arguments, read_timeout_seconds=600,
                    )
                except (RuntimeError, MCPError) as error:
                    return tool_result({"error": str(error), "backend": backend.name,
                                        "stage": backend.stage}, is_error=True)

        def input_schema(name):
            tool = builtins.get(name) or tools.get(name)
            return tool.input_schema if tool is not None else None

        server = Server(
            "multisim-mcp",
            version=version("multisim-unified-mcp"),
            instructions=(
                "Use native tools for library search, schematic editing and interactive simulation. "
                "Use handoff_to_com to stop, save and release the active native document, then open "
                "that exact saved file in COM. Close the COM connection before native edits. "
                "Use gateway_status for backend availability. Healthy backend tools remain usable "
                "when the other backend cannot start. "
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
    parser.add_argument("--doctor", action="store_true", help="Print path-free installation diagnostics")
    args = parser.parse_args()
    try:
        with args.config.open("rb") as file:
            config = tomllib.load(file)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        if args.doctor:
            print(json.dumps({"ok": False, "reason": "configuration_unreadable_or_invalid"}))
            raise SystemExit(1)
        parser.error("Could not read the backend configuration TOML")

    if args.doctor:
        from .doctor import diagnose

        report = asyncio.run(diagnose(configured_backends(config)))
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["ok"] else 1)
    asyncio.run(serve(configured_backends(config)))
