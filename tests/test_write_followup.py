import json
from datetime import timedelta

from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server


def test_attachment_upload_success_list_does_not_collide_with_write_schema(library, zot, tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"pdf")
    zot.upload_attachments.return_value = {
        "success": [{"key": "NEW00001"}],
        "failure": [],
        "unchanged": [],
    }
    result = library.attach_file("ABCD1234", str(path))
    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["attachment_key"] == "NEW00001"
    assert result["results"][0]["status"] == "success"
    assert result["upload"]["success"] == [{"key": "NEW00001"}]


async def test_protocol_attachment_upload_success_list_is_schema_compatible(library, zot, tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"pdf")
    zot.upload_attachments.return_value = {
        "success": [{"key": "NEW00001"}],
        "failure": [],
        "unchanged": [],
    }
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_attach_file",
            {"parent_key": "ABCD1234", "file_path": str(path)},
        )
    assert result.isError is False
    assert result.structuredContent["attachment_key"] == "NEW00001"
    assert result.structuredContent["upload"]["success"] == [{"key": "NEW00001"}]
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_protocol_attachment_upload_none_is_unknown_with_key_and_stage(
    library, zot, tmp_path
):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"pdf")
    zot.upload_attachments.return_value = None
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_attach_file",
            {"parent_key": "ABCD1234", "file_path": str(path)},
        )
    assert result.isError is True
    assert result.structuredContent["status"] == "unknown"
    assert result.structuredContent["attachment_key"] == "NEW00001"
    assert result.structuredContent["stage"] == "upload"
    assert result.structuredContent["error"]["code"] == "UNKNOWN_WRITE_RESULT"
    assert result.structuredContent["error"]["retryable"] is False
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_protocol_add_missing_batch_result_is_unknown_but_keeps_success(library, zot):
    zot.create_items.return_value = {"success": {"0": "NEW00001"}, "failed": {}}
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_add_items",
            {"items": [{"itemType": "book"}, {"itemType": "book"}]},
        )
    assert result.isError is True
    assert result.structuredContent["status"] == "unknown"
    assert result.structuredContent["success"] == {"0": "NEW00001"}
    assert result.structuredContent["results"][1]["status"] == "unknown"
    assert result.structuredContent["error"]["code"] == "UNKNOWN_WRITE_RESULT"
    assert json.loads(result.content[0].text) == result.structuredContent


def test_interrupted_batch_marks_only_current_item_unknown(library, zot):
    zot.update_item.side_effect = [True, ConnectionError("dropped")]
    result = library.batch_update_tags(["ABCD1234", "SECOND02", "THIRD003"], add=["new"])
    assert result["status"] == "unknown"
    assert [entry["status"] for entry in result["results"]] == [
        "success",
        "unknown",
        "pending",
    ]
    assert result["unknown"]["SECOND02"]["code"] == "UNKNOWN_WRITE_RESULT"
    assert result["pending"] == ["THIRD003"]


async def test_protocol_interrupted_batch_is_error_with_completed_entries(library, zot):
    zot.update_item.side_effect = [True, ConnectionError("dropped")]
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_batch_update_tags",
            {"item_keys": ["ABCD1234", "SECOND02", "THIRD003"], "add": ["new"]},
        )
    assert result.isError is True
    assert result.structuredContent["status"] == "unknown"
    assert [entry["status"] for entry in result.structuredContent["results"]] == [
        "success",
        "unknown",
        "pending",
    ]
    assert result.structuredContent["unknown"]["SECOND02"]["code"] == "UNKNOWN_WRITE_RESULT"
    assert result.structuredContent["pending"] == ["THIRD003"]
    assert json.loads(result.content[0].text) == result.structuredContent


async def test_protocol_write_connection_is_unknown_and_not_retryable(library, zot):
    zot.update_item.side_effect = ConnectionError("dropped")
    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        result = await session.call_tool(
            "zotero_update_item",
            {"item_key": "ABCD1234", "version": 7, "changes": {"title": "new"}},
        )
    assert result.isError is True
    assert result.structuredContent["status"] == "unknown"
    assert result.structuredContent["error"]["code"] == "UNKNOWN_WRITE_RESULT"
    assert result.structuredContent["error"]["retryable"] is False
    assert json.loads(result.content[0].text) == result.structuredContent


def test_attachment_upload_empty_mapping_is_unknown_with_key(library, zot, tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"pdf")
    zot.upload_attachments.return_value = {}
    result = library.attach_file("ABCD1234", str(path))
    assert result["ok"] is False
    assert result["status"] == "unknown"
    assert result["attachment_key"] == "NEW00001"
    assert result["stage"] == "upload"
    assert result["error"]["code"] == "UNKNOWN_WRITE_RESULT"


def test_attachment_upload_disconnect_keeps_key_and_unknown_details(library, zot, tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"pdf")
    zot.upload_attachments.side_effect = ConnectionError("dropped")
    result = library.attach_file("ABCD1234", str(path))
    assert result["ok"] is False
    assert result["status"] == "unknown"
    assert result["attachment_key"] == "NEW00001"
    assert result["stage"] == "upload"
    assert result["error"]["code"] == "UNKNOWN_WRITE_RESULT"
    assert result["error"]["retryable"] is False
    assert "Inspect" in result["error_message"]
