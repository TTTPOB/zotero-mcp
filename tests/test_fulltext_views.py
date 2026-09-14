"""Mock-only tests for stable attachment selection and indexed text views."""

from unittest.mock import call

import pytest
from pyzotero import ResourceNotFoundError

from zotero_mcp.client import FulltextError
from zotero_mcp.fulltext import text_revision


def attachment(key: str, *, content_type: str = "application/pdf", title: str = ""):
    return {
        "key": key,
        "data": {
            "itemType": "attachment",
            "contentType": content_type,
            "title": title,
            "filename": f"{key}.pdf",
        },
    }


def test_unique_pdf_is_preferred_over_other_attachment(library, zot):
    zot.children.return_value = [
        attachment("SUPP0001", content_type="text/plain", title="Supplement"),
        attachment("PAPER001", title="Main paper"),
    ]
    zot.fulltext_item.return_value = {"content": "paper text"}

    result = library.get_item_fulltext("ABCD1234", max_characters=4)

    assert result["attachment_key"] == "PAPER001"
    zot.fulltext_item.assert_called_once_with("PAPER001")


def test_multiple_pdfs_return_limited_candidates_without_probing(library, zot):
    zot.children.return_value = [attachment(f"PDF{index:05d}") for index in range(1, 8)]

    with pytest.raises(FulltextError) as caught:
        library.get_item_fulltext("ABCD1234")

    error = caught.value
    assert error.code == "FULLTEXT_ATTACHMENT_AMBIGUOUS"
    assert error.details["candidate_count"] == 7
    assert len(error.details["candidates"]) == 5
    assert all(
        set(candidate)
        == {"attachment_key", "title", "filename", "content_type", "index_state"}
        for candidate in error.details["candidates"]
    )
    zot.fulltext_item.assert_not_called()


def test_explicit_attachment_key_wins_over_parent_ambiguity(library, zot):
    zot.children.return_value = [attachment("PDF00001"), attachment("PDF00002")]
    zot.fulltext_item.return_value = {"content": "chosen"}

    result = library.get_item_fulltext("ABCD1234", attachment_key="PDF00002")

    assert result["attachment_key"] == "PDF00002"
    zot.fulltext_item.assert_called_once_with("PDF00002")
    zot.children.assert_not_called()


def test_follow_up_read_uses_revision_and_rejects_changed_text(library, zot):
    original = "abcdefgh"
    changed = "abcXYZgh"
    zot.fulltext_item.side_effect = [{"content": original}, {"content": changed}]

    first = library.get_item_fulltext(
        "ABCD1234", attachment_key="ATTACH01", max_characters=3
    )
    assert first["text_revision"] == text_revision(original)
    assert first["next_offset"] == 3

    with pytest.raises(FulltextError) as caught:
        library.get_item_fulltext(
            "ABCD1234",
            attachment_key="ATTACH01",
            offset=3,
            max_characters=3,
            text_revision=first["text_revision"],
        )
    assert caught.value.code == "FULLTEXT_REVISION_MISMATCH"
    assert caught.value.details["actual"] == text_revision(changed)
    assert zot.fulltext_item.call_args_list == [call("ATTACH01"), call("ATTACH01")]


def test_offsets_are_zero_based_unicode_code_points_and_coverage_is_explicit(library, zot):
    content = "甲😀乙丙"
    zot.fulltext_item.return_value = {
        "content": content,
        "indexedPages": 2,
        "totalPages": 5,
    }

    result = library.get_item_fulltext(
        "ABCD1234", attachment_key="ATTACH01", offset=1, max_characters=2
    )

    assert result["content"] == "😀乙"
    assert result["offset"] == 1
    assert result["next_offset"] == 3
    assert result["offset_unit"] == "unicode_code_point"
    assert result["offset_base"] == 0
    assert result["index_coverage"] == {"indexed_pages": 2, "total_pages": 5}
    assert result["reading_coverage"] == {
        "start": 1,
        "end": 3,
        "total_characters": 4,
        "complete": False,
    }


def test_unindexed_explicit_attachment_has_actionable_error(library, zot):
    zot.fulltext_item.side_effect = ResourceNotFoundError("unindexed")

    with pytest.raises(FulltextError) as caught:
        library.get_item_fulltext("ABCD1234", attachment_key="ATTACH01")

    assert caught.value.code == "FULLTEXT_NOT_INDEXED"
    assert caught.value.details == {"attachment_key": "ATTACH01"}
