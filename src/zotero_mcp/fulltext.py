"""Small helpers for transparent selection of indexed attachments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from zotero_mcp.fulltext_models import FulltextCandidate


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
