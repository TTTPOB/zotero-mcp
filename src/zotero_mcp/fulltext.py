"""Small, stateless helpers for selecting and reading indexed attachment text."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from zotero_mcp.fulltext_models import (
    FulltextCandidate,
    FulltextChunk,
    IndexCoverage,
    ReadingCoverage,
)


class FulltextError(ValueError):
    """A fulltext failure with fields suitable for the shared library error mapper."""

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details


def text_revision(content: str) -> str:
    """Return a stable revision for the exact Unicode text returned by Zotero."""
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _attachment_data(attachment: Mapping[str, Any]) -> Mapping[str, Any]:
    data = attachment.get("data", {})
    return data if isinstance(data, Mapping) else {}


def attachment_candidate(attachment: Mapping[str, Any]) -> FulltextCandidate:
    """Project one attachment without exposing the full Zotero item."""
    data = _attachment_data(attachment)
    attachment_key = attachment.get("key")
    if not isinstance(attachment_key, str) or not attachment_key:
        raise FulltextError(
            "FULLTEXT_ATTACHMENT_INVALID",
            "Zotero returned an attachment without a valid key.",
        )
    content_type = data.get("contentType")
    return {
        "attachment_key": attachment_key,
        "title": data.get("title") if isinstance(data.get("title"), str) else None,
        "filename": data.get("filename") if isinstance(data.get("filename"), str) else None,
        "content_type": content_type if isinstance(content_type, str) else None,
        "index_state": "unknown",
    }


def select_attachment(
    attachments: Iterable[Mapping[str, Any]], *, parent_key: str
) -> Mapping[str, Any]:
    """Select only an unambiguous preferred attachment; never probe and silently fall back."""
    candidates = list(attachments)
    if not candidates:
        raise FulltextError(
            "FULLTEXT_ATTACHMENT_NOT_FOUND",
            "No indexed attachment text is available. Index the attachment in Zotero first or provide an attachment key.",
            details={"parent_key": parent_key, "candidates": []},
        )
    if len(candidates) == 1:
        return candidates[0]

    pdfs = []
    for attachment in candidates:
        content_type = _attachment_data(attachment).get("contentType")
        if isinstance(content_type, str) and content_type.lower() == "application/pdf":
            pdfs.append(attachment)
    if len(pdfs) == 1:
        return pdfs[0]

    limited_candidates = [attachment_candidate(attachment) for attachment in candidates[:5]]
    reason = "multiple PDF attachments" if len(pdfs) > 1 else "multiple attachments"
    raise FulltextError(
        "FULLTEXT_ATTACHMENT_AMBIGUOUS",
        "Multiple attachments could contain the requested text; provide attachment_key.",
        details={
            "parent_key": parent_key,
            "reason": reason,
            "candidate_count": len(candidates),
            "candidates": limited_candidates,
        },
    )


def parse_fulltext_response(response: Any, *, attachment_key: str) -> tuple[str, dict[str, Any]]:
    """Validate the small part of Zotero's fulltext response used by this module."""
    if not isinstance(response, Mapping):
        raise FulltextError(
            "FULLTEXT_RESPONSE_INVALID",
            "Zotero returned an invalid fulltext response.",
            details={"attachment_key": attachment_key},
        )
    content = response.get("content", "")
    if not isinstance(content, str):
        raise FulltextError(
            "FULLTEXT_RESPONSE_INVALID",
            "Zotero returned non-text fulltext content.",
            details={"attachment_key": attachment_key},
        )
    return content, dict(response)


def coverage(
    response: Mapping[str, Any], *, start: int, end: int, total: int
) -> tuple[IndexCoverage, ReadingCoverage]:
    """Build explicit index and reading coverage without inferring page accuracy."""
    indexed_pages = response.get("indexedPages")
    total_pages = response.get("totalPages")
    index_coverage: IndexCoverage = {
        "indexed_pages": indexed_pages if isinstance(indexed_pages, int) else None,
        "total_pages": total_pages if isinstance(total_pages, int) else None,
    }
    reading_coverage: ReadingCoverage = {
        "start": start,
        "end": end,
        "total_characters": total,
        "complete": end >= total,
    }
    return index_coverage, reading_coverage


def make_chunk(
    *,
    attachment_key: str,
    response: Mapping[str, Any],
    content: str,
    offset: int,
    max_characters: int,
) -> FulltextChunk:
    end = min(offset + max_characters, len(content))
    index = {key: value for key, value in response.items() if key != "content"}
    index_coverage, reading_coverage = coverage(response, start=offset, end=end, total=len(content))
    return {
        "attachment_key": attachment_key,
        "content": content[offset:end],
        "offset": offset,
        "next_offset": end if end < len(content) else None,
        "total_characters": len(content),
        "text_revision": text_revision(content),
        "offset_unit": "unicode_code_point",
        "offset_base": 0,
        "index": index,
        "index_coverage": index_coverage,
        "reading_coverage": reading_coverage,
    }
