import pytest

from zotero_mcp.client import IDENTIFIER_CANDIDATE_CAP
from zotero_mcp.identifiers import matching_field, parse_identifier, stored_isbns


def native(key, title="Paper", item_type="journalArticle", **fields):
    data = {"itemType": item_type, "title": title, **fields}
    return {"key": key, "version": 1, "data": data}


@pytest.mark.parametrize(
    "query",
    [
        "10.1000/target.1",
        " 10.1000/Target.1 ",
        "doi:10.1000/target.1",
        "DOI: 10.1000/TARGET.1",
        "https://doi.org/10.1000/target.1",
        "http://dx.doi.org/10.1000/Target.1",
        "HTTPS://DOI.ORG/10.1000/target.1",
        "doi.org/10.1000/target.1",
        "https://doi.org/10.1000/target.1?utm=1",
    ],
)
def test_doi_query_forms_normalize_to_one_doi(query):
    identifier = parse_identifier(query)
    assert (identifier.kind, identifier.value) == ("doi", "10.1000/target.1")
    assert identifier.queries == ("10.1000/target.1",)


def test_percent_encoded_doi_url_is_decoded():
    identifier = parse_identifier("https://doi.org/10.1002/%28SICI%29ABC")
    assert identifier.value == "10.1002/(sici)abc"


@pytest.mark.parametrize(
    "query",
    ["9780262035613", "978-0-262-03561-3", "978 0 262 03561 3", "ISBN 978-0-262-03561-3"],
)
def test_isbn13_forms_normalize_and_cover_the_isbn10_spelling(query):
    identifier = parse_identifier(query)
    assert (identifier.kind, identifier.value) == ("isbn", "9780262035613")
    assert identifier.accepted == {"9780262035613", "0262035618"}
    # Wildcard queries reach the stored value however it is hyphenated.
    assert identifier.queries == ("9%7%8%0%2%6%2%0%3%5%6%1%3", "0%2%6%2%0%3%5%6%1%8")


@pytest.mark.parametrize("query", ["0262035618", "0-262-03561-8", "isbn:0-262-03561-8"])
def test_isbn10_forms_normalize_to_isbn13(query):
    identifier = parse_identifier(query)
    assert identifier.value == "9780262035613"
    assert identifier.accepted == {"9780262035613", "0262035618"}


def test_isbn10_with_x_check_digit():
    identifier = parse_identifier("0-8044-2957-X")
    assert identifier.accepted == {"080442957X", "9780804429573"}
    assert identifier.queries[-1] == "0%8%0%4%4%2%9%5%7%X"


def test_isbn979_has_no_isbn10_spelling():
    identifier = parse_identifier("979-10-90636-07-1")
    assert identifier.accepted == {"9791090636071"}


@pytest.mark.parametrize(
    "query",
    [
        "",
        "attention is all you need",
        "10.1000/target.1 smith",
        "see 10.1000/target.1",
        "doi 10.1000/x",
        "2020",
        "9780262035614",  # bare number with a wrong check digit
        "1234567890",
        "10.1000",
        "ISBN",
    ],
)
def test_other_queries_are_not_identifiers(query):
    assert parse_identifier(query) is None


def test_labelled_isbn_with_wrong_check_digit_is_looked_up_without_conversion():
    identifier = parse_identifier("ISBN 9780262035614")
    assert identifier.accepted == {"9780262035614"}


def test_stored_isbn_values_are_parsed_leniently():
    assert stored_isbns("978-1-4028-9462-6 978-3-16-148410-0") == {
        "9781402894626",
        "9783161484100",
    }
    assert stored_isbns("ISBN 0-262-03561-8") == {"0262035618", "9780262035613"}
    assert stored_isbns("9780262035613") == {"9780262035613"}
    assert stored_isbns("978 0 262 03561 3") == {"9780262035613"}
    assert stored_isbns("not an isbn 12345") == set()


