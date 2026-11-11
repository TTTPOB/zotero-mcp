import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pyzotero import PreConditionFailedError, ResourceNotFoundError

from zotero_mcp.client import Library, write_result
from zotero_mcp.errors import (
    CONNECTION_ERROR,
    INVALID_ARGUMENT,
    NOT_FOUND,
    UNKNOWN_WRITE_RESULT,
    VERSION_CONFLICT,
    LibraryError,
)
from zotero_mcp.server import build_server


def test_library_error_has_stable_payload():
    error = LibraryError("EXAMPLE", "message", retryable=True, details={"field": "x"})
    assert error.code == "EXAMPLE"
    assert error.message == "message"
    assert error.retryable is True
    assert error.as_dict() == {
        "error": {
            "code": "EXAMPLE",
            "message": "message",
            "retryable": True,
            "details": {"field": "x"},
        }
    }


def test_write_result_retains_each_input_index_and_status():
    result = write_result(
        {
            "success": {"0": "NEW00001"},
            "unchanged": {"1": True},
            "failed": {"2": {"code": 400, "message": "bad field"}},
        },
        input_keys=[None, "ABCD1234", "SECOND02"],
    )
    assert result["ok"] is False
    assert [entry["input_index"] for entry in result["results"]] == [0, 1, 2]
    assert [entry["status"] for entry in result["results"]] == [
        "success",
        "unchanged",
        "failed",
    ]
    assert result["results"][0]["key"] == "NEW00001"
    assert result["results"][1]["key"] == "ABCD1234"
    assert result["results"][2]["key"] == "SECOND02"
    assert result["results"][2]["error"] == {
        "code": "INVALID_ARGUMENT",
        "message": "bad field",
        "retryable": False,
        "details": {"http_status": 400},
    }


def test_create_failures_map_http_status_without_inventing_keys():
    result = write_result(
        {
            "failed": {
                "0": {"key": "", "code": 404, "message": "missing"},
                "1": {"key": "", "code": 412, "message": "stale"},
                "2": {"key": "", "code": 503, "message": "unavailable"},
            }
        }
    )
    assert [entry["error"]["code"] for entry in result["results"]] == [
        "NOT_FOUND",
        "VERSION_CONFLICT",
        "UNKNOWN_ERROR",
    ]
    assert [entry["error"]["details"]["http_status"] for entry in result["results"]] == [
        404,
        412,
        503,
    ]
    assert all("key" not in entry for entry in result["results"])
    assert all(entry["error"]["retryable"] is False for entry in result["results"])


def test_write_result_missing_inputs_are_unknown_and_preserve_success():
    result = write_result({"success": {"0": "NEW00001"}}, input_count=3)
    assert result["ok"] is False
    assert result["status"] == "unknown"
    assert result["results"][0]["key"] == "NEW00001"
    assert [entry["status"] for entry in result["results"]] == [
        "success",
        "unknown",
        "unknown",
    ]
    assert result["results"][1]["error"]["code"] == "UNKNOWN_WRITE_RESULT"
    assert result["results"][1]["error"]["retryable"] is False


def test_success_and_unchanged_have_success_status():
    result = write_result({"success": {"0": "NEW00001"}, "unchanged": {"1": True}}, input_count=2)
    assert result["ok"] is True
    assert result["status"] == "success"


def test_factory_connection_error_is_not_unknown_write(zot):
    attempts = 0

    def factory():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("Zotero is not running")
        return zot

    library = Library(factory)
    with pytest.raises(LibraryError) as raised:
        library.get_item_metadata(["ABCD1234"])
    assert raised.value.code == CONNECTION_ERROR
    assert raised.value.retryable is True
    try:
        result = library.get_item_metadata(["ABCD1234"])
        assert result["items"][0]["status"] == "ok"
        assert result["items"][0]["item"]["key"] == "ABCD1234"
    finally:
        library.close()


def test_write_transport_error_is_unknown_and_not_retryable(library, zot):
    zot.update_item.side_effect = ConnectionError("connection dropped")
    with pytest.raises(LibraryError) as raised:
        library.update_item("ABCD1234", 7, {"title": "new"})
    assert raised.value.code == UNKNOWN_WRITE_RESULT
    assert raised.value.retryable is False
    assert zot.update_item.call_count == 1


def test_known_exception_codes_are_not_hidden_by_http_causes(library, zot):
    zot.update_item.side_effect = PreConditionFailedError("stale")
    with pytest.raises(LibraryError) as raised:
        library.update_item("ABCD1234", 7, {"title": "new"})
    assert raised.value.code == VERSION_CONFLICT
    zot.items.side_effect = ResourceNotFoundError("missing")
    with pytest.raises(LibraryError) as raised:
        library.get_item_metadata(["ABCD1234"])
    assert raised.value.code == NOT_FOUND


def test_batch_result_keeps_updated_failed_and_pending_entries(library, zot):
    zot.update_item.side_effect = [True, PreConditionFailedError("stale")]
    result = library.batch_update_tags(["ABCD1234", "SECOND02", "THIRD003"], add=["new"])
    assert [entry["status"] for entry in result["results"]] == [
        "success",
        "failed",
        "pending",
    ]
    assert result["results"][1]["error"]["code"] == VERSION_CONFLICT
    assert result["results"][2] == {"input_index": 2, "key": "THIRD003", "status": "pending"}


def test_write_result_rejects_empty_response_with_machine_code():
    with pytest.raises(LibraryError) as raised:
        write_result({"success": {}, "failed": {}})
    assert raised.value.code == UNKNOWN_WRITE_RESULT


async def test_invalid_write_arguments_have_machine_code(library, zot):
    async with create_connected_server_and_client_session(build_server(library)) as session:
        result = await session.call_tool(
            "zotero_update_item", {"item_key": "bad", "version": 7, "changes": {"title": "new"}}
        )
    assert result.isError
    assert result.structuredContent["error"]["code"] == INVALID_ARGUMENT
    zot.update_item.assert_not_called()
