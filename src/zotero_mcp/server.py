"""Official MCP SDK, stdio transport and a fixed, small tool list."""

import json
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from zotero_mcp.client import Library
from zotero_mcp.errors import UNKNOWN_WRITE_RESULT, from_exception

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
    "read_attachment_text",
    "get_item_schema",
    "export_items",
    "get_citation_items",
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
    "attach_markdown",
)
CREATE_TOOLS = {
    "add_items",
    "create_note",
    "create_collection",
    "attach_file",
    "attach_markdown",
}


class ZoteroMCP(FastMCP):
    """FastMCP boundary that keeps library errors structured and actionable."""

    def __init__(self, library: Library, *args, **kwargs):
        self._library = library
        self._http_app_created = False
        super().__init__(*args, lifespan=self._lifespan, **kwargs)

    @asynccontextmanager
    async def _lifespan(self, _server):
        try:
            yield {}
        finally:
            # Streamable HTTP runs this context once per request in stateless mode.
            if not self._http_app_created:
                self._library.close()

    def streamable_http_app(self):
        app = super().streamable_http_app()
        self._http_app_created = True
        server_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            try:
                async with server_lifespan(app):
                    yield
            finally:
                self._library.close()

        app.router.lifespan_context = lifespan
        return app

    async def run_stdio_async(self):
        try:
            await super().run_stdio_async()
        finally:
            self._library.close()

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        try:
            result = await super().call_tool(name, arguments)
            if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
                # Keep text and structuredContent identical after output validation.
                structured = result[1]
                text = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
                status = structured.get("status")
                # A write that failed entirely must not look like a success to clients that
                # only read isError; structuredContent still carries the per-item results.
                if status == "unknown" or (
                    status == "failed" and name.removeprefix("zotero_") in WRITE_TOOLS
                ):
                    return CallToolResult(
                        content=[TextContent(type="text", text=text)],
                        structuredContent=structured,
                        isError=True,
                    )
                return ([TextContent(type="text", text=text)], structured)
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
                    "status": "unknown" if error.code == UNKNOWN_WRITE_RESULT else "failed",
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


INSTRUCTIONS = """\
Tools for the user's local Zotero library. Which tool to use:
- Find a paper: zotero_search_items with title words, author or year. Pass a DOI, DOI URL or \
ISBN as the whole query for an exact identifier lookup (found=false means the library lacks it).
- Cite in Word/LibreOffice: zotero_get_citation_items and use each item's uri (never a web \
link). Pandoc/LaTeX: use citation_key and csl from the same tool, or zotero_export_items.
- Read a paper's text: zotero_get_item_fulltext and zotero_find_item_fulltext take a paper \
or attachment key as item_key. Continue with the returned attachment_key as item_key, \
plus text_revision and next_offset. zotero_find_item_fulltext locates a phrase.
- Browse: zotero_get_collections, then zotero_get_collection_items; child attachments and \
notes come from zotero_get_item_children.
- Edit: use the local version returned by a create or update, or read the item first. A known \
version is returned for the next edit; an unavailable version is omitted. On VERSION_CONFLICT, \
error.details.current_version is the current version; \
resubmit only after confirming the change still applies.
Item keys are 8 characters (e.g. ABCD2345); collection keys are not item keys. Batch writes \
may partially succeed: check each results entry's status, key, version and error; never resubmit \
successful ones. Writes \
require local authorization via zotero-mcp authorize. Fulltext comes from Zotero's own index.\
"""


def build_server(
    library: Library | None = None, *, host: str = "127.0.0.1", port: int = 8000
) -> FastMCP:
    library = library or Library()

    mcp = ZoteroMCP(
        library,
        "Zotero Local",
        instructions=INSTRUCTIONS,
        stateless_http=True,
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
