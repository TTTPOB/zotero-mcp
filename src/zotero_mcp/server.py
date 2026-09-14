"""Official MCP SDK, stdio transport and a fixed, small tool list."""

import json
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from zotero_mcp.client import Library
from zotero_mcp.errors import from_exception

READ_TOOLS = (
    "search_items",
    "get_item_metadata",
    "get_item_children",
    "get_collections",
    "get_collection_items",
    "get_tags",
    "get_recent",
    "get_notes",
    "get_annotations",
    "get_item_fulltext",
    "find_item_fulltext",
    "get_item_schema",
    "export_items",
)
WRITE_TOOLS = (
    "add_items",
    "update_item",
    "delete_item",
    "create_note",
    "create_collection",
    "update_collection",
    "delete_collection",
    "batch_update_tags",
    "manage_collection_items",
    "attach_file",
)
CREATE_TOOLS = {"add_items", "create_note", "create_collection", "attach_file"}


class ZoteroMCP(FastMCP):
    """FastMCP boundary that keeps library errors structured and actionable."""

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        try:
            result = await super().call_tool(name, arguments)
            if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
                # Keep the legacy text channel a serialization of structuredContent,
                # including model defaults added during output validation.
                structured = result[1]
                return (
                    [
                        TextContent(
                            type="text",
                            text=json.dumps(structured, ensure_ascii=False, separators=(",", ":")),
                        )
                    ],
                    structured,
                )
            return result
        except Exception as exc:
            # FastMCP wraps function and input-validation failures in ToolError.
            # Convert at this boundary so the low-level server emits isError=true
            # while retaining a schema-compatible structuredContent object.
            error = from_exception(
                exc, write=name.removeprefix("zotero_") in WRITE_TOOLS, operation=name
            )
            payload = error.as_dict()
            if name.removeprefix("zotero_") in WRITE_TOOLS:
                structured = {
                    "ok": False,
                    "status": "failed",
                    "error": payload["error"],
                }
            else:
                structured = payload
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(structured, ensure_ascii=False, separators=(",", ":")),
                    )
                ],
                structuredContent=structured,
                isError=True,
            )


def build_server(
    library: Library | None = None, *, host: str = "127.0.0.1", port: int = 8000
) -> FastMCP:
    library = library or Library()

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            library.close()

    mcp = ZoteroMCP(
        "Zotero Local",
        instructions=(
            "Use Zotero's local library. Read metadata to obtain local versions before edits or "
            "deletions. Never substitute cloud versions. Batch writes may partially succeed: "
            "inspect successful/failed/pending entries before retrying. Writes require local "
            "authorization via zotero-mcp authorize. Fulltext comes from Zotero's own index."
        ),
        lifespan=lifespan,
        host=host,
        port=port,
    )
    for name in READ_TOOLS + WRITE_TOOLS:
        readonly = name in READ_TOOLS
        mcp.add_tool(
            getattr(library, name),
            name=f"zotero_{name}",
            structured_output=True,
            annotations=ToolAnnotations(
                readOnlyHint=readonly,
                destructiveHint=not readonly and name not in CREATE_TOOLS,
                idempotentHint=readonly,
                openWorldHint=False,
            ),
        )
    return mcp
