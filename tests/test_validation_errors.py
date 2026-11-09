import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server

URI = "http://zotero.org/users/1/items/ABCD2345"


async def _call(library, tool, arguments):
    async with create_connected_server_and_client_session(build_server(library)) as session:
        result = await session.call_tool(tool, arguments)
    assert result.isError
    assert json.loads(result.content[0].text) == result.structuredContent
    return result


def _error(result):
    error = result.structuredContent.get("error") or result.structuredContent
    assert error["code"] == "INVALID_ARGUMENT"
    assert error["retryable"] is False
    return error


def _assert_compact(result, *, forbidden=()):
    text = result.content[0].text
    assert "pydantic" not in text
    assert "errors.pydantic.dev" not in text
    assert "input_value" not in text
    for entry in _error(result).get("details", []):
        assert set(entry) == {"type", "loc", "msg"}
    for fragment in forbidden:
        assert fragment not in text


@pytest.mark.anyio
async def test_bad_key_names_the_element_and_the_format(library):
    result = await _call(
        library, "zotero_get_item_metadata", {"item_keys": ["ABCD2345"] * 3 + [URI]}
    )
    message = _error(result)["message"]

    assert message.startswith("item_keys[3] must be an 8-character Zotero key")
    assert "ABCD2345" in message and "not a URI or title" in message
    assert "zotero_search_items" in message
    assert list(_error(result)["details"][0]["loc"]) == ["item_keys", 3]
    _assert_compact(result, forbidden=[URI])
    assert len(result.content[0].text) < 400


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "arguments", "hint"),
    [
        ("zotero_get_collection_items", {"collection_key": "x"}, "zotero_get_collections"),
        ("zotero_get_item_children", {"item_key": "x"}, "zotero_search_items"),
        ("zotero_read_attachment_text", {"attachment_key": "x"}, "zotero_get_item_children"),
    ],
)
async def test_single_key_parameters_point_to_the_tool_that_lists_keys(
    library, tool, arguments, hint
):
    message = _error(await _call(library, tool, arguments))["message"]

    assert message.startswith(f"{next(iter(arguments))} must be an 8-character Zotero key")
    assert hint in message


@pytest.mark.anyio
async def test_overlong_list_reports_limit_and_count_without_echoing_keys(library):
    keys = [f"KEY{i:05d}" for i in range(51)]
    result = await _call(library, "zotero_get_item_metadata", {"item_keys": keys})

    assert _error(result)["message"] == (
        "item_keys accepts at most 50 entries, got 51; split the call into smaller batches."
    )
    _assert_compact(result, forbidden=["KEY00000", "KEY00050"])
    assert len(result.content[0].text) < 400


@pytest.mark.anyio
async def test_export_items_uses_the_same_key_list_validation(library):
    result = await _call(library, "zotero_export_items", {"item_keys": ["bad"]})

    assert _error(result)["message"].startswith("item_keys[0] must be an 8-character Zotero key")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        (
            "zotero_get_item_metadata",
            {"item_keys": "ABCD2345"},
            'item_keys must be a list of Zotero keys, e.g. ["ABCD2345"]; got a string.',
        ),
        ("zotero_get_item_metadata", {"item_keys": []}, "item_keys needs at least 1 entry."),
        ("zotero_get_item_metadata", {}, "item_keys is required."),
        (
            "zotero_get_item_metadata",
            {"item_keys": ["ABCD2345"], "view": "sumary"},
            "view must be 'summary', 'metadata' or 'raw'; got 'sumary'.",
        ),
        (
            "zotero_search_items",
            {"query": "x", "tags": "a"},
            'tags must be a list, got a string; wrap a single value in a list, e.g. ["value"].',
        ),
        (
            "zotero_search_items",
            {"query": "x", "limit": "many"},
            "limit must be an integer, got 'many'.",
        ),
        (
            "zotero_search_items",
            {"query": "x", "offset": -1},
            "offset must be >= 0, got -1.",
        ),
    ],
)
async def test_common_argument_mistakes_get_one_short_instruction(
    library, tool, arguments, expected
):
    result = await _call(library, tool, arguments)

    assert _error(result)["message"] == expected
    _assert_compact(result)


@pytest.mark.anyio
async def test_several_problems_list_the_first_few_and_the_total(library):
    keys = ["bad1", "bad2", "bad3", "bad4", "bad5"]
    result = await _call(library, "zotero_get_item_metadata", {"item_keys": keys})
    message = _error(result)["message"]

    assert message.startswith("5 invalid arguments: item_keys[0] must be")
    assert "item_keys[2] must be" in message and "item_keys[3]" not in message
    assert message.endswith("; 2 more not shown.")
    assert len(_error(result)["details"]) == 5
    _assert_compact(result)


@pytest.mark.anyio
async def test_write_tool_keeps_the_write_envelope(library):
    result = await _call(library, "zotero_delete_item", {"item_key": "nope", "version": 1})

    assert result.structuredContent["ok"] is False
    assert result.structuredContent["status"] == "failed"
    error = result.structuredContent["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert error["message"].startswith("item_key must be an 8-character Zotero key")


def test_key_helper_error_is_actionable(library):
    with pytest.raises(ValueError, match="8-character Zotero key.*not a URI or title"):
        library.get_item_children("not-a-key-at-all-but-passes-no-pydantic")
