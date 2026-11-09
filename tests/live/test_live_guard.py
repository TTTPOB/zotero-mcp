"""Authorization and the disposable-environment guard."""

from pyzotero import Zotero

from zotero_mcp.client import Library
from zotero_mcp.config import load_auth
from zotero_mcp.server import build_server


def test_authorization_file_matches_the_running_zotero(zotero_env):
    """scripts/zotero-test-env start writes the same file `zotero-mcp authorize` would."""
    auth = load_auth()
    assert auth is not None
    assert auth.server_id == zotero_env["server_id"]
    assert auth.key == zotero_env["key"]


def test_write_with_an_unknown_key_is_unauthorized(zotero_env):
    def factory():
        return Zotero(
            "0",
            "user",
            local=True,
            server_id=zotero_env["server_id"],
            local_api_key="0" * 32,
        )

    library = Library(factory)
    try:
        result = library.add_items([{"itemType": "journalArticle", "title": "never written"}])
    except Exception as exc:
        assert getattr(exc, "code", None) == "UNAUTHORIZED", exc
    else:
        raise AssertionError(f"expected UNAUTHORIZED, got {result}")
    finally:
        library.close()


def test_every_registered_tool_is_covered_by_a_live_test():
    """Keep the live suite in step with the tool list."""
    import asyncio
    import pathlib

    tools = asyncio.run(build_server(Library(lambda: None)).list_tools())
    source = "".join(p.read_text() for p in pathlib.Path(__file__).parent.glob("test_live_*.py"))
    missing = [t.name for t in tools if f'"{t.name.removeprefix("zotero_")}"' not in source]
    assert not missing, missing
