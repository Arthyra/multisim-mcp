"""Read the result of an MCP call used inside a gateway workflow."""

import json

from mcp.shared.exceptions import MCPError

from .backends import Backend


async def backend_result(session: Backend, tool: str, **arguments):
    try:
        result = await session.call_tool(tool, arguments, read_timeout_seconds=60)
    except MCPError as error:
        raise RuntimeError(str(error)) from error
    if result.is_error:
        details = "\n".join(item.text for item in result.content if item.type == "text")
        raise RuntimeError(details or f"{tool} failed")
    data = result.structured_content
    if data is None:
        data = json.loads(result.content[0].text)
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"])
    return data
