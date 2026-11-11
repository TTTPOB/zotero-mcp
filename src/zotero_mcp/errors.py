"""Stable, machine-readable errors at the library and MCP boundaries."""

from __future__ import annotations

import re
from typing import Any

import httpx2
from pydantic import ValidationError
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


class LibraryError(Exception):
    """An operation failure with a stable code and recovery hint."""

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


LIMIT_MESSAGE = (
    "limit must be between 1 and 100. To read more, request pages of at most 100 and pass "
    "the returned next_offset as offset until next_offset is null."
)

# At most this many validation problems are spelled out; the message states the total.
_MAX_REPORTED_PROBLEMS = 3
_MAX_DETAIL_ENTRIES = 5
# pydantic error type -> (comparison symbol, ctx key holding the bound)
_RANGE_ERRORS = {
    "greater_than": (">", "gt"),
    "greater_than_equal": (">=", "ge"),
    "less_than": ("<", "lt"),
    "less_than_equal": ("<=", "le"),
}
_KEY_ERRORS = {"string_type", "string_too_long", "string_too_short", "string_pattern_mismatch"}
KEY_FORMAT = "an 8-character Zotero key (letters and digits, e.g. ABCD2345), not a URI or title"
_VALUE_TYPES = {
    str: "a string",
    int: "an integer",
    float: "a number",
    bool: "a boolean",
    list: "a list",
    dict: "an object",
    type(None): "null",
}


def _param_path(loc: tuple[Any, ...]) -> str:
    """Render a pydantic location as item_keys[3] or options.view."""
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path or "arguments"


def _param_name(loc: tuple[Any, ...]) -> str:
    return next((part for part in reversed(loc) if isinstance(part, str)), "")


def _is_key_param(name: str) -> bool:
    return name == "parent_collection" or re.search(r"(^|_)keys?$", name) is not None


def key_source(name: str) -> str:
    """Name the tool that returns keys for a parameter."""
    if "collection" in name:
        return "get collection keys from zotero_get_collections"
    if "attachment" in name:
        return "get attachment keys from zotero_get_item_children"
    return "get item keys from zotero_search_items"


def _value_type(value: Any) -> str:
    return _VALUE_TYPES.get(type(value), type(value).__name__)


def _short(value: Any, limit: int = 40) -> str:
    """Echo a scalar input briefly; never echo containers or long strings in full."""
    if not isinstance(value, (str, int, float, bool)) and value is not None:
        return _value_type(value)
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _format_problem(entry: dict[str, Any]) -> str:
    """Turn one pydantic error (type/loc/ctx) into a short, actionable clause."""
    kind = str(entry.get("type", ""))
    loc = tuple(entry.get("loc", ()))
    ctx = entry.get("ctx") or {}
    value = entry.get("input")
    path = _param_path(loc)
    name = _param_name(loc)

    if kind == "missing":
        return f"{path} is required"
    if name == "limit" and kind in _RANGE_ERRORS:
        return LIMIT_MESSAGE.rstrip(".")
    if _is_key_param(name):
        if kind in _KEY_ERRORS:
            return f"{path} must be {KEY_FORMAT}; {key_source(name)}"
        if kind == "list_type":
            return (
                f'{path} must be a list of Zotero keys, e.g. ["ABCD2345"]; got {_value_type(value)}'
            )
    if kind == "too_long" and "max_length" in ctx:
        got = ctx.get("actual_length") or (len(value) if hasattr(value, "__len__") else "more")
        return (
            f"{path} accepts at most {ctx['max_length']} entries, got {got}; "
            "split the call into smaller batches"
        )
    if kind == "too_short" and "min_length" in ctx:
        count = ctx["min_length"]
        return f"{path} needs at least {count} {'entry' if count == 1 else 'entries'}"
    if kind == "list_type":
        hint = '; wrap a single value in a list, e.g. ["value"]' if isinstance(value, str) else ""
        return f"{path} must be a list, got {_value_type(value)}{hint}"
    if kind == "string_type":
        return f"{path} must be a string, got {_value_type(value)}"
    if kind == "string_too_long" and "max_length" in ctx:
        got = f", got {len(value)}" if isinstance(value, str) else ""
        return f"{path} must be at most {ctx['max_length']} characters{got}"
    if kind == "string_too_short" and "min_length" in ctx:
        return f"{path} must be at least {ctx['min_length']} characters"
    if kind == "literal_error" and "expected" in ctx:
        return f"{path} must be {ctx['expected']}; got {_short(value)}"
    if kind in {"int_parsing", "int_type", "int_from_float"}:
        return f"{path} must be an integer, got {_short(value)}"
    if kind in {"float_parsing", "float_type"}:
        return f"{path} must be a number, got {_short(value)}"
    if kind in {"bool_parsing", "bool_type"}:
        return f"{path} must be true or false, got {_short(value)}"
    if kind == "dict_type":
        return f"{path} must be an object, got {_value_type(value)}"
    if kind in _RANGE_ERRORS:
        symbol, bound_key = _RANGE_ERRORS[kind]
        return f"{path} must be {symbol} {ctx.get(bound_key, '?')}, got {_short(value)}"
    if kind in {"extra_forbidden", "unexpected_keyword_argument"}:
        return f"{path} is not a supported parameter"
    return f"{path}: {entry.get('msg', 'invalid value')}".rstrip(".")


def format_validation_error(exc: ValidationError) -> tuple[str, list[dict[str, Any]]]:
    """Summarize a pydantic ValidationError without echoing its input or doc URLs.

    Returns the message and JSON-safe details (type, loc, msg) for the first few problems.
    """
    entries = exc.errors(include_url=False, include_context=True, include_input=True)
    clauses = [_format_problem(entry) for entry in entries[:_MAX_REPORTED_PROBLEMS]]
    total = len(entries)
    if total == 1:
        message = f"{clauses[0]}."
    else:
        listed = "; ".join(clauses)
        extra = f"; {total - len(clauses)} more not shown" if total > len(clauses) else ""
        message = f"{total} invalid arguments: {listed}{extra}."
    details = [
        {"type": entry["type"], "loc": list(entry["loc"]), "msg": entry["msg"]}
        for entry in entries[:_MAX_DETAIL_ENTRIES]
    ]
    return message, details


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
        original,
        (
            CouldNotReachURLError,
            UploadError,
            TooManyRetriesError,
            ZoteroHTTPError,
            httpx2.HTTPError,
            ConnectionError,
            TimeoutError,
            OSError,
        ),
    ):
        return LibraryError(
            UNKNOWN_WRITE_RESULT if write else CONNECTION_ERROR,
            "The Zotero connection ended before the operation was confirmed. Inspect the library before retrying."
            if write
            else "Could not complete the connection to Zotero.",
            retryable=False if write else True,
            details={"operation": operation} if operation else None,
        )

    if isinstance(original, ValidationError):
        message, details = format_validation_error(original)
        return LibraryError(INVALID_ARGUMENT, message, retryable=False, details=details)

    message = str(original).strip()
    if isinstance(original, (TypeError, ValueError, KeyError, IndexError)):
        return LibraryError(
            INVALID_ARGUMENT,
            message or "The supplied arguments are invalid.",
            retryable=False,
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
    "NOT_FOUND",
    "NOT_CITABLE",
    "CITATION_DATA_INVALID",
    "UNKNOWN_ERROR",
    "UNKNOWN_WRITE_RESULT",
    "UNAUTHORIZED",
    "UNSUPPORTED_VERSION",
    "VERSION_CONFLICT",
    "format_validation_error",
    "from_exception",
]
