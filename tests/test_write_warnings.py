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
        "publicationTitle on book is absent from stored data; extra gained content; check get_item_schema before editing this field."
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
        "creators[0].creatorType on journalArticle changed from director to author; check get_item_schema before editing creators."
    ]
