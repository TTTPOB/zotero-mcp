"""Exercise real pyzotero serialization against an in-memory HTTP transport."""

import json

import httpx2 as http
import pytest
from pyzotero import Zotero

from zotero_mcp.client import Library


@pytest.fixture
def wire():
    calls = []
    responses = []

    def handler(request):
        calls.append(request)
        assert request.url.scheme == "http"
        assert request.url.host == "localhost"
        assert request.url.port == 23119
        assert str(request.url.path).startswith("/api/")
        response = responses.pop(0)
        status, body = response[:2]
        headers = {"Zotero-Server-ID": "server-1", "Last-Modified-Version": "8"}
        if len(response) > 2:
            headers.update(response[2])
        if isinstance(body, str):
            return http.Response(status, text=body, headers=headers)
        return http.Response(status, json=body, headers=headers)

    transport = http.MockTransport(handler)
    client = http.Client(transport=transport)
    zot = Zotero(
        "0", "user", local=True, server_id="server-1", local_api_key="local-test-key", client=client
    )
    library = Library(lambda: zot)
    yield library, calls, responses
    library.close()
    assert not responses, "Unused fake responses indicate a skipped API operation."


def test_create_note_posts_only_to_native_local_items(wire):
    library, calls, responses = wire
    responses.extend(
        [(200, [{"field": "title"}]), (200, {"success": {"0": "NOTE0001"}, "failed": {}})]
    )
    assert library.create_note("ABCD1234", "中文")["ok"]
    # The first call reads the field list used to reject unknown fields; only one POST follows.
    assert [c.method for c in calls] == ["GET", "POST"]
    assert calls[0].url.path == "/api/itemFields"
    assert calls[1].url.path == "/api/users/0/items"
    assert calls[1].headers["Zotero-Server-ID"] == "server-1"
    assert calls[1].headers["Zotero-API-Key"] == "local-test-key"
    assert json.loads(calls[1].content)[0]["parentItem"] == "ABCD1234"


def test_update_sends_patch_with_version_and_local_key(wire):
    library, calls, responses = wire
    responses.extend(
        [
            (200, {"key": "ABCD1234", "version": 7, "data": {}}),
            (200, [{"field": "title"}]),
            (204, ""),
            (200, {"key": "ABCD1234", "version": 8, "data": {}}),
        ]
    )
    result = library.update_item("ABCD1234", 7, {"title": "修改标题"})
    assert result["ok"] and result["version"] == 8
    assert result["results"][0]["version"] == 8
    assert [c.method for c in calls] == ["GET", "GET", "PATCH", "GET"]
    request = calls[2]
    assert request.method == "PATCH"
    assert request.url.path == "/api/users/0/items/ABCD1234"
    assert request.headers["If-Unmodified-Since-Version"] == "7"
    assert request.headers["Zotero-Server-ID"] == "server-1"
    assert request.headers["Zotero-API-Key"] == "local-test-key"
    assert json.loads(request.content)["title"] == "修改标题"


def test_stale_patch_is_not_retried(wire):
    library, calls, responses = wire
    responses.extend(
        [
            (200, {"key": "ABCD1234", "version": 7, "data": {}}),
            (200, [{"field": "title"}]),
            (412, "Item has changed since specified version"),
            (200, {"key": "ABCD1234", "version": 9, "data": {}}),
        ]
    )
    with pytest.raises(ValueError, match="current local version") as caught:
        library.update_item("ABCD1234", 7, {"title": "new"})
    assert caught.value.details["current_version"] == 9
    # The PATCH is sent once; the last GET only reads the version for the error.
    assert [c.method for c in calls] == ["GET", "GET", "PATCH", "GET"]


def test_delete_is_conditional_native_api(wire):
    library, calls, responses = wire
    responses.append((204, ""))
    assert library.delete_item("ABCD1234", 7)["ok"]
    assert calls[0].method == "DELETE"
    assert calls[0].headers["If-Unmodified-Since-Version"] == "7"
    assert calls[0].url.path == "/api/users/0/items/ABCD1234"


