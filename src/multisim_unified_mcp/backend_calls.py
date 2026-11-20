"""Read the result of an MCP call used inside a gateway workflow."""

import json

from mcp import types
from mcp.shared.exceptions import MCPError

from .backends import Backend


class BackendCallError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def backend_result(session: Backend, tool: str, **arguments) -> dict:
    try:
        result = await session.call_tool(tool, arguments, read_timeout_seconds=60)
    except MCPError as error:
        code = {
            types.CONNECTION_CLOSED: "backend_disconnected",
            types.REQUEST_TIMEOUT: "backend_timeout",
        }.get(error.code, "backend_call_failed")
        raise BackendCallError(code, str(error)) from error
    except TimeoutError as error:
        raise BackendCallError("backend_timeout", f"{tool} timed out") from error
    if result.is_error:
        details = "\n".join(item.text for item in result.content if item.type == "text")
        raise RuntimeError(details or f"{tool} failed")
    data = result.structured_content
    if data is None and result.content and result.content[0].type == "text":
        try:
            data = json.loads(result.content[0].text)
        except ValueError:
            pass
    if not isinstance(data, dict):
        raise BackendCallError("invalid_backend_result", f"{tool} returned no result object")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data
