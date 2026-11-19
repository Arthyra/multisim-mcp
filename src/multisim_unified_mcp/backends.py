"""Own one independent MCP connection for each Multisim backend."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TextIO

from mcp import ClientSession, StdioServerParameters, stdio_client, types
from mcp.shared.exceptions import MCPError


class Backend:
    def __init__(self, name: str, config, required_tools: tuple[str, ...]):
        self.name = name
        self.config = config
        self.required_tools = required_tools
        self.stage = "configuration"
        self.reason = None
        self.version = None
        self.missing_tools: list[str] = []
        self.tools: dict[str, types.Tool] = {}
        self.session: ClientSession | None = None
        self.ready = asyncio.Event()

    def parameters(self) -> StdioServerParameters:
        params = StdioServerParameters.model_validate(self.config)
        if self.name == "native":
            worker = Path(__file__).resolve().with_name("native_worker.py")
            params = params.model_copy(update={"args": [*params.args, str(worker)]})
        return params

    def status(self) -> dict:
        return {
            "available": self.session is not None,
            "stage": self.stage,
            "reason": self.reason,
            "version": self.version,
            "missing_tools": self.missing_tools,
        }

    async def run(self, stop: asyncio.Event, stderr: TextIO) -> None:
        try:
            params = self.parameters()
            self.stage = "launch"
            async with stdio_client(params, errlog=stderr) as streams:
                self.stage = "initialize"
                async with ClientSession(*streams, read_timeout_seconds=20) as session:
                    info = await session.initialize()
                    self.version = info.server_info.version
                    self.stage = "discover_tools"
                    cursor = None
                    while True:
                        page = await session.list_tools(
                            params=types.PaginatedRequestParams(cursor=cursor) if cursor else None
                        )
                        self.tools.update((tool.name, tool) for tool in page.tools)
                        cursor = page.next_cursor
                        if cursor is None:
                            break
                    self.missing_tools = sorted(set(self.required_tools) - self.tools.keys())
                    if self.missing_tools:
                        self.reason = "missing_required_tools"
                        return
                    self.session = session
                    self.stage = "ready"
                    self.ready.set()
                    await stop.wait()
        except Exception as error:
            # Exception text from a child can include local paths or its environment.
            self.reason = type(error).__name__
            if self.stage == "ready":
                self.stage = "disconnected"
        finally:
            self.session = None
            self.ready.set()

    async def call_tool(self, name, arguments=None, *, read_timeout_seconds=60):
        session = self.session
        if session is None:
            raise RuntimeError(f"{self.name} backend unavailable ({self.stage})")
        try:
            return await session.call_tool(name, arguments, read_timeout_seconds=read_timeout_seconds)
        except MCPError as error:
            if error.code == types.CONNECTION_CLOSED:
                self.mark_disconnected()
            raise

    async def ping(self) -> bool:
        session = self.session
        if session is None:
            return False
        try:
            await session.send_ping()
        except MCPError as error:
            if error.code == types.CONNECTION_CLOSED:
                self.mark_disconnected()
            return False
        return True

    def mark_disconnected(self) -> None:
        self.session = None
        self.stage = "disconnected"
        self.reason = "connection_closed"


@asynccontextmanager
async def start_backends(backends: dict[str, Backend], stderr: TextIO):
    stop = asyncio.Event()
    # Child transports own AnyIO task groups. Each backend enters and exits its
    # group in a dedicated task, so a child failure cannot cancel its sibling.
    tasks = [asyncio.create_task(backend.run(stop, stderr)) for backend in backends.values()]
    try:
        await asyncio.gather(*(backend.ready.wait() for backend in backends.values()))
        yield
    finally:
        stop.set()
        await asyncio.gather(*tasks)
