"""Every read-only MCP tool against a real Zotero."""

from live_support import PDF_NEEDLE, TEXT_NEEDLE, read_item


def keys(page):
    return [item["key"] for item in page["items"]]


def test_search_items_by_title_returns_summaries(mcp, seed):
    page = mcp("search_items", {"query": "Live alpha paper"})
    assert seed.alpha in keys(page)
    hit = next(item for item in page["items"] if item["key"] == seed.alpha)
    assert hit["item_type"] == "journalArticle"
    assert hit["title"] == "Live alpha paper"
    assert hit["year"] == 2024
    assert hit["doi"] == "10.5555/live.alpha"
    assert hit["candidate_attachment_key"] == seed.pdf
    assert isinstance(page["offset"], int)


def test_search_items_by_tag(mcp, seed):
    page = mcp("search_items", {"tags": ["live-alpha"]})
    assert seed.alpha in keys(page)
    assert seed.beta not in keys(page)


def test_search_items_everything_mode_finds_doi(mcp, seed):
    page = mcp(
        "search_items",
        {"query": "10.5555/live.beta", "qmode": "everything", "item_type": "-attachment"},
    )
    assert seed.beta in keys(page)


def test_search_items_everything_mode_hits_attachment_with_parent(mcp, seed):
    page = mcp("search_items", {"query": PDF_NEEDLE, "qmode": "everything"})
    hit = next(item for item in page["items"] if item["key"] == seed.pdf)
    assert hit["parent_key"] == seed.alpha
    assert hit["item_type"] == "attachment"
    assert hit["parent"] == {
        "title": "Live alpha paper",
        "creator_summary": "Ada Live",
        "year": 2024,
    }


def test_search_items_blank_item_type_means_no_filter(mcp, seed):
    plain = mcp("search_items", {"query": "Live alpha paper"})
    for blank in ("", "   "):
        page = mcp("search_items", {"query": "Live alpha paper", "item_type": blank})
        assert keys(page) == keys(plain)
        # In everything mode a blank item_type must not hide attachment hits.
        hits = mcp("search_items", {"query": PDF_NEEDLE, "qmode": "everything", "item_type": blank})
        assert seed.pdf in keys(hits)


def test_get_item_metadata_semantic_and_raw(mcp, seed):
    item = read_item(mcp, seed.alpha)
    assert item["key"] == seed.alpha
    assert isinstance(item["version"], int)
    assert item["item_type"] == "journalArticle"
    assert item["data"]["DOI"] == "10.5555/live.alpha"
    assert item["data"]["citationKey"] == "livealpha2024"
    assert {"live-seed", "live-alpha"} <= {t["tag"] for t in item["data"]["tags"]}

    raw = read_item(mcp, seed.alpha, view="raw")
    assert raw["key"] == seed.alpha
    assert "links" in raw and "meta" in raw


def test_get_item_metadata_reads_child_items(mcp, seed):
    attachment = read_item(mcp, seed.pdf)
    assert attachment["item_type"] == "attachment"
    assert attachment["parent_key"] == seed.alpha
    note = read_item(mcp, seed.note)
    assert note["item_type"] == "note"
    assert note["parent_key"] == seed.alpha


def test_get_item_metadata_unknown_key_is_not_found(mcp):
    entry = mcp("get_item_metadata", {"item_keys": ["ZZZZZZZZ"]})["items"][0]
    assert entry["status"] == "error"
    assert entry["error"]["code"] == "NOT_FOUND"


def test_get_item_metadata_batch_keeps_order_and_requested_keys_only(mcp, seed):
    requested = [seed.pdf, "ZZZZZZZZ", seed.alpha, seed.pdf]
    entries = mcp("get_item_metadata", {"item_keys": requested})["items"]

    assert [entry["index"] for entry in entries] == [0, 1, 2, 3]
    assert [entry["key"] for entry in entries] == requested
    assert [entry["status"] for entry in entries] == ["ok", "error", "ok", "ok"]
    assert entries[0]["item"]["parent_key"] == seed.alpha
    assert entries[1]["error"]["code"] == "NOT_FOUND"
    assert entries[2]["item"]["key"] == seed.alpha
    assert entries[3]["item"] == entries[0]["item"]

    # Zotero also returns the children of a requested parent; they must not leak in.
    only_parent = mcp("get_item_metadata", {"item_keys": [seed.alpha]})["items"]
    assert [entry["key"] for entry in only_parent] == [seed.alpha]


