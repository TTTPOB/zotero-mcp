"""Typed views for transparent Zotero attachment selection."""

from typing import TypedDict


class FulltextCandidate(TypedDict):
    """Small attachment summary returned when automatic selection is ambiguous."""

    attachment_key: str
    title: str | None
    filename: str | None
    content_type: str | None
    index_state: str
