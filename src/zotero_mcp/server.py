"""Official MCP SDK, stdio transport and a fixed, small tool list."""

from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from zotero_mcp.client import Library

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

    mcp = FastMCP(
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
                destructiveHint=not readonly,
                idempotentHint=readonly,
                openWorldHint=False,
            ),
        )
    return mcp
