import pytest


@pytest.mark.parametrize("format", ["bibtex", "ris", "csljson"])
@pytest.mark.parametrize("count", [1, 50])
def test_export_uses_top_with_explicit_limit(library, zot, format, count):
    keys = [f"KEY{i:05d}" for i in range(count)]
    exported = [{"id": "citation-key", "type": "article-journal"}]
    zot.top.return_value = exported
    result = library.export_items(keys, format=format)
    zot.top.assert_called_once_with(itemKey=",".join(keys), format=format, limit=count)
    zot.items.assert_not_called()
    assert result == {
        "format": format,
        "content": exported if format == "csljson" else zot.request.text,
    }


@pytest.mark.parametrize("keys", [[], ["ABCD1234"] * 51])
def test_export_rejects_out_of_range_count(library, zot, keys):
    with pytest.raises(ValueError, match="1 to 50"):
        library.export_items(keys)
    zot.top.assert_not_called()