def test_get_item_children(mcp, seed):
    page = mcp("get_item_children", {"item_key": seed.alpha})
    assert {seed.pdf, seed.note} <= set(keys(page))
    assert all(item["parent_key"] == seed.alpha for item in page["items"])
    types = {item["key"]: item["item_type"] for item in page["items"]}
    assert types[seed.pdf] == "attachment"
    assert types[seed.note] == "note"


def test_upstream_collection_num_items_counts_papers_not_child_attachments_or_notes(mcp, seed):
    page = mcp("get_collections", {"limit": 100})
    by_key = {item["key"]: item for item in page["items"]}
    assert by_key[seed.parent]["name"] == "Live parent"
    assert "parent_key" not in by_key[seed.parent]
    assert by_key[seed.child]["parent_key"] == seed.parent
    raw = mcp("get_collections", {"limit": 100, "view": "raw"})
    native = {item["key"]: item for item in raw["items"]}
    # One member paper has a child PDF and note; those children do not add to numItems.
    assert native[seed.parent]["meta"]["numItems"] == 1
    assert native[seed.child]["meta"]["numItems"] == 0
    assert by_key[seed.parent]["item_count"] == 1
    assert by_key[seed.child]["item_count"] == 0
    print("collection meta.numItems:", native[seed.parent]["meta"], native[seed.child]["meta"])
    assert isinstance(by_key[seed.child]["version"], int)


def test_get_collection_items(mcp, seed):
    page = mcp("get_collection_items", {"collection_key": seed.parent})
    assert seed.alpha in keys(page)
    # Children of collection members are not listed; the native non-top endpoint would.
    assert not {seed.pdf, seed.note} & set(keys(page))
    assert all("parent_key" not in item for item in page["items"])
    assert page["total"] == len(page["items"])
    assert mcp("get_collection_items", {"collection_key": seed.child})["items"] == []


def test_get_collection_items_keeps_standalone_notes(mcp, new_item):
    collection = mcp("create_collection", {"name": "Live top-level only"})["results"][0]["key"]
    paper, _ = new_item(collections=[collection])
    child_note = mcp("create_note", {"parent_key": paper, "text": "child"})["results"][0]["key"]
    standalone = mcp(
        "add_items",
        {"items": [{"itemType": "note", "note": "<p>standalone</p>", "collections": [collection]}]},
    )["results"][0]["key"]

    page = mcp("get_collection_items", {"collection_key": collection})

    assert set(keys(page)) == {paper, standalone}
    assert child_note not in keys(page)
    assert page["total"] == 2


def test_get_recent_lists_top_level_items(mcp, seed):
    page = mcp("get_recent", {"limit": 100})
    found = set(keys(page))
    assert {seed.alpha, seed.beta} <= found
    assert seed.note not in found  # child items are not top level


def test_get_notes_for_parent_and_library(mcp, seed):
    scoped = mcp("get_notes", {"parent_key": seed.alpha})
    assert keys(scoped) == [seed.note]
    note = scoped["items"][0]
    assert "Live seeded note" in note["data"]["note"]
    assert any(tag["tag"] == "live-note" for tag in note["data"]["tags"])
    assert seed.note in keys(mcp("get_notes", {"limit": 100}))


def test_get_annotations(mcp, seed):
    page = mcp("get_annotations", {"attachment_key": seed.pdf})
    assert keys(page) == [seed.annotation]
    data = page["items"][0]["data"]
    assert data["annotationType"] == "highlight"
    assert data["annotationText"] == "live highlighted text"


