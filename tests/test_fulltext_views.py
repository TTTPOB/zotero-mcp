"""Mock-only tests for transparent fulltext attachment selection."""

import pytest

from zotero_mcp.client import FulltextError


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
