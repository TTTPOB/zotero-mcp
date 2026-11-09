"""Argument errors are short and say how to fix the call, against a real Zotero."""


def test_bad_key_is_explained_in_one_sentence(mcp, seed):
    uri = f"http://zotero.org/users/0/items/{seed.alpha}"
    result = mcp("get_item_metadata", {"item_keys": [seed.alpha, uri]}, error=True)
    error = result["error"]

    assert error["code"] == "INVALID_ARGUMENT"
    assert error["message"].startswith("item_keys[1] must be an 8-character Zotero key")
    assert "zotero_search_items" in error["message"]
    assert uri not in str(result)
    assert "pydantic" not in str(result)


def test_oversized_batch_reports_limit_without_echoing_keys(mcp, seed):
    batch = [f"KEY{i:05d}" for i in range(51)]
    result = mcp("get_item_metadata", {"item_keys": batch}, error=True)

    assert result["error"]["message"].startswith("item_keys accepts at most 50 entries, got 51")
    assert "KEY00050" not in str(result)
    assert len(str(result)) < 500
