import copy
from types import SimpleNamespace

from zotero_mcp.client import page


def _item(item_key: str = "PAPER001") -> dict:
    return {
        "key": item_key,
        "version": 3,
        "data": {"itemType": "journalArticle", "title": "Title", "date": "2024"},
    }


def test_total_results_ends_exact_full_page(library, zot):
    native = _item()
    zot.top.return_value = [native, copy.deepcopy(native)]
    zot.links = {"self": "/users/0/items/top?start=0&limit=2"}
    zot.request = SimpleNamespace(headers={"total-results": "2"})

    result = library.get_recent(limit=2)

    assert result["total"] == 2
    assert result["next_offset"] is None


def test_pyzotero_next_link_wins_over_length_fallback(library, zot):
    native = _item()
    zot.top.return_value = [native, copy.deepcopy(native)]
    zot.links = {
        "self": "/users/0/items/top?start=0&limit=2",
        "next": "/users/0/items/top?start=7&limit=2",
    }
    zot.request = SimpleNamespace(headers={"Total-Results": "9"})

    result = library.get_recent(limit=2)

    assert result["total"] == 9
    assert result["next_offset"] == 7


def test_empty_page_with_total_does_not_repeat_same_offset():
    result = page([], limit=25, offset=0, headers={"Total-Results": "5"})

    assert result["total"] == 5
    assert result["next_offset"] is None


def test_link_header_leading_space_keeps_complete_start_value():
    result = page(
        [_item()],
        limit=1,
        offset=0,
        headers={"Link": ' <http://localhost/items?start=4&limit=1>; rel="next"'},
    )

    assert result["next_offset"] == 4


def test_http_link_header_is_used_when_links_are_unavailable(library, zot):
    native = _item()
    zot.top.return_value = [native]
    zot.links = None
    zot.request = SimpleNamespace(
        headers={"Link": '<http://localhost/items?start=4&limit=1>; rel="next"'}
    )

    result = library.get_recent(limit=1)

    assert result["next_offset"] == 4
