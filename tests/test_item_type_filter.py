import pytest

from zotero_mcp.errors import LibraryError


@pytest.mark.parametrize(
    "item_type",
    [
        "book",
        "-attachment",
        " -attachment ",
        "book || journalArticle",
        "book||-note",
        "",
        "   ",
        None,
    ],
)
def test_known_item_type_syntax_is_sent_unchanged(library, zot, item_type):
    library.search_items("term", item_type=item_type)

    zot.items.assert_called_once()


@pytest.mark.parametrize("item_type", ["article", "Book", "book || paper", "-nope"])
def test_unknown_item_type_is_rejected_before_searching(library, zot, item_type):
    with pytest.raises(LibraryError) as caught:
        library.search_items("term", item_type=item_type)

    assert caught.value.code == "INVALID_ARGUMENT"
    assert "zotero_get_item_schema" in caught.value.message
    zot.items.assert_not_called()


def test_unknown_item_type_suggests_close_matches(library):
    with pytest.raises(LibraryError) as caught:
        library.search_items("term", item_type="article")

    assert "'article' is not a Zotero item type" in caught.value.message
    assert "journalArticle" in caught.value.message
    with pytest.raises(LibraryError) as caught:
        library.search_items("term", item_type="Book")
    assert "Did you mean: book?" in caught.value.message


def test_item_types_are_fetched_once_per_library(library, zot):
    for _ in range(3):
        library.search_items("term", item_type="book")

    zot.item_types.assert_called_once()
