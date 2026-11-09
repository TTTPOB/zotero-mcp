"""Small, stateless helpers for selecting and reading indexed attachment text."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from zotero_mcp.errors import LibraryError
from zotero_mcp.fulltext_models import (
    FulltextCandidate,
    FulltextChunk,
    FulltextFindResult,
    FulltextMatch,
    IndexCoverage,
    ReadingCoverage,
)


class FulltextError(LibraryError):
    """A fulltext failure retaining its historical class name and fields."""

    pass


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


def preferred_attachment_key(item: Mapping[str, Any]) -> str | None:
    """Extract an existing enclosure key without fetching its URL."""
    links = item.get("links")
    if not isinstance(links, Mapping):
        return None
    enclosure = links.get("enclosure")
    values = enclosure if isinstance(enclosure, list) else [enclosure]
    for value in values:
        fields = value if isinstance(value, Mapping) else {"value": value}
        for field in ("key", "attachment_key", "itemKey", "href", "url", "value"):
            candidate = fields.get(field)
            if not isinstance(candidate, str):
                continue
            match = re.fullmatch(r"[A-Za-z0-9]{8}", candidate)
            if match:
                return candidate.upper()
            path = urlparse(candidate).path
            match = re.search(r"(?:^|/)(?:items|attachments)/([A-Za-z0-9]{8})(?:/|$)", path)
            if match:
                return match.group(1).upper()
    return None


def select_attachment(
    attachments: Iterable[Mapping[str, Any]],
    *,
    parent_key: str,
    preferred_attachment_key: str | None = None,
) -> Mapping[str, Any]:
    """Select a confirmed enclosure, then a unique PDF, without probing fallbacks."""
    candidates = list(attachments)
    if not candidates:
        raise FulltextError(
            "FULLTEXT_ATTACHMENT_NOT_FOUND",
            "This item has no attachments, so there is no text to read.",
            details={"parent_key": parent_key, "candidates": []},
        )
    if preferred_attachment_key:
        matches = [
            attachment
            for attachment in candidates
            if isinstance(attachment.get("key"), str)
            and attachment["key"].upper() == preferred_attachment_key.upper()
        ]
        if len(matches) == 1:
            return matches[0]
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


# Zotero indexes asynchronously after an attachment is added; this long after
# dateAdded, an empty index is more likely a real gap than work in progress.
RECENT_ATTACHMENT_WINDOW = timedelta(minutes=10)
_UNINDEXABLE_CONTENT_PREFIXES = ("image/", "audio/", "video/")


def _recently_added(data: Mapping[str, Any], now: datetime | None) -> bool:
    added = data.get("dateAdded")
    if not isinstance(added, str):
        return False
    try:
        added_at = datetime.fromisoformat(added.replace("Z", "+00:00"))
    except ValueError:
        return False
    if added_at.tzinfo is None:
        added_at = added_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return timedelta(0) <= current - added_at < RECENT_ATTACHMENT_WINDOW


def unindexed_attachment_error(
    attachment_key: str, attachment: Mapping[str, Any], *, now: datetime | None = None
) -> FulltextError:
    """Explain why an attachment has no indexed text and whether waiting can help.

    Zotero answers 404 for a missing index whatever the cause, so this inspects the
    attachment: a linked URL or media file is never indexed (not retryable); a
    recently added file may still be queued (retryable); anything else is probably
    not indexed and needs a reindex in Zotero (not retryable).
    """
    data = _attachment_data(attachment)
    link_mode = data.get("linkMode") if isinstance(data.get("linkMode"), str) else None
    content_type = data.get("contentType") if isinstance(data.get("contentType"), str) else None
    details: dict[str, Any] = {"attachment_key": attachment_key}
    if link_mode == "linked_url" or (
        content_type and content_type.lower().startswith(_UNINDEXABLE_CONTENT_PREFIXES)
    ):
        details.update(reason="not_indexable", link_mode=link_mode, content_type=content_type)
        return FulltextError(
            "FULLTEXT_NOT_INDEXED",
            "This attachment is a link or a media file, which Zotero does not index, so it "
            "has no text to read. Choose another attachment.",
            details=details,
        )
    if _recently_added(data, now):
        details["reason"] = "indexing_pending"
        return FulltextError(
            "FULLTEXT_NOT_INDEXED",
            "No indexed text is available yet. The attachment was added recently and Zotero "
            "may still be indexing it; try again in a few seconds. If it stays empty, "
            "rebuild the index in Zotero.",
            retryable=True,
            details=details,
        )
    details["reason"] = "not_indexed"
    return FulltextError(
        "FULLTEXT_NOT_INDEXED",
        "No indexed text is available for this attachment. If it was just added, Zotero may "
        "still be indexing it and a later retry can work; otherwise rebuild the index in "
        "Zotero (scanned PDFs without a text layer are never indexed).",
        details=details,
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
        "reached_end": end == total,
        "complete": start == 0 and end == total,
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
    if offset > len(content):
        raise FulltextError(
            "FULLTEXT_OFFSET_OUT_OF_RANGE",
            "offset is beyond the indexed text; use an offset up to total_characters.",
            details={"offset": offset, "total_characters": len(content)},
        )
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


def _fold_with_positions(content: str) -> tuple[str, list[int]]:
    folded: list[str] = []
    positions: list[int] = []
    for position, character in enumerate(content):
        folded_character = character.casefold()
        folded.append(folded_character)
        positions.extend([position] * len(folded_character))
    return "".join(folded), positions


def find_matches(
    content: str, query: str, context_characters: int, max_matches: int
) -> tuple[list[FulltextMatch], int]:
    """Find literal matches and count unique source spans after Unicode case folding."""
    folded_content, positions = _fold_with_positions(content)
    folded_query = query.casefold()
    matches: list[FulltextMatch] = []
    seen_spans: set[tuple[int, int]] = set()
    total_matches = 0
    if not folded_query:
        return matches, total_matches

    cursor = 0
    while True:
        folded_start = folded_content.find(folded_query, cursor)
        if folded_start < 0:
            break
        folded_end = folded_start + len(folded_query)
        if not positions or folded_end > len(positions):
            break
        start = positions[folded_start]
        end = positions[folded_end - 1] + 1
        span = (start, end)
        if span not in seen_spans:
            seen_spans.add(span)
            total_matches += 1
            if len(matches) < max_matches:
                snippet_start = max(0, start - context_characters)
                snippet_end = min(len(content), end + context_characters)
                matches.append(
                    {
                        "start": start,
                        "end": end,
                        "snippet": content[snippet_start:snippet_end],
                        "snippet_start": snippet_start,
                        "snippet_end": snippet_end,
                    }
                )
        # Advance one folded code point so overlapping literal matches are retained.
        cursor = folded_start + 1
    return matches, total_matches


def make_find_result(
    *,
    attachment_key: str,
    response: Mapping[str, Any],
    content: str,
    query: str,
    context_characters: int,
    max_matches: int,
) -> FulltextFindResult:
    matches, total_matches = find_matches(content, query, context_characters, max_matches)
    index = {key: value for key, value in response.items() if key != "content"}
    index_coverage, _ = coverage(response, start=0, end=0, total=len(content))
    has_more = total_matches > len(matches)
    return {
        "attachment_key": attachment_key,
        "query": query,
        "match_count": len(matches),
        "total_matches": total_matches,
        "has_more": has_more,
        "truncated": has_more,
        "matches": matches,
        "text_revision": text_revision(content),
        "total_characters": len(content),
        "offset_unit": "unicode_code_point",
        "offset_base": 0,
        "index": index,
        "index_coverage": index_coverage,
    }
