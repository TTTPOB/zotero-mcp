"""Per-key citation data for Zotero integrations and citation-key consumers."""

from typing import Any, Literal

from pydantic import BaseModel


class CitationItemError(BaseModel):
    code: str
    message: str


class CitationItemSuccess(BaseModel):
    index: int
    key: str
    status: Literal["ok"]
    uri: str
    select_link: str
    citation_key: str | None
    item_type: str
    title: str
    csl: dict[str, Any]


class CitationItemFailure(BaseModel):
    index: int
    key: str
    status: Literal["error"]
    error: CitationItemError


class CitationItemsResult(BaseModel):
    items: list[CitationItemSuccess | CitationItemFailure]
