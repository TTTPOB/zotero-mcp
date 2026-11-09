import copy
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server


def citation_item(key="ABCD1234", library_type="user", library_id=123):
    return {
        "key": key,
        "library": {"type": library_type, "id": library_id},
        "data": {"itemType": "journalArticle", "title": "中文文献", "citationKey": "Paper2026"},
        "links": {"alternate": {"href": "https://www.zotero.org/users/999/items/ABCD1234"}},
        "csljson": json.dumps(
            [{"id": "Paper2026", "type": "article-journal", "title": "中文文献"}]
        ),
    }


@pytest.mark.parametrize(
    "library_type,library_id,scope,select_scope",
    [
        ("user", 123, "users/123", "library"),
        ("user", 0, "users/0", "library"),
        ("group", 456, "groups/456", "groups/456"),
    ],
)
def test_citation_uris_use_item_library(
    library, zot, library_type, library_id, scope, select_scope
):
    zot.local_api_key = None
    item = citation_item(library_type=library_type, library_id=library_id)
    original = copy.deepcopy(item)
    zot.top.return_value = [item]
    result = library.get_citation_items(["abcd1234"])["items"][0]
    assert result == {
        "index": 0,
        "key": "ABCD1234",
        "status": "ok",
        "uri": f"http://zotero.org/{scope}/items/ABCD1234",
        "select_link": f"zotero://select/{select_scope}/items/ABCD1234",
        "citation_key": "Paper2026",
        "item_type": "journalArticle",
        "title": "中文文献",
        "csl": json.loads(item["csljson"])[0],
    }
    assert item == original
    zot.top.assert_called_once_with(itemKey="ABCD1234", include="data,csljson", limit=1)
    zot.items.assert_not_called()
    zot.item.assert_not_called()


def test_citation_preserves_input_order_duplicates_and_missing_keys(library, zot):
    zot.top.return_value = [citation_item(), citation_item("SECOND02")]
    keys = ["SECOND02", "MISSING1", "ABCD1234", "SECOND02"]
    entries = library.get_citation_items(keys)["items"]
    assert [e["key"] for e in entries] == keys
    assert [e["index"] for e in entries] == list(range(4))
    assert [e["status"] for e in entries] == ["ok", "error", "ok", "ok"]
    assert entries[1]["error"]["code"] == "NOT_FOUND"
    assert "/items/top" not in entries[1]["error"]["message"]
    assert "parent_key" not in entries[1]
    assert "uri" not in entries[1]
    zot.top.assert_called_once_with(itemKey=",".join(keys), include="data,csljson", limit=4)
    # The missing key is looked up once, in a batch that also covers child items.
    zot.items.assert_called_once_with(itemKey="MISSING1", limit=100)


@pytest.mark.parametrize("item_type", ["note", "attachment", "annotation"])
def test_standalone_non_citable_item(library, zot, item_type):
    item = citation_item()
    item["data"]["itemType"] = item_type
    item.pop("csljson")
    zot.top.return_value = [item]
    entry = library.get_citation_items(["ABCD1234"])["items"][0]
    assert entry["status"] == "error"
    assert entry["error"]["code"] == "NOT_CITABLE"


def stored_child(key, parent, item_type):
    return {"key": key, "data": {"itemType": item_type, "parentItem": parent}}


@pytest.mark.parametrize("item_type", ["note", "attachment"])
def test_child_note_or_attachment_points_to_its_parent(library, zot, item_type):
    zot.top.return_value = [citation_item()]
    zot.items.return_value = [stored_child("CHILD001", "ABCD1234", item_type)]
    entries = library.get_citation_items(["ABCD1234", "CHILD001"])["items"]
    assert entries[0]["status"] == "ok"
    entry = entries[1]
    assert entry["status"] == "error"
    assert entry["error"]["code"] == "NOT_CITABLE"
    assert entry["parent_key"] == "ABCD1234"
    assert f"Child {item_type} of ABCD1234" in entry["error"]["message"]
    assert "use the parent item key ABCD1234" in entry["error"]["message"]
    assert "/items/top" not in entry["error"]["message"]
    zot.items.assert_called_once_with(itemKey="CHILD001", limit=100)


def test_annotation_points_to_its_attachment(library, zot):
    zot.top.return_value = []
    zot.items.return_value = [stored_child("ANNOT001", "PDF00001", "annotation")]
    entry = library.get_citation_items(["ANNOT001"])["items"][0]
    assert entry["error"]["code"] == "NOT_CITABLE"
    assert entry["parent_key"] == "PDF00001"
    assert "Annotation of attachment PDF00001" in entry["error"]["message"]


