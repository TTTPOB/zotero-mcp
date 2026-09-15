"""Typed views for Zotero-indexed fulltext reads."""

from typing import Annotated, Any

from pydantic import Field
from typing_extensions import TypedDict

FulltextItemKey = Annotated[str, Field(min_length=8, max_length=8, pattern=r"^[A-Za-z0-9]{8}$")]
FulltextAttachmentKey = Annotated[
    str, Field(min_length=8, max_length=8, pattern=r"^[A-Za-z0-9]{8}$")
]
FulltextOffset = Annotated[int, Field(ge=0)]
FulltextMaxCharacters = Annotated[int, Field(ge=1, le=50000)]
FulltextContextCharacters = Annotated[int, Field(ge=0, le=5000)]
FulltextMaxMatches = Annotated[int, Field(ge=1, le=100)]
FulltextQuery = Annotated[str, Field(min_length=1)]
FulltextRevision = Annotated[str | None, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
AttachmentTextContent = Annotated[str, Field(min_length=1, max_length=50_000_000)]
AttachmentTextOffset = Annotated[int, Field(ge=0)]
AttachmentTextMaxCharacters = Annotated[int, Field(ge=1, le=50000)]


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


class AttachmentTextChunk(TypedDict):
    """A Unicode-code-point slice of an attachment's original UTF-8 bytes."""

    source: str
    encoding: str
    attachment_key: str
    filename: str
    content_type: str
    content: str
    offset: int
    next_offset: int | None
    total_characters: int
    byte_length: int
    content_sha256: str


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