def test_get_item_fulltext_from_parent(mcp, seed):
    chunk = mcp("get_item_fulltext", {"item_key": seed.alpha})
    assert chunk["attachment_key"] == seed.pdf
    assert PDF_NEEDLE in chunk["content"]
    assert chunk["offset"] == 0
    assert chunk["total_characters"] >= len(PDF_NEEDLE)
    assert chunk["text_revision"].startswith("sha256:")


def test_get_item_fulltext_chunks_reuse_revision(mcp, seed):
    first = mcp("get_item_fulltext", {"item_key": seed.alpha, "max_characters": 5})
    assert first["next_offset"] == 5
    second = mcp(
        "get_item_fulltext",
        {
            "item_key": first["attachment_key"],
            "offset": first["next_offset"],
            "text_revision": first["text_revision"],
        },
    )
    assert second["offset"] == 5
    whole = mcp("get_item_fulltext", {"item_key": seed.alpha})
    assert first["content"] + second["content"] == whole["content"]


def test_find_item_fulltext(mcp, seed):
    result = mcp("find_item_fulltext", {"item_key": seed.pdf, "query": PDF_NEEDLE.upper()})
    assert result["attachment_key"] == seed.pdf
    assert result["matches"], result
    assert PDF_NEEDLE in result["matches"][0]["snippet"].lower()


def test_read_attachment_text(mcp, seed):
    chunk = mcp("read_attachment_text", {"attachment_key": seed.text})
    assert chunk["filename"] == "notes.txt"
    assert chunk["content_type"] == "text/plain"
    assert TEXT_NEEDLE in chunk["content"]
    assert chunk["total_characters"] == len(chunk["content"])
    assert len(chunk["content_sha256"]) == 64


def test_read_attachment_text_rejects_pdf(mcp, seed):
    result = mcp("read_attachment_text", {"attachment_key": seed.pdf}, error=True)
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_get_item_schema(mcp):
    types = mcp("get_item_schema")["item_types"]
    assert "journalArticle" in {t["itemType"] for t in types}
    schema = mcp("get_item_schema", {"item_type": "journalArticle"})
    assert "DOI" in {f["field"] for f in schema["fields"]}
    assert "author" in {c["creatorType"] for c in schema["creator_types"]}


def test_export_items_bibtex_and_ris(mcp, seed):
    bibtex = mcp("export_items", {"item_keys": [seed.alpha], "format": "bibtex"})
    assert bibtex["format"] == "bibtex"
    assert "@article{livealpha2024" in bibtex["content"]
    ris = mcp("export_items", {"item_keys": [seed.alpha], "format": "ris"})
    assert ris["format"] == "ris"
    assert "TY  - JOUR" in ris["content"]


def test_export_items_csljson_is_parsed(mcp, seed):
    result = mcp("export_items", {"item_keys": [seed.alpha, seed.beta], "format": "csljson"})
    assert result["format"] == "csljson"
    content = result["content"]
    assert isinstance(content, list) and len(content) == 2
    assert {entry["DOI"] for entry in content} == {"10.5555/live.alpha", "10.5555/live.beta"}
    assert all(entry["type"] == "article-journal" for entry in content)


def test_get_citation_items_per_key_status(mcp, seed):
    result = mcp("get_citation_items", {"item_keys": [seed.alpha, seed.note, "ZZZZZZZZ"]})
    alpha, note, missing = result["items"]
    assert (alpha["index"], alpha["status"]) == (0, "ok")
    assert alpha["citation_key"] == "livealpha2024"
    assert alpha["uri"] == f"http://zotero.org/users/0/items/{seed.alpha}"
    assert alpha["select_link"] == f"zotero://select/library/items/{seed.alpha}"
    assert alpha["csl"]["DOI"] == "10.5555/live.alpha"
    assert note["status"] == "error"
    assert note["error"]["code"] == "NOT_CITABLE"
    assert note["parent_key"] == seed.alpha
    assert missing["error"]["code"] == "NOT_FOUND"
