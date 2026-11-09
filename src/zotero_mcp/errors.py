"""Stable, machine-readable errors at the library and MCP boundaries."""

from __future__ import annotations

from typing import Any

import httpx2
from pyzotero import (
    ConflictError,
    CouldNotReachURLError,
    FileDoesNotExistError,
    InvalidItemFieldsError,
    LocalAPIDeniedError,
    LocalAPIKeyRequiredError,
    ParamNotPassedError,
    PreConditionFailedError,
    PreConditionRequiredError,
    PyZoteroError,
    RequestEntityTooLargeError,
    ResourceNotFoundError,
    ServerIDMismatchError,
    ServerIDRequiredError,
    TooManyItemsError,
    TooManyRequestsError,
    TooManyRetriesError,
    UnsupportedParamsError,
    UploadError,
    UserNotAuthorisedError,
)
from pyzotero import (
    HTTPError as ZoteroHTTPError,
)

# These values are part of the tool contract. Keep them stable when messages change.
INVALID_ARGUMENT = "INVALID_ARGUMENT"
NOT_FOUND = "NOT_FOUND"
NOT_CITABLE = "NOT_CITABLE"
CITATION_DATA_INVALID = "CITATION_DATA_INVALID"
NO_INDEX = "NO_INDEX"
UNAUTHORIZED = "UNAUTHORIZED"
VERSION_CONFLICT = "VERSION_CONFLICT"
CONNECTION_ERROR = "CONNECTION_ERROR"
UNKNOWN_WRITE_RESULT = "UNKNOWN_WRITE_RESULT"
CHILD_ITEM_NOT_ALLOWED = "CHILD_ITEM_NOT_ALLOWED"
ATTACHMENT_NAME_AMBIGUOUS = "ATTACHMENT_NAME_AMBIGUOUS"
ATTACHMENT_CONTENT_CONFLICT = "ATTACHMENT_CONTENT_CONFLICT"
ATTACHMENT_CONTENT_UNAVAILABLE = "ATTACHMENT_CONTENT_UNAVAILABLE"
UNKNOWN_ERROR = "UNKNOWN_ERROR"
LIBRARY_LOCKED = "LIBRARY_LOCKED"
UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"


class LibraryError(ValueError, RuntimeError):
    """An operation failure with a stable code and recovery hint.

    The dual ValueError/RuntimeError base keeps compatibility with callers of the
    pre-contract API while every boundary now receives the same structured fields.
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        details: Any = None,
    ) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("LibraryError code must be a non-empty string.")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("LibraryError message must be a non-empty string.")
        self.code = code
        self.message = message
        self.retryable = bool(retryable)
        self.details = details
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        """Return the stable error payload used in structured MCP results."""
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details is not None:
            payload["details"] = self.details
        return {"error": payload}


def no_index_error(message: str = "No indexed attachment text is available.") -> LibraryError:
    """Build the actionable error returned when Zotero has no readable index."""
    if "index the attachment" not in message.lower():
        message = f"{message} Index the attachment in Zotero first."
    return LibraryError(NO_INDEX, message, retryable=False)


def _unwrap(exc: BaseException) -> BaseException:
    """Find a framework-wrapped error without crossing typed library failures."""
    current = exc
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (LibraryError, PyZoteroError)):
            return current
        cause = current.__cause__ or current.__context__
        if cause is None:
            break
        current = cause
    return current


def _validation_details(exc: BaseException) -> Any:
    """Extract JSON-safe pydantic details without coupling core errors to MCP."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return None
    try:
        return errors(include_context=False)
    except (TypeError, ValueError):
        try:
            return errors()
        except Exception:  # pragma: no cover - defensive for third-party validators
            return None


LIMIT_MESSAGE = (
    "limit must be between 1 and 100. To read more, request pages of at most 100 and pass "
    "the returned next_offset as offset until next_offset is null."
)


def _is_limit_violation(details: Any) -> bool:
    return isinstance(details, list) and any(
        isinstance(entry, dict) and list(entry.get("loc", ())) == ["limit"] for entry in details
    )


