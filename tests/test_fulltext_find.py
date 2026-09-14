"""Mock-only tests for explicit attachment text lookup."""

from unittest.mock import call

import pytest

from zotero_mcp.client import FulltextError
from zotero_mcp.fulltext import text_revision


def test_find_returns_actual_code_point_positions_and_context(library, zot):
    zot.fulltext_item.return_value = {"content": "前Beta中beta后", "indexedPages": 1, "totalPages": 1}

    result = library.find_item_fulltext(
        "ATTACH01", "BETA", context_characters=1
    )

    assert result["attachment_key"] == "ATTACH01"
    assert result["match_count"] == 2
    assert result["matches"] == [
        {
            "start": 1,
            "end": 5,
            "snippet": "前Beta中",
            "snippet_start": 0,
            "snippet_end": 6,
        },
        {
            "start": 6,
            "end": 10,
            "snippet": "中beta后",
            "snippet_start": 5,
            "snippet_end": 11,
        },
    ]
    assert result["offset_unit"] == "unicode_code_point"
    assert result["offset_base"] == 0
    assert result["index_coverage"] == {"indexed_pages": 1, "total_pages": 1}
    zot.item.assert_not_called()
    zot.children.assert_not_called()


def test_find_is_literal_and_can_limit_overlapping_results(library, zot):
    zot.fulltext_item.return_value = {"content": "banana a+b a.b"}

    result = library.find_item_fulltext(
        "ATTACH01", "ana", context_characters=0, max_matches=1
    )
    assert result["match_count"] == 1
    assert result["matches"][0] == {
        "start": 1,
        "end": 4,
        "snippet": "ana",
        "snippet_start": 1,
        "snippet_end": 4,
    }

    literal = library.find_item_fulltext("ATTACH01", "a+b", context_characters=0)
    assert literal["match_count"] == 1
    assert literal["matches"][0]["start"] == 7


def test_find_maps_casefold_expansion_to_source_code_points(library, zot):
    content = "Straße"
    zot.fulltext_item.return_value = {"content": content}

    result = library.find_item_fulltext("ATTACH01", "STRASSE")

    assert result["matches"][0]["start"] == 0
    assert result["matches"][0]["end"] == len(content)
    assert result["matches"][0]["snippet"] == content


def test_find_checks_text_revision(library, zot):
    zot.fulltext_item.side_effect = [{"content": "old text"}, {"content": "new text"}]
    first = library.find_item_fulltext("ATTACH01", "text")

    with pytest.raises(FulltextError) as caught:
        library.find_item_fulltext(
            "ATTACH01", "text", text_revision=first["text_revision"]
        )

    assert caught.value.code == "FULLTEXT_REVISION_MISMATCH"
    assert caught.value.details["actual"] == text_revision("new text")
    assert zot.fulltext_item.call_args_list == [call("ATTACH01"), call("ATTACH01")]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attachment_key": "bad", "query": "text"},
        {"attachment_key": "ATTACH01", "query": " "},
        {"attachment_key": "ATTACH01", "query": "text", "context_characters": -1},
        {"attachment_key": "ATTACH01", "query": "text", "max_matches": 0},
    ],
)
def test_find_rejects_invalid_arguments(library, zot, kwargs):
    with pytest.raises(ValueError):
        library.find_item_fulltext(**kwargs)
    zot.fulltext_item.assert_not_called()
