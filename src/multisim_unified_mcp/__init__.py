"""Expose selected Multisim tools through one persistent stdio MCP session."""

from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import sys
import tomllib

from anyio import CancelScope
from mcp import types
from mcp.shared.exceptions import MCPError
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from .backends import Backend, start_backends
from .f5_cases import VERIFY_F5_TOOL, verify_f5_cases
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
        "native_open_copy",
        "native_get_parameters",
        "native_check_netlist",
        "native_check_layout",
        "native_export_image",
        "native_run_analysis",
        "native_run_ac_sweep",
        "native_read_waveform",
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

REQUIRED_TOOLS = {
    "native": ("native_runtime_status", "native_open_circuit", "native_get_circuit_state"),
    "com": ("runtime_status", "connect", "open_circuit"),
}

WORKFLOW_TOOLS = {
    VERIFY_F5_TOOL.name: {
        "native": ("native_interactive_status", "native_control_interactive", "native_f5_logic_snapshot"),
    },
    VERIFY_LOGIC_TOOL.name: {"com": ("open_circuit",)},
}


STATUS_TOOL = types.Tool(
    name="gateway_status",
    description=(
        "Report backend connection state and a bounded MCP ping (reachable). "
        "A connected backend may be busy and fail to respond within 0.5 seconds. "
        "A ping does not test Multisim or open a circuit."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)


def configured_backends(config: dict) -> dict[str, Backend]:
    return {
        role: Backend(role, config.get(role), required)
        for role, required in REQUIRED_TOOLS.items()
    }


def tool_result(result, *, is_error=False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
        structured_content=result,
        is_error=is_error,
    )


async def serve(backends: dict[str, Backend]) -> None:
    async with start_backends(backends, sys.stderr):
        builtins = {tool.name: tool for tool in (STATUS_TOOL, HANDOFF_TOOL, VERIFY_LOGIC_TOOL, VERIFY_F5_TOOL)}
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
            # Keep the schemas discovered at startup valid for the entire session,
            # including for clients that cache tools/list.
            return types.ListToolsResult(tools=[*builtins.values(), *tools.values()])

        async def call_tool(_context, params):
            arguments = params.arguments or {}
            if params.name == STATUS_TOOL.name:
                probes = await asyncio.gather(*(backend.ping(timeout=0.5) for backend in backends.values()))
                statuses = {}
                for (name, backend), responded in zip(backends.items(), probes):
                    state = backend.status()
                    statuses[name] = {**state, "reachable": responded and state["available"]}
                return tool_result(statuses)
            # Both backends work on stateful Multisim sessions.
            async with call_lock:
                try:
                    if params.name == HANDOFF_TOOL.name:
                        return tool_result(await handoff_to_com(backends["native"], backends["com"], arguments))
                    for role, required in WORKFLOW_TOOLS.get(params.name, {}).items():
                        backend = backends[role]
                        missing = [name for name in required if not backend.supports(name)]
                        if missing:
                            return tool_result({
                                "error": f"{role} workflow tools unavailable", "backend": role,
                                "stage": backend.stage, "missing_tools": missing,
                                "code": "tool_unavailable" if backend.available() else "backend_unavailable",
                            }, is_error=True)
                    if params.name == VERIFY_LOGIC_TOOL.name:
                        return tool_result(await verify_logic_cases(backends["com"], arguments))
                    if params.name == VERIFY_F5_TOOL.name:
                        return tool_result(await verify_f5_cases(backends["native"], arguments))
                except (ValidationError, ValueError) as error:
                    return tool_result({"error": str(error), "code": "invalid_arguments"}, is_error=True)
                if params.name not in routes:
                    return tool_result({"error": f"Unknown tool: {params.name}"}, is_error=True)
                backend, upstream = routes[params.name]
                if not backend.available():
                    return tool_result({
                        "error": f"{backend.name} backend unavailable ({backend.stage})",
                        "code": "backend_unavailable", "backend": backend.name, "stage": backend.stage,
                    }, is_error=True)
                if params.name not in tools:
                    return tool_result({
                        "error": f"{backend.name} backend does not offer {upstream}",
                        "code": "tool_unavailable", "backend": backend.name, "stage": backend.stage,
                    }, is_error=True)
                try:
                    # Do not release the shared lock while a dispatched write is in flight.
                    with CancelScope(shield=True):
                        return await backend.call_tool(upstream, params.arguments, read_timeout_seconds=600)
                except (RuntimeError, MCPError, TimeoutError) as error:
                    code = "backend_call_failed" if backend.available() else "backend_unavailable"
                    return tool_result({
                        "error": str(error), "backend": backend.name, "stage": backend.stage, "code": code,
                    }, is_error=True)

        def input_schema(name):
            tool = builtins.get(name) or tools.get(name)
            return tool.input_schema if tool is not None else None

        server = Server(
            "multisim-mcp",
            version=version("multisim-unified-mcp"),
            instructions=(
                "Follow the requested circuit, textbook method and part models. Use a working copy "
                "before editing an original. Use Native for editing and supported simulations. "
                "Verify electrical connectivity with native nets or a netlist and inspect layout separately. "
                "Use native_verify_f5_cases to compare real indicators with the expected truth table. "
                "Use native_run_analysis or native_run_ac_sweep and native_read_waveform for numerical analysis. "
                "Use handoff_to_com when COM is required or for a cross-check. It stops, saves and releases "
                "the active file and opens it in COM. Disconnect COM before returning to Native editing."
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
