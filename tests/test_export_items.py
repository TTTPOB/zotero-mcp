import json
from types import SimpleNamespace

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.errors import LibraryError
from zotero_mcp.server import build_server

CSL = [{"id": "citation-key", "type": "article-journal"}]


@pytest.mark.parametrize("format", ["bibtex", "ris", "csljson"])
@pytest.mark.parametrize("count", [1, 50])
def test_export_uses_top_with_explicit_limit(library, zot, format, count):
    keys = [f"KEY{i:05d}" for i in range(count)]
    zot.top.return_value = json.dumps(CSL).encode()
    result = library.export_items(keys, format=format)
    zot.top.assert_called_once_with(itemKey=",".join(keys), format=format, limit=count)
    zot.items.assert_not_called()
    assert result == {
        "format": format,
        "content": CSL if format == "csljson" else zot.request.text,
    }


@pytest.mark.parametrize("raw", [json.dumps(CSL).encode(), json.dumps(CSL), CSL])
def test_csljson_export_returns_parsed_array(library, zot, raw):
    zot.top.return_value = raw
    assert library.export_items(["ABCD1234"], format="csljson")["content"] == CSL


def test_csljson_export_rejects_invalid_json(library, zot):
    zot.top.return_value = b"not json"
    with pytest.raises(LibraryError) as caught:
        library.export_items(["ABCD1234"], format="csljson")
    assert caught.value.code == "CITATION_DATA_INVALID"


@pytest.mark.parametrize("keys", [[], ["ABCD1234"] * 51])
async def test_export_rejects_out_of_range_count(library, zot, keys):
    async with create_connected_server_and_client_session(build_server(library)) as session:
        result = await session.call_tool("zotero_export_items", {"item_keys": keys})
    assert result.isError
    assert result.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
    zot.top.assert_not_called()


def exported(zot, count, text="@article{x}"):
    """Zotero reports how many of the requested items it exported in Total-Results."""
    zot.request = SimpleNamespace(text=text, headers={"Total-Results": str(count)})


def stored(key, parent=None, item_type="note"):
    data = {"itemType": item_type, **({"parentItem": parent} if parent else {})}
    return {"key": key, "data": data}


def test_export_of_all_listed_keys_costs_one_request(library, zot):
    zot.top.return_value = "@article{x}"
    exported(zot, 2)
    # Duplicates are one item to Zotero.
    result = library.export_items(["ABCD1234", "SECOND02", "abcd1234"])
    assert result["content"] == "@article{x}"
    zot.items.assert_not_called()


@pytest.mark.parametrize("format", ["bibtex", "csljson"])
def test_exporting_a_child_item_fails_with_its_parent(library, zot, format):
    zot.top.return_value = b"[]"
    exported(zot, 1, text="[]")
    zot.items.return_value = [
        stored("ABCD1234", item_type="journalArticle"),
        stored("NOTE0001", "ABCD1234"),
    ]
    with pytest.raises(LibraryError) as caught:
        library.export_items(["ABCD1234", "NOTE0001"], format=format)
    error = caught.value
    assert error.code == "NOT_CITABLE"
    assert "NOTE0001 -> ABCD1234" in error.message
    assert "endpoint" not in error.message and "/items/top" not in error.message
    assert error.details == {
        "items": [{"key": "NOTE0001", "code": "NOT_CITABLE", "parent_key": "ABCD1234"}]
    }
    # One batch lookup on /items, not one request per key.
    zot.items.assert_called_once_with(itemKey="ABCD1234,NOTE0001", limit=100)


def test_exporting_an_unknown_key_fails_as_not_found(library, zot):
    zot.top.return_value = ""
    exported(zot, 0, text="\n\n")
    zot.items.return_value = []
    with pytest.raises(LibraryError) as caught:
        library.export_items(["ZZZZZZZZ"])
    assert caught.value.code == "NOT_FOUND"
    assert caught.value.details == {"items": [{"key": "ZZZZZZZZ", "code": "NOT_FOUND"}]}


def test_child_and_unknown_keys_are_all_reported(library, zot):
    zot.top.return_value = ""
    exported(zot, 0, text="")
    zot.items.return_value = [stored("PDF00001", "ABCD1234", "attachment")]
    with pytest.raises(LibraryError) as caught:
        library.export_items(["PDF00001", "ZZZZZZZZ"], format="ris")
    assert caught.value.code == "NOT_CITABLE"
    assert caught.value.details["items"] == [
        {"key": "PDF00001", "code": "NOT_CITABLE", "parent_key": "ABCD1234"},
        {"key": "ZZZZZZZZ", "code": "NOT_FOUND"},
    ]


def test_export_without_total_results_header_is_not_second_guessed(library, zot):
    zot.top.return_value = "@article{x}"
    assert library.export_items(["ABCD1234", "SECOND02"])["content"] == zot.request.text
    zot.items.assert_not_called()


async def test_export_error_reaches_the_client_with_details(library, zot):
    zot.top.return_value = ""
    exported(zot, 0, text="\n\n")
    zot.items.return_value = [stored("NOTE0001", "ABCD1234")]
    async with create_connected_server_and_client_session(build_server(library)) as session:
        result = await session.call_tool(
            "zotero_export_items", {"item_keys": ["NOTE0001"], "format": "bibtex"}
        )
    assert result.isError
    error = result.structuredContent["error"]
    assert error["code"] == "NOT_CITABLE"
    assert error["details"]["items"][0]["parent_key"] == "ABCD1234"
