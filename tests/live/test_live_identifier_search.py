"""Live checks that DOI/ISBN queries find exactly the item that holds the identifier."""

import random
import uuid

import pytest

TOKEN = uuid.uuid4().hex[:8]
DOI_BASE = f"10.7777/idl{TOKEN}"


def isbn_pair(rng):
    """A random ISBN-10 and the ISBN-13 of the same book, as digit strings."""
    body = "".join(str(rng.randrange(10)) for _ in range(9))
    check10 = (-sum((10 - i) * int(c) for i, c in enumerate(body))) % 11
    isbn10 = body + ("X" if check10 == 10 else str(check10))
    body13 = "978" + body
    check13 = (10 - sum(int(c) * (1 + 2 * (i % 2)) for i, c in enumerate(body13)) % 10) % 10
    return isbn10, body13 + str(check13)


@pytest.fixture(scope="module")
def isbns():
    rng = random.Random(TOKEN)
    ten, thirteen = isbn_pair(rng)
    return {"ten": ten, "thirteen": thirteen, "other": isbn_pair(rng)[1]}


@pytest.fixture(scope="module")
def ids(mcp, isbns):
    """Library fixture: a target, a citer of it, near-miss DOIs and ISBN books."""
    ids = {}

    def add(name, **fields):
        item = {"itemType": "journalArticle", **fields}
        result = mcp("add_items", {"items": [item]})
        ids[name] = result["results"][0]["key"]
        return ids[name]

    target = add(
        "target",
        title=f"Identifier lookup target {TOKEN}",
        DOI=f"{DOI_BASE}.target.1",
        tags=[{"tag": f"idl-{TOKEN}"}],
    )
    mcp("create_note", {"parent_key": target, "text": f"Notes cite {DOI_BASE}.target.1"})
    add(
        "citer",
        title=f"Cites {DOI_BASE}.target.1 in its title",
        abstractNote=f"We extend {DOI_BASE}.target.1 here.",
        DOI=f"{DOI_BASE}.citer.1",
    )
    add("longer", title="Longer DOI sibling", DOI=f"{DOI_BASE}.target.10")
    add("upper", title="Upper-case DOI", DOI=f"{DOI_BASE.upper()}.UPPER.2")
    add("extra", title="Chapter with DOI in Extra", extra=f"DOI: {DOI_BASE}.extra.3")
    add("book13", title="Book with ISBN-13", itemType="book", ISBN=isbns["thirteen"])
    add("book10", title="Same book, ISBN-10", itemType="book", ISBN=isbns["ten"])
    add("other_book", title="Another book", itemType="book", ISBN=isbns["other"])
    return ids


def found_keys(result):
    return [item["key"] for item in result["items"]]


@pytest.mark.parametrize(
    "form",
    [
        "{doi}",
        "{DOI}",
        "doi:{doi}",
        "DOI: {doi}",
        "https://doi.org/{doi}",
        "http://dx.doi.org/{DOI}",
    ],
)
def test_doi_forms_return_only_the_target(mcp, ids, form):
    doi = f"{DOI_BASE}.target.1"
    result = mcp("search_items", {"query": form.format(doi=doi, DOI=doi.upper())})

    assert found_keys(result) == [ids["target"]]
    lookup = result["identifier_lookup"]
    assert lookup["type"] == "doi" and lookup["match"] == "exact" and lookup["found"] is True
    assert lookup["matches"] == [{"key": ids["target"], "field": "DOI", "value": doi}]
    assert result["total"] == 1 and result["next_offset"] is None


def test_doi_lookup_ignores_qmode_and_item_type_workarounds(mcp, ids):
    result = mcp(
        "search_items",
        {"query": f"{DOI_BASE}.target.1", "qmode": "everything", "item_type": "-attachment"},
    )
    assert found_keys(result) == [ids["target"]]


def test_doi_in_upper_case_and_in_extra(mcp, ids):
    assert found_keys(mcp("search_items", {"query": f"{DOI_BASE}.upper.2"})) == [ids["upper"]]
    result = mcp("search_items", {"query": f"https://doi.org/{DOI_BASE.upper()}.EXTRA.3"})
    assert found_keys(result) == [ids["extra"]]
    # Zotero 10 moves a "DOI:" line of Extra into the DOI field when the item is saved,
    # so the extra fallback only matters for older data (covered offline).
    assert result["identifier_lookup"]["matches"][0]["field"] in {"DOI", "extra"}


def test_missing_doi_is_reported_as_not_found(mcp, ids):
    # The citer's title and abstract mention the target DOI, and a sibling DOI starts
    # with it, but no item has this exact DOI as its identifier.
    result = mcp("search_items", {"query": f"{DOI_BASE}.target"})
    assert result["items"] == [] and result["total"] == 0 and result["next_offset"] is None
    lookup = result["identifier_lookup"]
    assert lookup["found"] is False and lookup["matches"] == []
    assert "No item in the library" in lookup["message"]


@pytest.mark.parametrize(
    "form",
    [
        "{thirteen}",
        "{hyphen13}",
        "{spaced13}",
        "{ten}",
        "{hyphen10}",
        "ISBN {thirteen}",
        "isbn:{ten}",
    ],
)
def test_isbn_forms_find_both_spellings_of_the_book(mcp, ids, isbns, form):
    t, th = isbns["ten"], isbns["thirteen"]
    query = form.format(
        thirteen=th,
        ten=t,
        hyphen13=f"{th[:3]}-{th[3]}-{th[4:7]}-{th[7:12]}-{th[12]}",
        spaced13=f"{th[:3]} {th[3]} {th[4:7]} {th[7:12]} {th[12]}",
        hyphen10=f"{t[0]}-{t[1:4]}-{t[4:9]}-{t[9]}",
    )
    result = mcp("search_items", {"query": query})

    assert sorted(found_keys(result)) == sorted([ids["book13"], ids["book10"]])
    assert result["identifier_lookup"]["type"] == "isbn"
    assert {m["field"] for m in result["identifier_lookup"]["matches"]} == {"ISBN"}
    assert result["total"] == 2


def test_isbn_lookup_pages_through_exact_matches(mcp, ids, isbns):
    query = isbns["thirteen"]
    first = mcp("search_items", {"query": query, "limit": 1})
    assert len(first["items"]) == 1 and first["total"] == 2 and first["next_offset"] == 1
    second = mcp("search_items", {"query": query, "limit": 1, "offset": 1})
    assert len(second["items"]) == 1 and second["next_offset"] is None
    assert {found_keys(first)[0], found_keys(second)[0]} == {ids["book13"], ids["book10"]}


def test_unknown_isbn_is_reported_as_not_found(mcp, ids):
    result = mcp("search_items", {"query": "9780306406157"})
    assert result["items"] == [] and result["identifier_lookup"]["found"] is False


def test_tags_filter_identifier_matches(mcp, ids):
    query = f"{DOI_BASE}.target.1"
    hit = mcp("search_items", {"query": query, "tags": [f"idl-{TOKEN}"]})
    assert found_keys(hit) == [ids["target"]]
    miss = mcp("search_items", {"query": query, "tags": ["no-such-tag"]})
    assert miss["items"] == [] and miss["identifier_lookup"]["found"] is False


def test_plain_text_searches_are_unchanged(mcp, ids):
    result = mcp("search_items", {"query": f"Identifier lookup target {TOKEN}"})
    # Metadata search also returns the target's child note, whose parent title matches.
    assert ids["target"] in found_keys(result)
    assert "identifier_lookup" not in result
