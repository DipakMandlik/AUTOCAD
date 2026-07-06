"""MCP stdio server entrypoint.

Wires config -> CAD backend -> planner/validator -> the tool registry.
`list_tools`/`call_tool` and the `process_command` tool all dispatch
through the same `TOOL_REGISTRY` (see `apps/server/tools.py`), so there is
exactly one implementation of each drawing operation, unlike the original
repo's duplicated `process_command`/`handle_call_tool` branches.
"""

from __future__ import annotations

import asyncio
import json
import logging

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from apps.context import ServerContext, build_context
from apps.server.tools import TOOL_REGISTRY, TOOLS_BY_NAME
from config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("cad_mcp.log", encoding="utf-8")],
)
logger = logging.getLogger("cad_mcp_server")


def build_mcp_server(settings: Settings, ctx: ServerContext) -> Server:
    server = Server(settings.server.name)

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=tool.name, description=tool.description, inputSchema=tool.input_schema)
            for tool in TOOL_REGISTRY
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            error = {"success": False, "message": f"unknown tool '{name}'"}
            return [types.TextContent(type="text", text=json.dumps(error))]
        try:
            result = tool.handler(arguments or {}, ctx)
        except Exception as exc:  # noqa: BLE001 - a bad tool call must not crash the server
            logger.exception("tool '%s' raised an unhandled exception", name)
            result = {"success": False, "message": f"internal error: {exc}"}
        return [types.TextContent(type="text", text=json.dumps(result))]

    return server


async def main() -> None:
    settings = Settings.load()
    logger.info(
        "Starting %s v%s (backend=%s)", settings.server.name, settings.server.version, settings.cad.backend
    )
    ctx = build_context(settings)
    server = build_mcp_server(settings, ctx)

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        logger.info("Serving over stdio")
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=settings.server.name,
                server_version=settings.server.version,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
