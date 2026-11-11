"""Warnings report Zotero's stored changes without rejecting its accepted writes."""

import copy


def test_create_warns_when_a_field_moves_to_extra_but_not_for_normalization(library, zot):
    sent = {
        "itemType": "book",
        "title": " A title ",
        "publicationTitle": "Journal",
        "tags": ["tag"],
        "date": 2020,
    }
    zot.create_items.return_value = {
        "successful": {
            "0": {
                "key": "NEW00001",
                "version": 9,
                "data": {
                    "itemType": "book",
                    "title": "A title",
                    "extra": "Publication Title: Journal",
                    "tags": [{"tag": "tag"}],
                    "date": "2020",
                },
            }
        }
    }
    result = library.add_items([sent])
    assert result["ok"] is True
    assert result["results"][0]["warnings"] == [
        "publicationTitle is not a book field: Zotero stored the item without it and changed extra "
        "instead. Check zotero_get_item_schema for valid fields."
    ]
    # A base-field mapping is not a dropped value.
    zot.create_items.return_value["successful"]["0"]["data"] = {
        "itemType": "thesis",
        "university": "P",
    }
    assert (
        "warnings"
        not in library.add_items([{"itemType": "thesis", "publisher": "P"}])["results"][0]
    )


def test_update_warns_when_zotero_replaces_a_creator_role(library, zot, item):
    saved = copy.deepcopy(item)
    saved["version"] = 12
    saved["data"]["creators"] = [{"creatorType": "author", "name": "A"}]
    zot.item.side_effect = [item, saved]
    result = library.update_item(
        "ABCD1234", 7, {"creators": [{"creatorType": "director", "name": "A"}]}
    )
    assert result["ok"] is True and result["version"] == 12
    assert result["results"][0]["warnings"] == [
        "creators[0].creatorType director is not valid for journalArticle: Zotero stored author. "
        "Check zotero_get_item_schema for valid creator types."
    ]


def test_update_does_not_blame_extra_that_was_already_there(library, zot, item):
    item["data"]["itemType"] = "book"
    item["data"]["extra"] = "Citation Key: old"
    saved = copy.deepcopy(item)
    zot.item.side_effect = [item, saved]
    result = library.update_item("ABCD1234", 7, {"notAField": "x"})
    assert result["results"][0]["warnings"] == [
        "notAField is not a book field: Zotero did not store it. "
        "Check zotero_get_item_schema for valid fields."
    ]