def test_collection_create_stays_local(wire):
    library, calls, responses = wire
    responses.append((200, {"success": {"0": "COLL0001"}, "failed": {}}))
    assert library.create_collection("中文集合")["ok"]
    assert calls[0].url.path == "/api/users/0/collections"
    assert json.loads(calls[0].content) == [{"name": "中文集合", "parentCollection": False}]


def test_batch_list_edits_read_write_and_read_back_each_item_once(wire):
    library, calls, responses = wire
    responses.append((200, [{"field": "title"}]))
    library.zot.check_items([{"title": "warm field cache"}])
    calls.clear()
    responses.extend([
        (200, {"key": "ABCD1234", "version": 7, "data": {"tags": [{"tag": "keep", "type": 1}]}}),
        (204, ""),
        (200, {"key": "ABCD1234", "version": 12, "data": {}}),
    ])
    result = library.batch_update_tags(["ABCD1234"], add=["new"])
    assert [c.method for c in calls] == ["GET", "PATCH", "GET"]
    assert calls[1].headers["If-Unmodified-Since-Version"] == "7"
    assert json.loads(calls[1].content) == {"key": "ABCD1234", "version": 7, "tags": [{"tag": "keep", "type": 1}, {"tag": "new"}]}
    assert result["results"][0]["version"] == 12


def test_collection_add_reuses_its_child_preflight_read(wire):
    library, calls, responses = wire
    responses.append((200, [{"field": "title"}]))
    library.zot.check_items([{"title": "warm field cache"}])
    calls.clear()
    responses.extend([
        (200, {"key": "COLL0001", "version": 3, "data": {"name": "Reading"}}),
        (200, {"key": "ABCD1234", "version": 7, "data": {"collections": []}}),
        (204, ""),
        (200, {"key": "ABCD1234", "version": 12, "data": {}}),
    ])
    result = library.manage_collection_items("COLL0001", ["ABCD1234"], "add")
    assert [c.method for c in calls] == ["GET", "GET", "PATCH", "GET"]
    assert calls[0].url.path == "/api/users/0/collections/COLL0001"
    assert json.loads(calls[2].content)["collections"] == ["COLL0001"]
    assert result["results"][0]["version"] == 12


def test_native_batch_partial_failure_is_preserved(wire):
    library, calls, responses = wire
    responses.append(
        (
            200,
            {"success": {"0": "ABCD1234"}, "failed": {"1": {"code": 400, "message": "bad field"}}},
        )
    )
    responses.insert(0, (200, [{"field": "title"}]))
    result = library.add_items([{"itemType": "book"}, {"itemType": "book", "title": "bad"}])
    assert result["ok"] is False
    assert result["results"][1]["error"]["code"] == "INVALID_ARGUMENT"
    assert [c.method for c in calls] == ["GET", "POST"]


def test_search_then_metadata_does_not_leak_query(wire):
    library, calls, responses = wire
    responses.extend(
        [(200, []), (200, [{"key": "ABCD1234", "version": 7, "data": {"title": "text"}}])]
    )
    library.search_items("中文", tags=["one", "two"], limit=3, offset=6)
    library.get_item_metadata(["ABCD1234"])
    assert calls[0].url.params.get_list("tag") == ["one", "two"]
    assert calls[0].url.params["start"] == "6"
    assert "q" not in calls[1].url.params
    assert "tag" not in calls[1].url.params
    assert calls[1].url.params["itemKey"] == "ABCD1234"
    assert calls[1].url.params["limit"] == "100"
    assert calls[1].url.path.endswith("/items")


