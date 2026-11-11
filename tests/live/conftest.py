"""Live tests against a disposable Zotero started by scripts/zotero-test-env.

Everything here is skipped unless ZOTERO_LIVE=1. Even then, the tests refuse to run
unless the Zotero answering on localhost:23119 reports the server ID that the
environment script recorded when it created the disposable library.
"""

import asyncio
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from live_support import PDF_NEEDLE, TEXT_NEEDLE, minimal_pdf
from mcp.shared.memory import create_connected_server_and_client_session
from pyzotero import Zotero

from zotero_mcp.client import Library
from zotero_mcp.errors import LibraryError
from zotero_mcp.server import build_server

LIVE = os.environ.get("ZOTERO_LIVE") == "1"
API_ROOT = "http://localhost:23119/api"


def pytest_collection_modifyitems(config, items):
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live tests need a disposable Zotero (set ZOTERO_LIVE=1)")
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.path)).parents:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_network():
    """Override tests/conftest.py: live tests talk to the local Zotero over HTTP."""


@pytest.fixture(scope="session")
def zotero_env():
    """Credentials of the disposable Zotero, after proving it is the disposable one."""
    server_id = os.environ.get("ZOTERO_TEST_SERVER_ID", "")
    api_key = os.environ.get("ZOTERO_TEST_API_KEY", "")
    if not server_id or not api_key:
        pytest.exit(
            "Live tests need ZOTERO_TEST_SERVER_ID and ZOTERO_TEST_API_KEY; "
            "run them with scripts/zotero-test-env test.",
            returncode=2,
        )
    request = urllib.request.Request(f"{API_ROOT}/users/0/items?limit=1")
    with urllib.request.urlopen(request, timeout=10) as response:
        actual = response.headers.get("Zotero-Server-ID")
    if actual != server_id:
        pytest.exit(
            f"Refusing to run: the Zotero on localhost:23119 has server ID {actual!r}, "
            f"not the disposable one ({server_id!r}).",
            returncode=2,
        )
    return {"server_id": server_id, "key": api_key}


@pytest.fixture(scope="session")
def library(zotero_env):
    def factory():
        return Zotero(
            "0",
            "user",
            local=True,
            server_id=zotero_env["server_id"],
            local_api_key=zotero_env["key"],
        )

    library = Library(factory)
    yield library
    library.close()


@pytest.fixture(scope="session")
def mcp(library):
    """Call a tool through the MCP protocol (schemas and serialization included)."""

    async def _call(name, arguments):
        async with create_connected_server_and_client_session(
            build_server(library), read_timeout_seconds=timedelta(seconds=120)
        ) as session:
            return await session.call_tool(f"zotero_{name}", arguments)

    def call(name, arguments=None, *, error=False):
        result = asyncio.run(_call(name, arguments or {}))
        assert result.isError is error, result.content[0].text
        # The text channel must serialize the structured result.
        assert json.loads(result.content[0].text) == result.structuredContent
        return result.structuredContent

    return call


@dataclass(frozen=True)
class Seed:
    alpha: str  # journal article with DOI, citation key, tags, in `parent`
    beta: str  # journal article with DOI, tags, and a text attachment
    pdf: str  # imported PDF attachment of alpha
    text: str  # text attachment of beta
    note: str  # child note of alpha
    annotation: str  # highlight annotation on the PDF
    parent: str  # collection
    child: str  # sub-collection of `parent`


def _only_key(result) -> str:
    assert result["ok"], result
    (entry,) = result["results"]
    return entry["key"]


@pytest.fixture(scope="session")
def seed(library, tmp_path_factory):
    parent = _only_key(library.create_collection("Live parent"))
    child = _only_key(library.create_collection("Live child", parent))
    created = library.add_items(
        [
            {
                "itemType": "journalArticle",
                "title": "Live alpha paper",
                "DOI": "10.5555/live.alpha",
                "citationKey": "livealpha2024",
                "date": "2024",
                "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Live"}],
                "tags": [{"tag": "live-seed"}, {"tag": "live-alpha"}],
                "collections": [parent],
            },
            {
                "itemType": "journalArticle",
                "title": "Live beta paper",
                "DOI": "10.5555/live.beta",
                "date": "2023",
                "tags": [{"tag": "live-seed"}],
            },
        ]
    )
    alpha, beta = (entry["key"] for entry in created["results"])
    note = _only_key(library.create_note(alpha, "Live seeded note", tags=["live-note"]))

    files = tmp_path_factory.mktemp("live-files")
    (files / "paper.pdf").write_bytes(minimal_pdf(f"{PDF_NEEDLE} indexed words"))
    (files / "notes.txt").write_text(f"{TEXT_NEEDLE} plain text attachment\n", encoding="utf-8")
    pdf = library.attach_file(alpha, str(files / "paper.pdf"))
    text = library.attach_file(beta, str(files / "notes.txt"))
    assert pdf["ok"] and text["ok"], (pdf, text)

    annotation = _only_key(
        library.add_items(
            [
                {
                    "itemType": "annotation",
                    "parentItem": pdf["key"],
                    "annotationType": "highlight",
                    "annotationText": "live highlighted text",
                    "annotationColor": "#ffd400",
                    "annotationPageLabel": "1",
                    "annotationSortIndex": "00000|000000|00000",
                    "annotationPosition": json.dumps(
                        {"pageIndex": 0, "rects": [[20, 100, 120, 118]]}
                    ),
                }
            ]
        )
    )

    # Fulltext indexing of the PDF is asynchronous.
    deadline = time.monotonic() + 60
    while True:
        try:
            library.get_item_fulltext(alpha, attachment_key=pdf["key"])
            break
        except LibraryError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)

    return Seed(
        alpha=alpha,
        beta=beta,
        pdf=pdf["key"],
        text=text["key"],
        note=note,
        annotation=annotation,
        parent=parent,
        child=child,
    )


@pytest.fixture
def new_item(mcp):
    """Create a throwaway top-level article through MCP and return its key and version."""
    counter = iter(range(1_000_000))

    def create(title=None, **fields):
        title = title or f"Live scratch item {next(counter)}"
        result = mcp(
            "add_items", {"items": [{"itemType": "journalArticle", "title": title, **fields}]}
        )
        key = result["results"][0]["key"]
        version = result["results"][0]["version"]
        return key, version

    return create
