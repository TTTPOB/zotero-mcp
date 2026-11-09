"""search_items rejects item types Zotero does not know, against a real Zotero."""

from live_support import PDF_NEEDLE


def keys(page):
    return [item["key"] for item in page["items"]]


def test_unknown_item_type_is_rejected_with_a_pointer_to_the_schema(mcp, seed):
    result = mcp("search_items", {"query": "Live", "item_type": "article"}, error=True)
    message = result["error"]["message"]

    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert "'article' is not a Zotero item type" in message
    assert "journalArticle" in message
    assert "zotero_get_item_schema" in message


def test_item_type_combinations_still_search(mcp, seed):
    for item_type in ("journalArticle", "book || journalArticle", "-attachment", "-note"):
        page = mcp("search_items", {"query": "Live alpha paper", "item_type": item_type})
        assert seed.alpha in keys(page), item_type
    hits = mcp(
        "search_items", {"query": PDF_NEEDLE, "qmode": "everything", "item_type": "attachment"}
    )
    assert seed.pdf in keys(hits)