def test_matching_field_covers_doi_field_extra_and_isbn():
    doi = parse_identifier("10.1000/target.1")
    assert matching_field(native("A", DOI="10.1000/TARGET.1"), doi) == ("DOI", "10.1000/TARGET.1")
    assert matching_field(native("A", DOI="https://doi.org/10.1000/target.1"), doi)[0] == "DOI"
    assert matching_field(native("A", DOI="10.1000/target.10"), doi) is None
    assert matching_field(native("A", DOI="10.1000/target"), doi) is None
    extra = native("A", extra="PMID: 1\nDOI: 10.1000/Target.1\nfoo")
    assert matching_field(extra, doi) == ("extra", "10.1000/Target.1")
    assert matching_field(native("A", extra="see DOI: 10.1000/target.1 inline"), doi) is None
    assert matching_field(native("A", abstractNote="10.1000/target.1"), doi) is None

    isbn = parse_identifier("0262035618")
    assert matching_field(native("B", ISBN="978-0-262-03561-3"), isbn)[0] == "ISBN"
    assert matching_field(native("B", ISBN="9780262035613"), isbn)[0] == "ISBN"
    assert matching_field(native("B", ISBN="978-0-262-03561-4"), isbn) is None


def doi_candidates():
    return [
        native("CITER001", "Cites 10.1000/target.1", DOI="10.1000/citer.1"),
        native("LONGER01", "Longer", DOI="10.1000/target.10"),
        native("TARGET01", "Target", DOI="10.1000/Target.1"),
        native("EXTRA001", "Chapter", extra="DOI: 10.1000/target.1"),
        native("NOTE0001", "", item_type="note"),
    ]


def test_doi_search_returns_only_items_holding_the_doi(library, zot):
    zot.top.return_value = doi_candidates()

    result = library.search_items("https://doi.org/10.1000/target.1")

    assert [item["key"] for item in result["items"]] == ["TARGET01", "EXTRA001"]
    assert result["items"][0]["doi"] == "10.1000/Target.1"
    assert result["total"] == 2 and result["next_offset"] is None and result["offset"] == 0
    assert result["identifier_lookup"] == {
        "type": "doi",
        "value": "10.1000/target.1",
        "match": "exact",
        "fields": ["DOI", "extra"],
        "found": True,
        "matches": [
            {"key": "TARGET01", "field": "DOI", "value": "10.1000/Target.1"},
            {"key": "EXTRA001", "field": "extra", "value": "10.1000/target.1"},
        ],
    }
    zot.top.assert_called_once_with(q="10.1000/target.1", qmode="everything", limit=100, start=0)
    zot.items.assert_not_called()


def test_doi_search_ignores_qmode_but_forwards_explicit_item_type(library, zot):
    zot.top.return_value = doi_candidates()

    result = library.search_items(
        "10.1000/target.1", qmode="titleCreatorYear", item_type="journalArticle"
    )

    assert result["identifier_lookup"]["found"] is True
    assert zot.top.call_args.kwargs["qmode"] == "everything"
    assert zot.top.call_args.kwargs["itemType"] == "journalArticle"


def test_missing_doi_reports_not_found_instead_of_returning_citers(library, zot):
    zot.top.return_value = [doi_candidates()[0], doi_candidates()[1]]

    result = library.search_items("doi:10.1000/target.1")

    assert result["items"] == [] and result["total"] == 0 and result["next_offset"] is None
    lookup = result["identifier_lookup"]
    assert lookup["found"] is False and lookup["matches"] == []
    assert "No item in the library has this DOI in DOI or extra" in lookup["message"]


def test_doi_search_pages_over_exact_matches_only(library, zot):
    zot.top.return_value = doi_candidates()

    first = library.search_items("10.1000/target.1", limit=1)
    second = library.search_items("10.1000/target.1", limit=1, offset=1)
    past_end = library.search_items("10.1000/target.1", limit=1, offset=2)

    assert [i["key"] for i in first["items"]] == ["TARGET01"]
    assert first["total"] == 2 and first["next_offset"] == 1
    assert [i["key"] for i in second["items"]] == ["EXTRA001"]
    assert second["next_offset"] is None and second["offset"] == 1
    assert past_end["items"] == [] and past_end["identifier_lookup"]["found"] is True