def test_parent_tag_search_preserves_search_pagination_headers(wire):
    library, calls, responses = wire
    attachment = {
        "key": "ATTACH01",
        "version": 3,
        "data": {"itemType": "attachment", "parentItem": "PARENT01", "title": "paper.pdf"},
    }
    parent = {
        "key": "PARENT01",
        "version": 4,
        "data": {"itemType": "journalArticle", "title": "Parent", "tags": [{"tag": "review"}]},
    }
    responses.extend(
        [
            (
                200,
                [attachment],
                {
                    "Total-Results": "4",
                    "Link": '<http://localhost:23119/api/users/0/items?start=3&limit=1>; rel="next"',
                },
            ),
            (200, parent, {"Total-Results": "1"}),
        ]
    )

    result = library.search_items(
        "needle", tags=["review"], qmode="everything", tag_scope="parent", limit=1
    )

    assert result["total"] == 4
    assert result["next_offset"] == 3
    assert len(result["items"]) == 1
    assert calls[0].url.path == "/api/users/0/items"
    assert "tag" not in calls[0].url.params
    assert calls[1].url.path == "/api/users/0/items/PARENT01"


def test_parent_response_without_pagination_headers_keeps_search_total(wire):
    library, calls, responses = wire
    attachment = {
        "key": "ATTACH01",
        "version": 3,
        "data": {"itemType": "attachment", "parentItem": "PARENT01", "title": "paper.pdf"},
    }
    parent = {
        "key": "PARENT01",
        "version": 4,
        "data": {"itemType": "journalArticle", "title": "Parent", "tags": [{"tag": "review"}]},
    }
    responses.extend(
        [
            (200, [attachment], {"Total-Results": "2"}),
            (200, parent),
        ]
    )

    result = library.search_items(
        "needle", tags=["review"], qmode="everything", tag_scope="parent", limit=1
    )

    assert result["total"] == 2
    assert result["next_offset"] == 1
    assert len(calls) == 2


def test_read_does_not_send_local_write_key(wire):
    library, calls, responses = wire
    responses.append((200, [{"key": "ABCD1234", "data": {}}]))
    library.get_item_metadata(["ABCD1234"])
    assert "Zotero-API-Key" not in calls[0].headers
    assert calls[0].headers["Zotero-Server-ID"] == "server-1"


def test_local_auth_uses_native_permission_endpoint(monkeypatch, tmp_path):
    from zotero_mcp import cli, config

    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "localhost"
        assert request.url.path == "/api/local/authorize"
        assert json.loads(request.content) == {"appName": "Zotero MCP (local)"}
        return http.Response(
            200,
            json={"key": "new-local-key", "remember": True},
            headers={"Zotero-Server-ID": "server-1"},
        )

    zot = Zotero(
        "0",
        "user",
        local=True,
        server_id="server-1",
        client=http.Client(transport=http.MockTransport(handler)),
    )
    import pyzotero

    monkeypatch.setattr(pyzotero, "Zotero", lambda *a, **k: zot)
    monkeypatch.setattr(config, "auth_path", lambda: tmp_path / "auth.json")
    assert cli.main(["authorize"]) == 0
    assert config.load_auth().key == "new-local-key"
    assert len(calls) == 1


def test_attachment_upload_uses_local_receiver_and_registration(wire, monkeypatch, tmp_path):
    library, calls, responses = wire
    path = tmp_path / "中文.pdf"
    path.write_bytes(b"%PDF-fake-content")
    responses.extend(
        [
            (200, {"success": {"0": "ATTACH01"}, "failed": {}}),
            (
                200,
                {
                    "url": "http://localhost:23119/api/local/upload/test",
                    "params": {"key": "upload-test"},
                    "uploadKey": "registration-test",
                },
            ),
            (204, ""),
        ]
    )
    uploads = []

    def upload(*, url, files, headers, timeout):
        uploads.append(url)
        assert url == "http://localhost:23119/api/local/upload/test"
        assert headers["Zotero-Server-ID"] == "server-1"
        assert "Zotero-API-Key" not in headers
        assert dict(files)["file"] == path.read_bytes()
        return http.Response(
            201, request=http.Request("POST", url), headers={"Zotero-Server-ID": "server-1"}
        )

    monkeypatch.setattr(http, "post", upload)
    result = library.attach_file("ABCD1234", str(path))
    assert result["ok"] is True
    assert result["key"] == "ATTACH01"
    assert result["version"] == result["results"][0]["version"] == 8
    assert len(uploads) == 1
    assert calls[-1].url.path == "/api/users/0/items/ATTACH01/file"
    assert b"upload=registration-test" in calls[-1].content