def test_all_missing_keys_are_classified_with_one_batch_request(library, zot):
    zot.top.return_value = []
    # Zotero also returns the children of every match; unrequested items are ignored.
    zot.items.return_value = [
        stored_child("CHILD001", "ABCD1234", "note"),
        stored_child("OTHER001", "ABCD1234", "note"),
    ]
    keys = ["CHILD001", "ZZZZZZZZ", "CHILD001", "KL642QCB"]
    entries = library.get_citation_items(keys)["items"]
    assert [e["error"]["code"] for e in entries] == [
        "NOT_CITABLE",
        "NOT_FOUND",
        "NOT_CITABLE",
        "NOT_FOUND",
    ]
    assert [e.get("parent_key") for e in entries] == ["ABCD1234", None, "ABCD1234", None]
    zot.items.assert_called_once_with(itemKey="CHILD001,KL642QCB,ZZZZZZZZ", limit=100)


@pytest.mark.parametrize("representation", ["string", "list", "dict"])
def test_csl_representations_are_tolerated_and_preserved(library, zot, representation):
    item = citation_item()
    csl = {"id": "NotTheItemKey", "citation-key": "Fallback2026", "custom": {"x": [1, 2]}}
    item["data"]["citationKey"] = ""
    item["csljson"] = {"string": json.dumps([csl]), "list": [csl], "dict": csl}[representation]
    zot.top.return_value = [item]
    entry = library.get_citation_items(["ABCD1234"])["items"][0]
    assert entry["csl"] == csl
    assert entry["citation_key"] == "Fallback2026"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_citation_key_is_invented(library, zot, value):
    item = citation_item()
    item["data"]["citationKey"] = value
    zot.top.return_value = [item]
    assert library.get_citation_items(["ABCD1234"])["items"][0]["citation_key"] is None


@pytest.mark.parametrize("csl", [None, "invalid", "null", "[]", "[{}, {}]", "[1]", [], [{}, {}], 1])
def test_invalid_csl_is_per_key_error(library, zot, csl):
    item = citation_item()
    item["csljson"] = csl
    zot.top.return_value = [item, citation_item("SECOND02")]
    entries = library.get_citation_items(["ABCD1234", "SECOND02"])["items"]
    assert entries[0]["error"]["code"] == "CITATION_DATA_INVALID"
    assert entries[1]["status"] == "ok"


@pytest.mark.parametrize("keys", [[], ["ABCD1234"] * 51])
def test_citation_count_is_bounded(library, zot, keys):
    with pytest.raises(ValueError, match="1 to 50"):
        library.get_citation_items(keys)
    zot.top.assert_not_called()


def test_citation_requests_all_fifty_keys(library, zot):
    keys = [f"KEY{i:05d}" for i in range(50)]
    zot.top.return_value = [citation_item(k) for k in reversed(keys)]
    assert len(library.get_citation_items(keys)["items"]) == 50
    zot.top.assert_called_once_with(itemKey=",".join(keys), include="data,csljson", limit=50)


async def test_citation_mcp_contract(library, zot):
    zot.top.return_value = [citation_item()]
    async with create_connected_server_and_client_session(build_server(library)) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
        tool = tools["zotero_get_citation_items"]
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.destructiveHint is False
        parameter = tool.inputSchema["properties"]["item_keys"]
        assert parameter["minItems"] == 1
        assert parameter["maxItems"] == 50
        assert parameter["description"]
        assert tool.outputSchema["required"] == ["items"]
        result = await session.call_tool(
            "zotero_get_citation_items", {"item_keys": ["ABCD1234", "MISSING1"]}
        )
        zot.items.return_value = [stored_child("CHILD001", "ABCD1234", "note")]
        child = await session.call_tool("zotero_get_citation_items", {"item_keys": ["CHILD001"]})
        invalid = await session.call_tool("zotero_get_citation_items", {"item_keys": []})
    assert not result.isError
    assert result.structuredContent["items"][0]["csl"]["id"] == "Paper2026"
    assert result.structuredContent["items"][1]["error"]["code"] == "NOT_FOUND"
    assert "uri" not in result.structuredContent["items"][1]
    assert json.loads(result.content[0].text) == result.structuredContent
    assert child.structuredContent["items"][0]["parent_key"] == "ABCD1234"
    assert child.structuredContent["items"][0]["error"]["code"] == "NOT_CITABLE"
    assert invalid.isError
    assert invalid.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
