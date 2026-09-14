"""Typed views for Zotero-indexed fulltext reads."""

from typing import Any, TypedDict


class FulltextCandidate(TypedDict):
    """Small attachment summary returned when automatic selection is ambiguous."""

    attachment_key: str
    title: str | None
    filename: str | None
    content_type: str | None
    index_state: str


class IndexCoverage(TypedDict):
    """Coverage reported by Zotero's fulltext index, when available."""

    indexed_pages: int | None
    total_pages: int | None


class ReadingCoverage(TypedDict):
    """The portion of the indexed text included in this response."""

    start: int
    end: int
    total_characters: int
    reached_end: bool
    complete: bool


class FulltextChunk(TypedDict):
    """A Unicode-code-point slice of one stable attachment's indexed text."""

    attachment_key: str
    content: str
    offset: int
    next_offset: int | None
    total_characters: int
    text_revision: str
    offset_unit: str
    offset_base: int
    index: dict[str, Any]
    index_coverage: IndexCoverage
    reading_coverage: ReadingCoverage


class FulltextMatch(TypedDict):
    """One literal, case-insensitive match and its surrounding text."""

    start: int
    end: int
    snippet: str
    snippet_start: int
    snippet_end: int


class FulltextFindResult(TypedDict):
    """Matches found in one explicitly selected attachment."""

    attachment_key: str
    query: str
    match_count: int
    total_matches: int
    has_more: bool
    truncated: bool
    matches: list[FulltextMatch]
    text_revision: str
    total_characters: int
    offset_unit: str
    offset_base: int
    index: dict[str, Any]
    index_coverage: IndexCoverage