def test_doi_search_applies_tags_to_matches_for_every_scope(library, zot):
    tagged = native("TARGET01", DOI="10.1000/target.1", tags=[{"tag": "keep"}])
    untagged = native("EXTRA001", extra="DOI: 10.1000/target.1")
    zot.top.return_value = [tagged, untagged]

    for scope in ("matched_item", "parent", "either"):
        result = library.search_items("10.1000/target.1", tags=["keep"], tag_scope=scope)
        assert [i["key"] for i in result["items"]] == ["TARGET01"]
    assert library.search_items("10.1000/target.1", tags=["-keep"])["items"][0]["key"] == "EXTRA001"
    assert "tag" not in zot.top.call_args.kwargs
    zot.item.assert_not_called()


def test_doi_search_raw_view_is_the_native_record(library, zot):
    zot.top.return_value = doi_candidates()
    result = library.search_items("10.1000/target.1", view="raw", limit=1)
    assert result["items"] == [doi_candidates()[2]]
    assert result["identifier_lookup"]["matches"][0]["key"] == "TARGET01"


def test_isbn_search_queries_both_spellings_and_filters_exactly(library, zot):
    hyphenated = native("BOOK0013", "Book", item_type="book", ISBN="978-0-262-03561-3")
    old_edition = native("BOOK0010", "Book", item_type="book", ISBN="0-262-03561-8")
    near_miss = native("BOOK0099", "Other", item_type="book", ISBN="978-0-262-03561-4")
    zot.top.side_effect = [[hyphenated, near_miss], [old_edition, hyphenated]]

    result = library.search_items("9780262035613")

    assert [i["key"] for i in result["items"]] == ["BOOK0013", "BOOK0010"]
    assert result["total"] == 2
    assert result["identifier_lookup"]["type"] == "isbn"
    assert result["identifier_lookup"]["fields"] == ["ISBN"]
    assert result["identifier_lookup"]["value"] == "9780262035613"
    queries = [call.kwargs["q"] for call in zot.top.call_args_list]
    assert queries == ["9%7%8%0%2%6%2%0%3%5%6%1%3", "0%2%6%2%0%3%5%6%1%8"]


def test_candidate_fetching_is_bounded_and_reports_truncation(library, zot):
    zot.top.side_effect = lambda **kwargs: [
        native(f"CAND{kwargs['start'] + n:04d}", DOI="10.1000/other") for n in range(100)
    ]

    result = library.search_items("10.1000/target.1")

    assert zot.top.call_count == IDENTIFIER_CANDIDATE_CAP // 100
    assert result["items"] == [] and result["identifier_lookup"]["candidates_truncated"] is True


def test_plain_queries_keep_native_search_and_have_no_identifier_lookup(library, zot):
    zot.items.return_value = [native("PAPER001")]

    result = library.search_items("attention is all you need")

    zot.top.assert_not_called()
    assert "identifier_lookup" not in result
    assert result["items"][0]["key"] == "PAPER001"


async def test_identifier_lookup_is_exposed_and_executed_through_mcp(library, zot):
    from mcp.shared.memory import create_connected_server_and_client_session

    from zotero_mcp.server import build_server

    zot.top.return_value = [native("PAPER001", DOI="10.1234/example")]
    async with create_connected_server_and_client_session(build_server(library)) as session:
        tool = next(
            t for t in (await session.list_tools()).tools if t.name == "zotero_search_items"
        )
        result = await session.call_tool(
            "zotero_search_items", {"query": "https://doi.org/10.1234/example"}
        )

    assert "identifier_lookup" in tool.outputSchema["properties"]
    assert result.isError is False
    assert result.structuredContent["items"][0]["key"] == "PAPER001"
    assert result.structuredContent["identifier_lookup"]["found"] is True