def from_exception(
    exc: BaseException,
    *,
    write: bool = False,
    operation: str | None = None,
) -> LibraryError:
    """Map pyzotero, transport and validation failures to contract errors."""
    # Keep pyzotero's typed error at the top of its own cause chain. Its cause
    # is often an httpx status exception that would otherwise look like a drop.
    if isinstance(exc, LibraryError):
        return exc
    original = exc if isinstance(exc, PyZoteroError) else _unwrap(exc)
    if isinstance(original, LibraryError):
        return original

    if isinstance(original, ServerIDRequiredError):
        return LibraryError(
            UNSUPPORTED_VERSION,
            "The local Zotero API did not provide its server ID; use a supported Zotero version.",
            retryable=False,
        )
    if isinstance(
        original,
        (
            LocalAPIKeyRequiredError,
            LocalAPIDeniedError,
            UserNotAuthorisedError,
            PreConditionRequiredError,
        ),
    ):
        return LibraryError(
            UNAUTHORIZED,
            "Local Zotero authorization is missing or no longer valid. Run zotero-mcp authorize.",
            retryable=False,
            details={"operation": operation} if operation else None,
        )
    if isinstance(original, ServerIDMismatchError):
        return LibraryError(
            VERSION_CONFLICT,
            "Zotero's database/server ID changed. Re-read the item, then authorize the active profile.",
            retryable=False,
            details={"operation": operation} if operation else None,
        )
    if isinstance(original, PreConditionFailedError):
        return LibraryError(
            VERSION_CONFLICT,
            "The item or collection changed. Re-read its current local version before retrying.",
            retryable=False,
            details={"operation": operation} if operation else None,
        )
    if isinstance(original, ResourceNotFoundError):
        return LibraryError(
            NOT_FOUND,
            "The requested Zotero item, collection or attachment was not found.",
            retryable=False,
            details={"operation": operation} if operation else None,
        )
    if isinstance(original, (UnsupportedParamsError, ParamNotPassedError, InvalidItemFieldsError)):
        return LibraryError(
            INVALID_ARGUMENT, str(original) or "The supplied arguments are invalid."
        )
    if isinstance(original, (TooManyItemsError, FileDoesNotExistError, RequestEntityTooLargeError)):
        return LibraryError(
            INVALID_ARGUMENT, str(original) or "The supplied arguments are invalid."
        )
    if isinstance(original, ConflictError):
        return LibraryError(
            LIBRARY_LOCKED,
            "Zotero is busy or its library is locked. Check Zotero and try again.",
            retryable=not write,
        )
    if isinstance(original, TooManyRequestsError):
        return LibraryError(
            UNKNOWN_WRITE_RESULT if write else CONNECTION_ERROR,
            "Zotero rate-limited the request; inspect the library before repeating a write."
            if write
            else "Zotero rate-limited the request; try again later.",
            retryable=False if write else True,
        )

    if isinstance(
        original, (CouldNotReachURLError, UploadError, TooManyRetriesError, ZoteroHTTPError)
    ):
        return LibraryError(
            UNKNOWN_WRITE_RESULT if write else CONNECTION_ERROR,
            "The Zotero connection ended before the operation was confirmed. Inspect the library before retrying."
            if write
            else "Could not complete the connection to Zotero.",
            retryable=False if write else True,
            details={"operation": operation} if operation else None,
        )
    if isinstance(original, (httpx2.HTTPError, ConnectionError, TimeoutError, OSError)):
        return LibraryError(
            UNKNOWN_WRITE_RESULT if write else CONNECTION_ERROR,
            "The Zotero connection ended before the operation was confirmed. Inspect the library before retrying."
            if write
            else "Could not complete the connection to Zotero.",
            retryable=False if write else True,
            details={"operation": operation} if operation else None,
        )

    message = str(original).strip()
    if "no indexed attachment" in message.lower() or "index the attachment" in message.lower():
        return no_index_error(message)
    if isinstance(original, (TypeError, ValueError, KeyError, IndexError)):
        details = _validation_details(original)
        if _is_limit_violation(details):
            message = LIMIT_MESSAGE
        return LibraryError(
            INVALID_ARGUMENT,
            message or "The supplied arguments are invalid.",
            retryable=False,
            details=details,
        )

    return LibraryError(
        UNKNOWN_WRITE_RESULT if write else UNKNOWN_ERROR,
        "The write may have changed Zotero; inspect the library before repeating it."
        if write
        else (message or "The Zotero operation failed unexpectedly."),
        retryable=False,
        details={"operation": operation, "exception": type(original).__name__}
        if operation
        else {"exception": type(original).__name__},
    )


__all__ = [
    "CHILD_ITEM_NOT_ALLOWED",
    "CONNECTION_ERROR",
    "INVALID_ARGUMENT",
    "LIBRARY_LOCKED",
    "LibraryError",
    "NO_INDEX",
    "NOT_FOUND",
    "NOT_CITABLE",
    "CITATION_DATA_INVALID",
    "UNKNOWN_ERROR",
    "UNKNOWN_WRITE_RESULT",
    "UNAUTHORIZED",
    "UNSUPPORTED_VERSION",
    "VERSION_CONFLICT",
    "from_exception",
    "no_index_error",
]
