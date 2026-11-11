"""Local API operations. Pyzotero owns HTTP, authorization headers and versions."""

import copy
import difflib
import hashlib
import html
import json
import mimetypes
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import Field
from pyzotero import (
    PreConditionFailedError,
    ResourceNotFoundError,
    ServerIDMismatchError,
    Zotero,
)

from zotero_mcp.citation_models import CitationItemsResult
from zotero_mcp.config import Settings, load_auth
from zotero_mcp.errors import (
    ATTACHMENT_CONTENT_CONFLICT,
    ATTACHMENT_CONTENT_UNAVAILABLE,
    ATTACHMENT_NAME_AMBIGUOUS,
    CHILD_ITEM_NOT_ALLOWED,
    CITATION_DATA_INVALID,
    CONNECTION_ERROR,
    INVALID_ARGUMENT,
    NOT_CITABLE,
    NOT_FOUND,
    UNKNOWN_ERROR,
    UNKNOWN_WRITE_RESULT,
    VERSION_CONFLICT,
    LibraryError,
    from_exception,
)
from zotero_mcp.fulltext import (
    FulltextError,
    make_chunk,
    make_find_result,
    parse_fulltext_response,
    preferred_attachment_key,
    select_attachment,
    text_revision,
    unindexed_attachment_error,
)
from zotero_mcp.fulltext_models import (
    AttachmentTextChunk,
    AttachmentTextContent,
    AttachmentTextMaxCharacters,
    AttachmentTextOffset,
    FulltextAttachmentKey,
    FulltextChunk,
    FulltextContextCharacters,
    FulltextFindResult,
    FulltextItemKey,
    FulltextMaxCharacters,
    FulltextMaxMatches,
    FulltextOffset,
    FulltextQuery,
    FulltextRevision,
)
from zotero_mcp.identifiers import Identifier, matching_field, parse_identifier
from zotero_mcp.models import ItemDataList, ItemKey, ItemKeyList, Version, WriteResponse
from zotero_mcp.presentation import (
    ItemMetadataBatch,
    ItemPage,
    ReadItemKey,
    ReadLimit,
    ReadOffset,
    ReadTag,
    ReadView,
    TagPage,
    project_items,
)

QUERY_OR_TAGS_REQUIRED = "QUERY_OR_TAGS_REQUIRED"
PAPER_CHEW_MARKDOWN_FILENAME = "paper-chew-fulltext.md"
SAFE_TEXT_CONTENT_TYPES = {"text/markdown", "text/plain"}
LEGACY_MARKDOWN_CONTENT_TYPES = {"", "application/octet-stream"}
SAFE_TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
MAX_ATTACHMENT_TEXT_BYTES = 50 * 1024 * 1024
BINARY_SIGNATURES = (
    b"%PDF-",
    b"PK\x03\x04",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF8",
    b"\x1f\x8b",
    b"Rar!",
    b"\x7fELF",
)


def new_client(settings: Settings | None = None) -> Zotero:
    settings = settings or Settings.from_env()
    auth = load_auth()
    return Zotero(
        settings.library_id,
        settings.library_type,
        local=True,
        server_id=auth.server_id if auth else None,
        local_api_key=auth.key if auth else None,
    )


def key(value: str) -> str:
    value = value.upper()
    return value


def pagination(limit: int, offset: int) -> dict[str, Any]:
    return {"limit": limit, "start": offset}


def _header(headers: Mapping[str, Any] | None, name: str) -> Any:
    if headers is None:
        return None
    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    for key_name, value in headers.items():
        if str(key_name).lower() == lowered:
            return value
    return None


def _total_results(headers: Mapping[str, Any] | None) -> int | None:
    value = _header(headers, "Total-Results")
    try:
        total = int(value)
    except (TypeError, ValueError):
        return None
    return total if total >= 0 else None


def _next_link(links: Mapping[str, Any] | None, headers: Mapping[str, Any] | None) -> str | None:
    if isinstance(links, Mapping):
        value = links.get("next")
        if isinstance(value, Mapping):
            value = value.get("url")
        if isinstance(value, str):
            return value
    link_header = _header(headers, "Link")
    if not isinstance(link_header, str):
        return None
    for entry in link_header.split(","):
        if 'rel="next"' not in entry and "rel=next" not in entry:
            continue
        stripped = entry.strip()
        if stripped.startswith("<") and ">" in stripped:
            return stripped[1 : stripped.index(">")]
    return None


def _next_offset_from_link(link: str | None) -> int | None:
    if not link:
        return None
    try:
        values = parse_qs(urlparse(link).query).get("start", [])
        return int(values[0]) if values and int(values[0]) >= 0 else None
    except (TypeError, ValueError):
        return None


def page(
    items: list,
    limit: int,
    offset: int,
    *,
    view: ReadView = "raw",
    consumed_count: int | None = None,
    links: Mapping[str, Any] | None = None,
    headers: Mapping[str, Any] | None = None,
) -> ItemPage:
    """Build a projected page using native response pagination when available."""
    projected = project_items(items, view)
    upstream_count = len(items) if consumed_count is None else consumed_count

    total = _total_results(headers)
    next_link = _next_link(links, headers)
    next_offset = _next_offset_from_link(next_link)
    if next_offset is None and total is not None:
        candidate = offset + upstream_count
        next_offset = candidate if upstream_count > 0 and candidate < total else None
    if next_offset is None and total is None:
        # A full last page can require one final empty request when no response
        # pagination metadata is available. This remains a bounded fallback.
        next_offset = offset + upstream_count if upstream_count == limit else None

    result: dict[str, Any] = {
        "items": projected,
        "offset": offset,
        "next_offset": next_offset,
    }
    if total is not None:
        result["total"] = total
    return result


# Upper bound on Zotero hits examined per identifier query (100 per request).
IDENTIFIER_CANDIDATE_CAP = 500


def _item_tags(item: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(item, Mapping):
        return set()
    data = item.get("data")
    if not isinstance(data, Mapping):
        return set()
    tags = data.get("tags")
    if not isinstance(tags, list):
        return set()
    return {
        tag["tag"] for tag in tags if isinstance(tag, Mapping) and isinstance(tag.get("tag"), str)
    }


def _tag_expression_matches(expression: str, actual: set[str]) -> bool:
    alternatives = [part.strip() for part in expression.split("||") if part.strip()]
    if not alternatives:
        return False
    for alternative in alternatives:
        if alternative.startswith("-"):
            if alternative[1:] not in actual:
                return True
        elif alternative in actual:
            return True
    return False


def _tags_match(item: Mapping[str, Any] | None, tags: list[str]) -> bool:
    actual = _item_tags(item)
    return all(_tag_expression_matches(expression, actual) for expression in tags)


def _response_key(value: Any) -> str | None:
    """Extract a created key from the response shapes returned by pyzotero."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        candidate = value.get("key")
        return candidate if isinstance(candidate, str) else None
    return None


def _response_version(value: Any) -> int | None:
    """Take an entity version from a native saved object, never a library header."""
    candidate = value.get("version") if isinstance(value, dict) else None
    return candidate if type(candidate) is int and candidate >= 0 else None


def _stored_item_warnings(
    sent: dict[str, Any], stored: dict[str, Any], previous_extra: str = ""
) -> list[str]:
    """Flag dropped fields and creator-role replacement, not routine normalization."""
    warnings = []
    item_type = stored.get("itemType", sent.get("itemType", "item"))
    extra_changed = stored.get("extra", "") != sent.get("extra", previous_extra)
    for field, value in sent.items():
        if value in (None, "", [], {}) or field in stored:
            continue
        if value in stored.values():
            continue  # A base field may have been mapped to its item-type-specific name.
        outcome = (
            "Zotero stored the item without it and changed extra instead"
            if extra_changed
            else "Zotero did not store it"
        )
        warnings.append(
            f"{field} is not a {item_type} field: {outcome}. "
            "Check zotero_get_item_schema for valid fields."
        )
    for index, creator in enumerate(sent.get("creators", [])):
        saved_creators = stored.get("creators", [])
        if index >= len(saved_creators):
            break
        original = creator.get("creatorType")
        saved = saved_creators[index].get("creatorType")
        if original != saved:
            warnings.append(
                f"creators[{index}].creatorType {original} is not valid for {item_type}: "
                f"Zotero stored {saved}. Check zotero_get_item_schema for valid creator types."
            )
    return warnings


def _result_status(entries: list[dict[str, Any]]) -> str:
    statuses = {entry["status"] for entry in entries}
    if not statuses or statuses <= {"success", "unchanged"}:
        return "success"
    if "unknown" in statuses:
        return "unknown"
    if statuses <= {"failed"}:
        return "failed"
    return "partial"


def _indexed_results(result: dict, field: str) -> dict[str, Any]:
    value = result.get(field) or {}
    if not isinstance(value, dict):
        raise LibraryError(
            UNKNOWN_WRITE_RESULT,
            f"Zotero returned malformed {field} results; inspect the library before retrying.",
        )
    for index in value:
        if not str(index).isdigit():
            raise LibraryError(
                UNKNOWN_WRITE_RESULT,
                "Zotero returned a non-indexed write result; inspect the library before retrying.",
            )
    return {str(index): item for index, item in value.items()}


def write_result(
    result: dict,
    *,
    input_keys: list[str | None] | None = None,
    input_count: int | None = None,
    input_data: list[dict[str, Any]] | None = None,
    single: bool = False,
) -> dict[str, Any]:
    """Normalize pyzotero's indexed create response.

    A missing index is deliberately ``unknown``: the server may have applied an
    input before dropping its response, so it is never safe to replay blindly.
    """
    if not isinstance(result, dict):
        raise LibraryError(
            UNKNOWN_WRITE_RESULT,
            "Zotero returned an unexpected write response; inspect the library before retrying.",
        )
    if not {"successful", "success", "unchanged", "failed"} & result.keys():
        raise LibraryError(
            UNKNOWN_WRITE_RESULT,
            "Zotero returned an unexpected write response; inspect the library before retrying.",
        )
    successful = _indexed_results(result, "successful")
    success = _indexed_results(result, "success")
    unchanged = _indexed_results(result, "unchanged")
    failed = _indexed_results(result, "failed")
    if not any((successful, success, unchanged, failed)):
        raise LibraryError(
            UNKNOWN_WRITE_RESULT,
            "Zotero returned no item results; inspect the library before retrying.",
        )

    if input_keys is not None:
        input_count = len(input_keys)
    indexes = {
        int(index) for values in (successful, success, unchanged, failed) for index in values
    }
    if input_count is not None:
        if type(input_count) is not int or input_count < 1:
            raise LibraryError(
                UNKNOWN_WRITE_RESULT,
                "The write input count is invalid; inspect the library before retrying.",
            )
        indexes.update(range(input_count))

    unknown: dict[str, Any] = {}
    entries: list[dict[str, Any]] = []
    for index in sorted(indexes):
        source_key = (
            input_keys[index] if input_keys is not None and index < len(input_keys) else None
        )
        index_key = str(index)
        if index_key in failed:
            status, value = "failed", failed[index_key]
        elif index_key in successful:
            status, value = "success", successful[index_key]
        elif index_key in success:
            status, value = "success", success[index_key]
        elif index_key in unchanged:
            status, value = "unchanged", unchanged[index_key]
        else:
            value = {
                "code": UNKNOWN_WRITE_RESULT,
                "message": "Zotero omitted this input's result; inspect the library before retrying.",
                "retryable": False,
            }
            unknown[index_key] = value
            status = "unknown"
        entry: dict[str, Any] = {
            "input_index": index,
            "status": status,
            **(
                {"key": source_key or _response_key(value)}
                if source_key or _response_key(value)
                else {}
            ),
        }
        if status == "failed":
            http_status = value["code"]
            entry["error"] = {
                "code": {400: INVALID_ARGUMENT, 404: NOT_FOUND, 412: VERSION_CONFLICT}.get(
                    http_status, UNKNOWN_ERROR
                ),
                "message": value["message"],
                "retryable": False,
                "details": {"http_status": http_status},
            }
        elif status == "unknown":
            entry["error"] = value
        elif (version := _response_version(value)) is not None:
            entry["version"] = version
        if status == "success" and input_data is not None and isinstance(value, dict):
            stored = value.get("data")
            if isinstance(stored, dict):
                warnings = _stored_item_warnings(input_data[index], stored)
                if warnings:
                    entry["warnings"] = warnings
        entries.append(entry)

    normalized = {
        "ok": not any(entry["status"] in {"failed", "unknown"} for entry in entries),
        "status": _result_status(entries),
        "results": entries,
    }
    if unknown:
        normalized["error"] = {
            "code": UNKNOWN_WRITE_RESULT,
            "message": "Some write results were not returned; inspect the library before retrying.",
            "retryable": False,
        }
    if single:
        normalized.update({k: entries[0][k] for k in ("key", "version") if k in entries[0]})
    return normalized


def _single_write_result(
    entity_key: str | None = None,
    version: int | None = None,
    *,
    status: str = "success",
    stage: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one compact single-target write outcome."""
    entity = {
        name: value
        for name, value in {"key": entity_key, "version": version}.items()
        if value is not None
    }
    outcome = {
        name: value for name, value in {"stage": stage, "error": error}.items() if value is not None
    }
    return {
        "ok": status in {"success", "unchanged"},
        "status": "success" if status == "unchanged" else status,
        "results": [{"input_index": 0, "status": status, **entity, **outcome}],
        **entity,
        **({"error": error} if error is not None else {}),
    }


def _not_found_error(kind: str, entity_key: str, operation_name: str) -> LibraryError:
    finder = "get_collections" if kind == "collection" else "search_items"
    return LibraryError(
        NOT_FOUND,
        f"The {kind} {entity_key} does not exist; it may have been deleted or the key is "
        f"wrong. Search again with {finder} to get a current key.",
        retryable=False,
        details={"operation": operation_name, "key": entity_key},
    )


def _version_conflict_error(
    kind: str, entity_key: str, operation_name: str, current_version: int | None
) -> LibraryError:
    details: dict[str, Any] = {"operation": operation_name, "key": entity_key}
    if current_version is None:
        message = (
            f"The {kind} {entity_key} changed. Re-read its current local version before retrying."
        )
    else:
        details["current_version"] = current_version
        message = (
            f"The {kind} {entity_key} changed since you read it; its current local version is "
            f"{current_version}. If your changes still apply to its current state, resubmit "
            f"with version={current_version}; otherwise re-read it first."
        )
    # Not retryable: repeating the same call fails again, and a resubmission needs the
    # caller to confirm first that the changes still apply (see docs/errors-and-writes.md).
    return LibraryError(VERSION_CONFLICT, message, retryable=False, details=details)


def operation(*, write: bool = False):
    """Serialize one operation and always clear pyzotero's mutable request state."""

    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            zot = None
            with self._lock:
                try:
                    # Resolve the factory inside the guarded block so startup and connection
                    # failures receive the same contract as failures from an API call.
                    zot = self.zot
                    if write and not getattr(zot, "local_api_key", None):
                        raise LibraryError(
                            "UNAUTHORIZED",
                            "Local Zotero authorization is missing. Run zotero-mcp authorize first.",
                        )
                    return method(self, *args, **kwargs)
                except Exception as exc:
                    error = from_exception(
                        exc, write=write and zot is not None, operation=method.__name__
                    )
                    if error is exc:
                        raise
                    raise error from exc
                finally:
                    if zot is not None:
                        # Pyzotero clears these on success, but some failures leave them set.
                        zot.url_params = None
                        zot.tag_data = False
                        zot.links = None

        return wrapped

    return decorate


class Library:
    def __init__(self, factory: Callable[[], Zotero] = new_client):
        self._factory = factory
        self._zot: Zotero | None = None
        self._lock = threading.RLock()
        self._item_type_names: frozenset[str] | None = None

    @property
    def zot(self) -> Zotero:
        if self._zot is None:
            candidate = self._factory()
            if not candidate.local:
                candidate.client.close()
                raise LibraryError("UNSUPPORTED_VERSION", "Only the Zotero local API is supported.")
            self._zot = candidate
        return self._zot

    def close(self):
        with self._lock:
            if self._zot is not None:
                self._zot.client.close()
                self._zot = None

    def _read_version(self, kind: str, entity_key: str) -> int | None:
        """Current local version of an item or collection, or None when it cannot be read.

        Used after a write that already conflicted or succeeded, so a failed read must not
        change that outcome. Zotero's Last-Modified-Version response header is deliberately
        not used: it is the library version, which differs from the entity's own version
        when an update changed nothing.
        """
        try:
            record = (
                self.zot.collection(entity_key)
                if kind == "collection"
                else self.zot.item(entity_key)
            )
            version = record["version"]
        except Exception:
            return None
        return version if type(version) is int and version >= 0 else None

    def _write_failure(
        self, exc: Exception, kind: str, entity_key: str, operation_name: str
    ) -> LibraryError:
        """Map the 404/412 of an item or collection write to NOT_FOUND / VERSION_CONFLICT."""
        if isinstance(exc, ServerIDMismatchError):
            # A subclass of PreConditionFailedError, but not an item version conflict.
            return from_exception(exc, write=True, operation=operation_name)
        if isinstance(exc, PreConditionFailedError):
            return _version_conflict_error(
                kind, entity_key, operation_name, self._read_version(kind, entity_key)
            )
        return _not_found_error(kind, entity_key, operation_name)

    def _page(
        self,
        items: list,
        limit: int,
        offset: int,
        *,
        view: ReadView = "raw",
        consumed_count: int | None = None,
    ) -> ItemPage:
        request = getattr(self.zot, "request", None)
        return page(
            items,
            limit,
            offset,
            view=view,
            consumed_count=consumed_count,
            links=getattr(self.zot, "links", None),
            headers=getattr(request, "headers", None),
        )

    @operation()
    def search_items(
        self,
        query: Annotated[
            str,
            Field(
                description="Search text; omit only when supplying valid tags. A DOI "
                "(10.x/..., doi:..., https://doi.org/...) or ISBN-10/13 (hyphens optional) "
                "as the whole query returns only items whose DOI/ISBN equals it."
            ),
        ] = "",
        tags: Annotated[
            list[ReadTag] | None,
            Field(
                description="Tag filters combined with AND; each filter supports OR (a || b) and exclusion (-tag)."
            ),
        ] = None,
        item_type: Annotated[
            str | None,
            Field(
                description="Native item-type filter; -attachment excludes attachment hits. Empty or blank counts as not provided. Not needed for DOI/ISBN queries."
            ),
        ] = None,
        qmode: Annotated[
            Literal["titleCreatorYear", "everything"],
            Field(
                description="titleCreatorYear matches title, creators and year; everything also matches other fields, notes and attachment text. Ignored for DOI/ISBN queries."
            ),
        ] = "titleCreatorYear",
        limit: ReadLimit = 25,
        offset: ReadOffset = 0,
        tag_scope: Annotated[
            Literal["matched_item", "parent", "either"],
            Field(
                description="Apply tags to the native hit, its parent, or either level within the current native page. For DOI/ISBN queries tags filter the matching items themselves."
            ),
        ] = "matched_item",
        view: Annotated[
            ReadView,
            Field(
                description="Result projection: summary, full semantic metadata, or native Zotero raw records."
            ),
        ] = "summary",
    ) -> ItemPage:
        """Search requires a query or at least one valid tag.

        Return native metadata or attachment hits from Zotero's indexed text.
        ``qmode='everything'`` includes attachment hits by default: no item type
        filter is sent unless ``item_type`` is explicit. Metadata search keeps the
        historical ``-attachment`` default. ``matched_item`` applies tags to the
        native hit; ``parent`` applies them to its parent when it has one; ``either``
        accepts either level. Attachment hits are not deduplicated into parent items.
        Their summary includes ``parent_key`` and the parent's title, creators and year.
        Identifier lookup: when the whole query is one DOI (bare, ``doi:`` prefix,
        or a doi.org URL; case-insensitive) or one ISBN-10/13 (hyphens and spaces
        optional; the ISBN-10 and ISBN-13 forms of one book both match), only
        top-level items whose DOI field (or ``DOI:`` line in Extra) or ISBN field
        equals it exactly are returned. Papers merely citing the DOI, and child notes
        or attachments, are never returned. ``qmode`` and ``item_type`` are not
        needed. The result carries ``identifier_lookup`` (``found`` false with empty
        ``items`` means the library does not hold it). In this mode ``tags`` filter
        the matches, and ``offset``/``limit``/``next_offset``/``total`` page through
        the exact matches only.
        """
        self._check_item_type(item_type)
        valid_tags = bool(tags) and all(tag.strip() for tag in tags)
        if not query.strip() and not valid_tags:
            raise LibraryError(
                QUERY_OR_TAGS_REQUIRED,
                "Provide a query or at least one valid tag. To browse recent items, use zotero_get_recent.",
            )
        if tags is not None and any(not tag.strip() for tag in tags):
            raise ValueError("tags must be a list of non-empty strings.")
        # An empty or whitespace-only item_type means "no filter", like omitting it.
        item_type = item_type.strip() if item_type and item_type.strip() else None

        identifier = parse_identifier(query)
        if identifier is not None:
            return self._lookup_identifier(identifier, tags, item_type, limit, offset, view)

        params = pagination(limit, offset)
        params.update(q=query, qmode=qmode)
        if item_type is not None:
            params["itemType"] = item_type
        elif qmode != "everything":
            params["itemType"] = "-attachment"
        if tags and tag_scope == "matched_item":
            params["tag"] = tags
        native_items = self.zot.items(**params)
        search_links = dict(self.zot.links) if isinstance(self.zot.links, Mapping) else None
        search_request = getattr(self.zot, "request", None)
        search_headers = (
            dict(search_request.headers)
            if isinstance(getattr(search_request, "headers", None), Mapping)
            else None
        )
        parent_keys = {
            key(item["data"]["parentItem"])
            for item in native_items
            if item.get("data", {}).get("parentItem")
        }
        parents = (
            self._lookup_items(sorted(parent_keys))
            if parent_keys and (view == "summary" or (tags and tag_scope != "matched_item"))
            else {}
        )
        filtered_items = native_items
        if tags and tag_scope != "matched_item":
            filtered_items = []
            for item in native_items:
                data = item.get("data", {}) if isinstance(item, Mapping) else {}
                parent = data.get("parentItem") if isinstance(data, Mapping) else None
                parent_item = parents.get(key(parent)) if parent else None
                parent_item = parent_item or item
                item_match = _tags_match(item, tags)
                parent_match = _tags_match(parent_item, tags)
                if (tag_scope == "parent" and parent_match) or (
                    tag_scope == "either" and (item_match or parent_match)
                ):
                    filtered_items.append(item)
        result = page(
            filtered_items,
            limit,
            offset,
            view=view,
            consumed_count=len(native_items),
            links=search_links,
            headers=search_headers,
        )
        if view == "summary":
            for summary in result["items"]:
                parent = parents.get(summary.get("parent_key"))
                if parent is not None:
                    parent_summary = project_items([parent])[0]
                    summary["parent"] = {
                        field: parent_summary[field]
                        for field in ("title", "creator_summary", "year")
                        if parent_summary.get(field) is not None and parent_summary[field] != ""
                    }
        return result

    def _lookup_identifier(
        self,
        identifier: Identifier,
        tags: list[str] | None,
        item_type: str | None,
        limit: int,
        offset: int,
        view: ReadView,
    ) -> ItemPage:
        """Exact DOI/ISBN lookup.

        Zotero's own search is substring based and also hits papers that merely cite
        the DOI, so it only supplies candidates (top-level items, via /items/top, so
        child notes and attachments collapse into their parents). The identifier
        fields then decide what really matches.
        """
        candidates: dict[str, Any] = {}
        truncated = False
        for query in identifier.queries:
            for start in range(0, IDENTIFIER_CANDIDATE_CAP, 100):
                params: dict[str, Any] = {
                    "q": query,
                    "qmode": "everything",
                    "limit": 100,
                    "start": start,
                }
                if item_type is not None:
                    params["itemType"] = item_type
                batch = self.zot.top(**params)
                for item in batch:
                    if isinstance(item, Mapping) and isinstance(item.get("key"), str):
                        candidates.setdefault(item["key"], item)
                if len(batch) < 100:
                    break
            else:
                truncated = True

        matches: list[tuple[Mapping[str, Any], str, str]] = []
        for item in candidates.values():
            hit = matching_field(item, identifier)
            # Candidates are top-level, so the "parent" level of a hit is the hit.
            if hit and (not tags or _tags_match(item, tags)):
                matches.append((item, *hit))
        window = matches[offset : offset + limit]
        result = page(
            [item for item, _, _ in window],
            limit,
            offset,
            view=view,
            headers={"Total-Results": len(matches)},
        )
        lookup: dict[str, Any] = {
            "type": identifier.kind,
            "value": identifier.value,
            "match": "exact",
            "fields": identifier.fields,
            "found": bool(matches),
            "matches": [
                {"key": item["key"], "field": field, "value": value}
                for item, field, value in window
            ],
        }
        if truncated:
            lookup["candidates_truncated"] = True
        if not matches:
            lookup["message"] = (
                f"No item in the library has this {identifier.kind.upper()} in "
                f"{' or '.join(identifier.fields)}; items that only mention it were not returned."
            )
        result["identifier_lookup"] = lookup
        return result

    @operation()
    def get_item_metadata(
        self,
        item_keys: Annotated[
            ItemKeyList,
            Field(
                description="1–50 Zotero item keys, including child notes and attachments; results retain input order and duplicates."
            ),
        ],
        view: Annotated[
            ReadView,
            Field(
                description="Result projection: summary, full semantic metadata, or native Zotero raw records."
            ),
        ] = "metadata",
    ) -> ItemMetadataBatch:
        """Read 1–50 items by key; each result is ``{index, key, status, item | error}``.

        ``items`` follows the input order (duplicates included). A key Zotero does not
        return has status ``error`` with code NOT_FOUND. Child notes and attachments
        are readable. ``item`` is semantic metadata by default; use ``view='raw'``
        for Zotero's native object.
        """
        keys = [key(k) for k in item_keys]
        wanted = set(keys)
        # Not /items/top: child items must be readable. Zotero also returns the
        # children of each requested item here, so keep only the requested keys.
        # The limit is explicit (pyzotero defaults to 100); everything() follows
        # the remaining pages when children push results past one page.
        returned = self.zot.everything(self.zot.items(itemKey=",".join(sorted(wanted)), limit=100))
        by_key = {item["key"]: item for item in returned if item.get("key") in wanted}
        entries = []
        for index, item_key in enumerate(keys):
            entry: dict[str, Any] = {"index": index, "key": item_key}
            item = by_key.get(item_key)
            if item is None:
                entry.update(
                    status="error",
                    error={"code": NOT_FOUND, "message": "Item was not found in the library."},
                )
            else:
                entry.update(status="ok", item=project_items([item], view)[0])
            entries.append(entry)
        return {"items": entries}

    def _require_item(self, item_key: str) -> None:
        """Raise NOT_FOUND unless an item with this key exists.

        Zotero answers /items/{key}/children with 200 and [] for any key, so an empty
        list cannot tell "no children" from "no such item". The check runs after the
        listing, so the listing's pagination state (headers, links) is put back.
        """
        zot = self.zot
        request, links = getattr(zot, "request", None), zot.links
        try:
            zot.item(item_key)
        except ResourceNotFoundError as exc:
            raise LibraryError(
                NOT_FOUND,
                f"No item with key {item_key} exists in the library. Check the key "
                "(a collection key is not an item key; zotero_get_collections lists "
                "collection keys).",
            ) from exc
        finally:
            zot.request, zot.links = request, links

    @operation()
    def get_item_children(
        self,
        item_key: ReadItemKey,
        limit: ReadLimit = 25,
        offset: ReadOffset = 0,
        view: Annotated[
            ReadView,
            Field(
                description="Result projection: summary, full semantic metadata, or native Zotero raw records."
            ),
        ] = "summary",
    ) -> ItemPage:
        """List an item's child attachments, notes or annotations."""
        parent = key(item_key)
        children = self.zot.children(parent, **pagination(limit, offset))
        if not children:
            self._require_item(parent)
        return self._page(children, limit, offset, view=view)

    @operation()
    def get_collections(
        self,
        limit: ReadLimit = 25,
        offset: ReadOffset = 0,
        view: Annotated[
            Literal["summary", "raw"],
            Field(description="Compact collection summary, or native Zotero raw records."),
        ] = "summary",
    ) -> ItemPage:
        """List collections with keys, versions, names and item counts by default."""
        result = self._page(self.zot.collections(**pagination(limit, offset)), limit, offset)
        if view == "summary":
            summaries = []
            for collection in result["items"]:
                data = collection.get("data", {})
                summary = {
                    "key": collection["key"],
                    "version": collection["version"],
                    "name": data.get("name", ""),
                    "item_count": collection.get("meta", {}).get("numItems"),
                }
                if data.get("parentCollection"):
                    summary["parent_key"] = data["parentCollection"]
                summaries.append(summary)
            result["items"] = summaries
        return result

    @operation()
    def get_collection_items(
        self,
        collection_key: ReadItemKey,
        limit: ReadLimit = 25,
        offset: ReadOffset = 0,
        view: Annotated[
            ReadView,
            Field(
                description="Result projection: summary, full semantic metadata, or native Zotero raw records."
            ),
        ] = "summary",
    ) -> ItemPage:
        """List the top-level items directly in a collection, as summaries by default.

        Child attachments and notes are not listed (Zotero's non-top endpoint would
        add the children of every member, taking up pages and the total). Standalone
        attachments and notes are top-level and still appear. Use get_item_children
        for a paper's children; a summary's candidate_attachment_key names
        its best attachment. Request metadata or raw when needed.
        """
        collection = key(collection_key)
        # Zotero answers /collections/{unknown key}/items/top with 200 and the whole
        # library's top-level items, so the collection has to be checked first.
        try:
            self.zot.collection(collection)
        except ResourceNotFoundError as exc:
            raise LibraryError(
                NOT_FOUND,
                f"No collection with key {collection} exists. Use zotero_get_collections "
                "to look up collection keys (an item key is not a collection key).",
            ) from exc
        return self._page(
            self.zot.collection_items_top(collection, **pagination(limit, offset)),
            limit,
            offset,
            view=view,
        )

    @operation()
    def get_tags(self, limit: ReadLimit = 100, offset: ReadOffset = 0) -> TagPage:
        """List tag names in the configured local library.

        Each entry in ``items`` is ``{"tag": "<name>"}``; tag type and item counts
        are not returned.
        """
        names = self.zot.tags(**pagination(limit, offset))
        # pyzotero returns plain tag-name strings, not item-like dicts.
        entries = [{"tag": name} for name in names]
        return self._page(entries, limit, offset, view="raw")

    @operation()
    def get_recent(
        self,
        limit: ReadLimit = 25,
        offset: ReadOffset = 0,
        view: Annotated[
            ReadView,
            Field(
                description="Result projection: summary, full semantic metadata, or native Zotero raw records."
            ),
        ] = "summary",
    ) -> ItemPage:
        """List all top-level items, including independent attachments, as summaries."""
        return self._page(
            self.zot.top(sort="dateAdded", direction="desc", **pagination(limit, offset)),
            limit,
            offset,
            view=view,
        )

    @operation()
    def get_notes(
        self,
        parent_key: Annotated[
            ReadItemKey | None,
            Field(description="Parent item key to restrict notes; omit to list library notes."),
        ] = None,
        limit: ReadLimit = 25,
        offset: ReadOffset = 0,
        view: Annotated[
            ReadView,
            Field(
                description="Result projection: summary, full semantic metadata, or native Zotero raw records."
            ),
        ] = "metadata",
    ) -> ItemPage:
        """Read notes, optionally restricted to a parent item."""
        params = {**pagination(limit, offset), "itemType": "note"}
        if not parent_key:
            return self._page(self.zot.items(**params), limit, offset, view=view)
        parent = key(parent_key)
        items = self.zot.children(parent, **params)
        if not items:
            self._require_item(parent)
        return self._page(items, limit, offset, view=view)

    @operation()
    def get_annotations(
        self,
        attachment_key: ReadItemKey,
        limit: ReadLimit = 25,
        offset: ReadOffset = 0,
        view: Annotated[
            ReadView,
            Field(
                description="Result projection: summary, full semantic metadata, or native Zotero raw records."
            ),
        ] = "metadata",
    ) -> ItemPage:
        """Read Zotero reader annotations belonging to an attachment."""
        attachment = key(attachment_key)
        items = self.zot.children(attachment, itemType="annotation", **pagination(limit, offset))
        if not items:
            self._require_item(attachment)
        return self._page(items, limit, offset, view=view)

    @staticmethod
    def _safe_attachment_text_metadata(item: Mapping[str, Any]) -> tuple[str, str]:
        data = item.get("data")
        if not isinstance(data, Mapping) or data.get("itemType") != "attachment":
            raise LibraryError(
                INVALID_ARGUMENT,
                "The requested item is not a Zotero attachment.",
            )
        filename = data.get("filename")
        content_type = data.get("contentType")
        if not isinstance(filename, str) or not filename.strip():
            raise LibraryError(
                INVALID_ARGUMENT,
                "The requested attachment has no safe text filename.",
            )
        normalized_type = (
            content_type.lower().split(";", 1)[0].strip() if isinstance(content_type, str) else ""
        )
        suffix = Path(filename).suffix.lower()
        is_safe_type = normalized_type in SAFE_TEXT_CONTENT_TYPES
        is_legacy_markdown = (
            suffix in {".md", ".markdown"} and normalized_type in LEGACY_MARKDOWN_CONTENT_TYPES
        )
        if suffix not in SAFE_TEXT_SUFFIXES or not (is_safe_type or is_legacy_markdown):
            raise LibraryError(
                INVALID_ARGUMENT,
                "Only UTF-8 Markdown or plain-text attachments are readable.",
                details={
                    "filename": filename,
                    "content_type": normalized_type or None,
                },
            )
        return filename, normalized_type or "application/octet-stream"

    def _read_file_redirect(self, attachment_key: str) -> bytes:
        client = getattr(self.zot, "client", None)
        if client is None:
            raise LibraryError(
                CONNECTION_ERROR,
                "Zotero did not expose a configured HTTP client for the file redirect.",
            )
        url = (
            f"{str(self.zot.endpoint).rstrip('/')}/{self.zot.library_type}/"
            f"{self.zot.library_id}/items/{key(attachment_key)}/file"
        )
        try:
            response = client.get(
                url,
                headers=self.zot._local_headers(),
                follow_redirects=False,
                timeout=120,
            )
        except Exception as exc:
            error = from_exception(exc, operation="read_attachment_file_redirect")
            raise error from exc
        self.zot.request = response
        status = getattr(response, "status_code", 0)
        if 300 <= status < 400:
            location = response.headers.get("location")
            parsed = urlparse(location or "")
            if (
                parsed.scheme.lower() != "file"
                or parsed.netloc.lower() not in {"", "localhost"}
                or parsed.query
                or parsed.fragment
            ):
                raise LibraryError(
                    INVALID_ARGUMENT,
                    "Zotero returned an unsafe local file redirect.",
                )
            path_text = unquote(parsed.path)
            if os.name == "nt" and re.match(r"^/[A-Za-z]:", path_text):
                path_text = path_text[1:]
            path = Path(path_text)
            try:
                info = path.stat()
                if not path.is_file() or info.st_size > MAX_ATTACHMENT_TEXT_BYTES:
                    raise LibraryError(
                        INVALID_ARGUMENT,
                        "The redirected text attachment is missing, not a regular file, or exceeds 50 MiB.",
                    )
                return path.read_bytes()
            except LibraryError:
                raise
            except OSError as exc:
                error = from_exception(exc, operation="read_attachment_file_redirect")
                raise error from exc
        if 200 <= status < 300:
            content = response.content
            if isinstance(content, bytes):
                return content
        raise LibraryError(
            CONNECTION_ERROR,
            "Zotero did not return a readable attachment file.",
            details={"status_code": status},
        )

    def _read_attachment_bytes(self, attachment_key: str) -> bytes:
        try:
            content = self.zot.file(key(attachment_key))
        except (KeyError, NotImplementedError):
            content = self._read_file_redirect(attachment_key)
        except Exception as exc:
            error = from_exception(exc, operation="read_attachment_file")
            raise error from exc
        if not isinstance(content, bytes):
            raise LibraryError(
                INVALID_ARGUMENT,
                "Zotero returned a non-binary attachment response.",
            )
        if len(content) > MAX_ATTACHMENT_TEXT_BYTES:
            raise LibraryError(
                INVALID_ARGUMENT,
                "The text attachment exceeds the 50 MiB safety limit.",
                details={"byte_length": len(content)},
            )
        return content

    @staticmethod
    def _decode_attachment_text(content: bytes, attachment_key: str) -> str:
        if b"\x00" in content or any(
            content.startswith(signature) for signature in BINARY_SIGNATURES
        ):
            raise LibraryError(
                INVALID_ARGUMENT,
                "The attachment has a binary file signature and is not readable as text.",
                details={"attachment_key": attachment_key},
            )
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LibraryError(
                INVALID_ARGUMENT,
                "The text attachment is not valid UTF-8.",
                details={"attachment_key": attachment_key},
            ) from exc

    @operation()
    def read_attachment_text(
        self,
        attachment_key: FulltextAttachmentKey,
        offset: AttachmentTextOffset = 0,
        max_characters: AttachmentTextMaxCharacters = 50000,
    ) -> AttachmentTextChunk:
        """Read original UTF-8 bytes from one safe text attachment."""
        attachment_key = key(attachment_key)
        item = self.zot.item(attachment_key)
        filename, content_type = self._safe_attachment_text_metadata(item)
        raw = self._read_attachment_bytes(attachment_key)
        content = self._decode_attachment_text(raw, attachment_key)
        total_characters = len(content)
        if offset > total_characters:
            raise LibraryError(
                INVALID_ARGUMENT,
                "offset is beyond the attachment text; use an offset up to total_characters.",
                details={"offset": offset, "total_characters": total_characters},
            )
        end = min(offset + max_characters, total_characters)
        return {
            "source": "attachment-file",
            "encoding": "utf-8",
            "attachment_key": attachment_key,
            "filename": filename,
            "content_type": content_type,
            "content": content[offset:end],
            "offset": offset,
            "next_offset": end if end < total_characters else None,
            "total_characters": total_characters,
            "byte_length": len(raw),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
        }

    def _fetch_index(self, attachment_key: str) -> tuple[str, dict[str, Any]] | None:
        """Return indexed text, or None when Zotero has no usable index for this key."""
        try:
            response = self.zot.fulltext_item(attachment_key)
        except ResourceNotFoundError:
            return None
        content, response = parse_fulltext_response(response, attachment_key=attachment_key)
        return (content, response) if content else None

    def _fulltext_lookup(self, item_key: str) -> Mapping[str, Any]:
        """Fetch the item behind a fulltext key, naming the key's real kind on failure."""
        try:
            return self.zot.item(item_key)
        except ResourceNotFoundError:
            pass
        try:
            self.zot.collection(item_key)
        except ResourceNotFoundError:
            raise LibraryError(
                NOT_FOUND,
                "No Zotero item has this key.",
                details={"key": item_key},
            ) from None
        raise LibraryError(
            INVALID_ARGUMENT,
            "This key belongs to a collection, not an item. Fulltext tools need a "
            "paper key or an attachment key; list a collection with get_collection_items.",
            details={"key": item_key, "kind": "collection"},
        )

    @staticmethod
    def _reject_non_text_item(item_key: str, item: Mapping[str, Any]) -> None:
        """Notes and annotations never have indexed text; say where to read them."""
        data = item.get("data")
        item_type = data.get("itemType") if isinstance(data, Mapping) else None
        if item_type == "note":
            raise LibraryError(
                INVALID_ARGUMENT,
                "This key is a note, and notes have no indexed fulltext. Read its text "
                "with get_item_metadata or get_notes.",
                details={"key": item_key, "item_type": "note"},
            )
        if item_type == "annotation":
            raise LibraryError(
                INVALID_ARGUMENT,
                "This key is an annotation, and annotations have no indexed fulltext. "
                "Read annotations with get_annotations on their attachment.",
                details={"key": item_key, "item_type": "annotation"},
            )

    def _select_fulltext_attachment(
        self, item_key: str, item: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Pick the attachment of a parent item; shared by every fulltext tool."""
        attachments = self.zot.everything(self.zot.children(item_key, itemType="attachment"))
        return select_attachment(
            attachments,
            parent_key=item_key,
            preferred_attachment_key=preferred_attachment_key(item),
        )

    def _read_indexed_attachment(self, key_: str) -> tuple[str, str, dict[str, Any]]:
        """Read indexed text; return (attachment_key, content, index response).

        A parent item key is accepted and mapped to one of its
        attachments by the same rules as get_item_fulltext. The common case (a real
        attachment key) costs one request; the item is fetched only after a miss.
        """
        indexed = self._fetch_index(key_)
        if indexed is not None:
            return key_, *indexed
        item = self._fulltext_lookup(key_)
        data = item.get("data")
        if isinstance(data, Mapping) and data.get("itemType") == "attachment":
            raise unindexed_attachment_error(key_, item)
        self._reject_non_text_item(key_, item)
        selected = self._select_fulltext_attachment(key_, item)
        selected_key = key(selected["key"])
        indexed = self._fetch_index(selected_key)
        if indexed is None:
            raise unindexed_attachment_error(selected_key, selected)
        return selected_key, *indexed

    @staticmethod
    def _check_text_revision(content: str, expected: str | None, *, attachment_key: str) -> str:
        actual = text_revision(content)
        if expected is not None and expected != actual:
            raise FulltextError(
                "FULLTEXT_REVISION_MISMATCH",
                "The attachment text changed; restart reading from offset 0 with the new revision.",
                details={
                    "attachment_key": attachment_key,
                    "expected": expected,
                    "actual": actual,
                },
            )
        return actual

    @operation()
    def get_item_fulltext(
        self,
        item_key: FulltextItemKey,
        offset: FulltextOffset = 0,
        max_characters: FulltextMaxCharacters = 20000,
        text_revision: FulltextRevision = None,
    ) -> FulltextChunk:
        """Read Zotero-indexed text from one stable attachment.

        Pass a paper or attachment key as item_key. A paper uses Zotero's best
        attachment, then a sole attachment or sole PDF. If selection is ambiguous,
        pass one of the candidates' attachment_key as item_key. For later chunks,
        pass the returned attachment_key as item_key and reuse text_revision.
        Offsets are zero-based Unicode code points.
        """
        selected_key, content, response = self._read_indexed_attachment(key(item_key))
        self._check_text_revision(content, text_revision, attachment_key=selected_key)
        return make_chunk(
            attachment_key=selected_key,
            response=response,
            content=content,
            offset=offset,
            max_characters=max_characters,
        )

    @operation()
    def find_item_fulltext(
        self,
        item_key: FulltextItemKey,
        query: FulltextQuery,
        context_characters: FulltextContextCharacters = 120,
        max_matches: FulltextMaxMatches = 20,
        text_revision: FulltextRevision = None,
    ) -> FulltextFindResult:
        """Find literal, case-insensitive text in one attachment.

        Pass an attachment key, or a parent item key to select its attachment the same
        way get_item_fulltext does. Match and snippet positions are zero-based Unicode code points in the
        unmodified Zotero index text. This does not search parent items or the
        whole library, and does not use regular expressions.
        """
        item_key = key(item_key)
        if not query.strip():
            raise ValueError("query must contain non-whitespace text.")
        attachment_key, content, response = self._read_indexed_attachment(item_key)
        self._check_text_revision(content, text_revision, attachment_key=attachment_key)
        return make_find_result(
            attachment_key=attachment_key,
            response=response,
            content=content,
            query=query,
            context_characters=context_characters,
            max_matches=max_matches,
        )

    @operation()
    def get_item_schema(
        self,
        item_type: Annotated[
            str | None,
            Field(description="Zotero item type to inspect; omit to list available item types."),
        ] = None,
    ) -> dict[str, Any]:
        """List item types, or fields and creator types for one type, from local Zotero."""
        if item_type is None:
            return {"item_types": self.zot.item_types()}
        return {
            "fields": self.zot.item_type_fields(item_type),
            "creator_types": self.zot.item_creator_types(item_type),
        }

    def _check_item_type(self, item_type: str | None) -> None:
        """Reject an item_type filter naming no Zotero item type; Zotero would return 0 hits.

        Zotero's combined syntax is accepted: "a || b" and a leading "-" per alternative.
        The type list is fetched once per Library, not on every search.
        """
        if not isinstance(item_type, str) or not item_type.strip():
            return
        wanted = [part.strip().removeprefix("-").strip() for part in item_type.split("||")]
        wanted = [name for name in wanted if name]
        if not wanted:
            return
        if self._item_type_names is None:
            self._item_type_names = frozenset(
                entry["itemType"] for entry in self.zot.item_types() if "itemType" in entry
            )
        unknown = [name for name in wanted if name not in self._item_type_names]
        if not unknown:
            return
        lowered = {name.lower(): name for name in self._item_type_names}
        guesses = [
            lowered[match]
            for name in unknown
            for match in difflib.get_close_matches(name.lower(), lowered, n=3, cutoff=0.6)
        ]
        hint = f" Did you mean: {', '.join(dict.fromkeys(guesses))}?" if guesses else ""
        raise LibraryError(
            INVALID_ARGUMENT,
            f"item_type {', '.join(repr(name) for name in unknown)} is not a Zotero item type "
            f"(names are case-sensitive).{hint} Call zotero_get_item_schema without arguments "
            "to list valid types; combine them like 'book || journalArticle' or exclude with "
            "'-attachment'.",
        )

    def _lookup_items(self, item_keys: list[str]) -> dict[str, Any]:
        """Look up keys in one batch request to see which exist (children included).

        /items/top hides child notes, attachments and annotations; /items returns them,
        so a key that is absent here does not exist. Zotero also returns the children of
        every match, so only the requested keys are kept.
        """
        wanted = set(item_keys)
        found = self.zot.everything(self.zot.items(itemKey=",".join(sorted(wanted)), limit=100))
        return {item["key"]: item for item in found if item.get("key") in wanted}

    @staticmethod
    def _child_failure(item: Mapping[str, Any]) -> tuple[str, str, str]:
        """Return (code, parent_key, message) for a child item that cannot be used."""
        data = item.get("data", {})
        parent_key = data.get("parentItem") or ""
        item_type = data.get("itemType", "item")
        if item_type == "annotation":
            advice = (
                f"Annotation of attachment {parent_key}; the paper that owns the attachment "
                "is the item to cite."
            )
        else:
            advice = f"Child {item_type} of {parent_key}; use the parent item key {parent_key}."
        return NOT_CITABLE, parent_key, advice

    @operation()
    def get_citation_items(
        self,
        item_keys: Annotated[
            ItemKeyList,
            Field(description="1–50 Zotero item keys; results retain input order and duplicates."),
        ],
    ) -> CitationItemsResult:
        """Get per-key citation data for top-level bibliographic items.

        Use uri for Zotero Word/LibreOffice fields, select_link for note links, and
        citation_key plus csl for Pandoc/LaTeX. Never use links.alternate.href (a web
        page URL) as a citation URI. CSL is unchanged; its id is not a lookup key.
        """
        keys = [key(k) for k in item_keys]
        returned = self.zot.top(itemKey=",".join(keys), include="data,csljson", limit=len(keys))
        by_key = {item["key"]: item for item in returned}
        # /items/top never lists child items; one batch request tells them from keys
        # that do not exist. It is only made when some key is missing from the first.
        absent = list(dict.fromkeys(k for k in keys if k not in by_key))
        children = self._lookup_items(absent) if absent else {}
        entries = []
        for index, item_key in enumerate(keys):
            entry: dict[str, Any] = {"index": index, "key": item_key}
            try:
                item = by_key.get(item_key)
                if item is None:
                    child = children.get(item_key)
                    if child is None:
                        raise LibraryError(
                            NOT_FOUND,
                            "No item with this key exists in the library (a collection key "
                            "is not an item key).",
                        )
                    code, parent_key, message = self._child_failure(child)
                    entry["parent_key"] = parent_key
                    raise LibraryError(code, message)
                data = item.get("data", {})
                item_type = data.get("itemType", "")
                if item_type in {"note", "attachment", "annotation"}:
                    raise LibraryError(NOT_CITABLE, f"A standalone {item_type} cannot be cited.")
                csl = item.get("csljson")
                if isinstance(csl, str):
                    try:
                        csl = json.loads(csl)
                    except ValueError as exc:
                        raise LibraryError(
                            CITATION_DATA_INVALID, "Zotero returned invalid CSL JSON."
                        ) from exc
                if isinstance(csl, dict):
                    csl = [csl]
                if not isinstance(csl, list) or len(csl) != 1 or not isinstance(csl[0], dict):
                    raise LibraryError(
                        CITATION_DATA_INVALID, "Expected exactly one CSL object for this item."
                    )
                csl = csl[0]
                library = item.get("library", {})
                library_type = library.get("type")
                library_id = library.get("id")
                if library_type not in {"user", "group"} or library_id is None:
                    raise LibraryError(
                        CITATION_DATA_INVALID,
                        "Zotero returned missing or unsupported library data.",
                    )
                scope = "users" if library_type == "user" else "groups"
                select_scope = "library" if library_type == "user" else f"groups/{library_id}"
                citation_key = data.get("citationKey")
                if not isinstance(citation_key, str) or not citation_key.strip():
                    citation_key = csl.get("citation-key")
                if not isinstance(citation_key, str) or not citation_key.strip():
                    citation_key = None
                entry.update(
                    status="ok",
                    uri=f"http://zotero.org/{scope}/{library_id}/items/{item_key}",
                    select_link=f"zotero://select/{select_scope}/items/{item_key}",
                    citation_key=citation_key,
                    item_type=item_type,
                    title=data.get("title", ""),
                    csl=csl,
                )
            except LibraryError as exc:
                entry.update(status="error", error={"code": exc.code, "message": exc.message})
            entries.append(entry)
        return {"items": entries}

    @operation()
    def export_items(
        self,
        item_keys: Annotated[ItemKeyList, Field(description="1–50 Zotero item keys to export.")],
        format: Annotated[
            Literal["bibtex", "ris", "csljson"],
            Field(description="Zotero export format: BibTeX, RIS, or CSL JSON."),
        ] = "bibtex",
    ) -> dict[str, Any]:
        """Export up to 50 top-level items using Zotero's built-in local translators.

        Child notes and attachments are not exported: a request naming one fails as a
        whole with NOT_CITABLE (details.items gives each key's code and parent_key), and
        a key that does not exist fails with NOT_FOUND. BibTeX and RIS content is text;
        CSL JSON content is a parsed array of CSL objects. A CSL id is the item's
        citation key when available (otherwise an item URI), not a lookup key.
        For per-key citation data use zotero_get_citation_items.
        """
        keys = [key(k) for k in item_keys]
        result = self.zot.top(itemKey=",".join(keys), format=format, limit=len(item_keys))
        request = self.zot.request
        text = request.text
        # Zotero skips keys that /items/top does not list (children, unknown keys)
        # without any error; its Total-Results header counts the items it exported.
        distinct = list(dict.fromkeys(keys))
        exported = _total_results(getattr(request, "headers", None))
        if exported is not None and exported < len(distinct):
            self._reject_unexportable(distinct)
        if format != "csljson":
            return {"format": format, "content": text}
        # pyzotero returns the raw CSL JSON body for this format.
        if isinstance(result, (bytes, str)):
            try:
                result = json.loads(result)
            except ValueError as exc:
                raise LibraryError(
                    CITATION_DATA_INVALID, "Zotero returned invalid CSL JSON."
                ) from exc
        return {"format": format, "content": result}

    def _reject_unexportable(self, item_keys: list[str]) -> None:
        """Explain which keys /items/top skipped, then fail the whole export."""
        known = self._lookup_items(item_keys)
        failures = []
        for item_key in item_keys:
            item = known.get(item_key)
            if item is None:
                failures.append({"key": item_key, "code": NOT_FOUND})
            elif item.get("data", {}).get("parentItem"):
                code, parent_key, _ = self._child_failure(item)
                failures.append({"key": item_key, "code": code, "parent_key": parent_key})
        if any(f["code"] == NOT_CITABLE for f in failures):
            fixes = ", ".join(
                f"{f['key']} -> {f['parent_key']}" for f in failures if f["code"] == NOT_CITABLE
            )
            raise LibraryError(
                NOT_CITABLE,
                "Only top-level items can be exported; child notes, attachments and "
                f"annotations cannot. Use the parent item key instead ({fixes}). "
                "Nothing was exported.",
                details={"items": failures},
            )
        raise LibraryError(
            NOT_FOUND,
            "Some keys do not exist in the library; nothing was exported: "
            + ", ".join(f["key"] for f in failures)
            + ".",
            details={"items": failures},
        )

    @operation(write=True)
    def add_items(self, items: ItemDataList) -> WriteResponse:
        """Create 1 to 50 items, notes or annotations from Zotero item-data objects.

        Use zotero_get_item_schema to check fields. Child notes/annotations need
        parentItem. Unknown field names reject the whole batch (INVALID_ARGUMENT) before
        anything is written. Returns each successful and failed batch entry; a batch may
        partially succeed. Never resubmit successful entries.
        """
        for item in items:
            if not isinstance(item.get("itemType"), str) or not item["itemType"]:
                raise ValueError("Each item must have an itemType.")
            if {"key", "version", "data"} & item.keys():
                raise ValueError(
                    "Pass new item-data objects; use zotero_update_item to change existing items."
                )
        # Zotero silently drops unknown properties on create, so check the field names
        # first (as update_item does). This rejects the whole batch before any write.
        self.zot.check_items(items)
        return write_result(
            self.zot.create_items(copy.deepcopy(items)),
            input_count=len(items),
            input_data=items,
        )

    @operation(write=True)
    def update_item(
        self,
        item_key: ItemKey,
        version: Version,
        changes: Annotated[
            dict[str, Any],
            Field(
                description="Changed Zotero fields only; arrays replace whole arrays, so preserve entries you want to keep."
            ),
        ],
    ) -> WriteResponse:
        """Patch item metadata, a note or annotation using its last-read local version.

        Only supplied fields change. Arrays such as tags, creators and collections
        replace the existing array. A missing item is NOT_FOUND. A concurrent change
        is VERSION_CONFLICT with error.details.current_version; resubmit with that
        version only after confirming your changes still apply to the current item. On
        success, version (also in results[0].version) is the item's new local version, or
        omitted if it could not be read back; use it for the next update without re-reading.
        """
        if not changes or {"key", "version", "data", "library"} & changes.keys():
            raise ValueError("Provide changed fields only, without key/version/data/library.")
        payload = {
            **copy.deepcopy(changes),
            "key": key(item_key),
            "version": version,
        }
        # Check existence first: PATCH on an unknown key answers 400 (or, when the changes
        # contain itemType, creates a new item under that key) instead of 404.
        try:
            current = self.zot.item(payload["key"])
        except ResourceNotFoundError as exc:
            raise _not_found_error("item", payload["key"], "update_item") from exc
        if current["version"] != payload["version"]:
            raise _version_conflict_error("item", payload["key"], "update_item", current["version"])
        stored = self._update_item(payload)
        result = _single_write_result(payload["key"], _response_version(stored))
        if stored is not None:
            warnings = _stored_item_warnings(
                changes, stored["data"], current["data"].get("extra", "")
            )
            if warnings:
                result["results"][0]["warnings"] = warnings
        return result

    def _update_item(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Write already-read item data once and read back its actual local version."""
        try:
            confirmed = self.zot.update_item(payload)
        except (PreConditionFailedError, ResourceNotFoundError) as exc:
            raise self._write_failure(exc, "item", payload["key"], "update_item") from exc
        if confirmed is not True:
            raise LibraryError(
                UNKNOWN_WRITE_RESULT,
                "Zotero did not confirm the update; inspect the item before retrying.",
            )
        try:
            return self.zot.item(payload["key"])
        except Exception:
            # The write succeeded; unavailable read-back data must not change that outcome.
            return None

    @operation(write=True)
    def delete_item(self, item_key: ItemKey, version: Version) -> WriteResponse:
        """Permanently delete an item (including child items), note or annotation.

        This does not move it to Trash. Supply its last-read local version. A missing
        item is NOT_FOUND; a concurrent change is VERSION_CONFLICT with
        error.details.current_version.
        """
        payload = {"key": key(item_key), "version": version}
        try:
            confirmed = self.zot.delete_item(payload)
        except (PreConditionFailedError, ResourceNotFoundError) as exc:
            raise self._write_failure(exc, "item", payload["key"], "delete_item") from exc
        if confirmed is not True:
            raise RuntimeError(
                "Zotero did not confirm deletion; check the library before retrying."
            )
        return _single_write_result(payload["key"])

    @operation(write=True)
    def create_note(
        self,
        parent_key: ItemKey,
        text: Annotated[
            str, Field(description="Note body as plain text, or Zotero HTML when as_html is true.")
        ],
        title: Annotated[
            str, Field(description="Optional note heading; escaped and prepended to the body.")
        ] = "",
        as_html: Annotated[
            bool,
            Field(description="Preserve the body as Zotero HTML instead of escaping plain text."),
        ] = False,
        tags: Annotated[
            list[str] | None, Field(description="Tag names to assign to the new note.")
        ] = None,
    ) -> WriteResponse:
        """Create a child note. Escape plain text by default; as_html preserves Zotero HTML."""
        content = text if as_html else "<p>" + html.escape(text).replace("\n", "<br/>") + "</p>"
        if title:
            content = f"<h1>{html.escape(title)}</h1>{content}"
        result = self.add_items(
            [
                {
                    "itemType": "note",
                    "parentItem": key(parent_key),
                    "note": content,
                    "tags": [{"tag": tag} for tag in (tags or [])],
                }
            ]
        )
        result.update(
            {k: result["results"][0][k] for k in ("key", "version") if k in result["results"][0]}
        )
        return result

    @operation(write=True)
    def create_collection(
        self,
        name: Annotated[str, Field(description="Collection name.")],
        parent_collection: Annotated[
            str | None,
            Field(
                description="Parent collection key; omit or use an empty string to create at the root."
            ),
        ] = None,
    ) -> WriteResponse:
        """Create a collection, optionally under a parent collection."""
        if not name.strip():
            raise ValueError("A collection name cannot be empty.")
        payload = {
            "name": name,
            "parentCollection": key(parent_collection) if parent_collection else False,
        }
        return write_result(self.zot.create_collections([payload]), input_count=1, single=True)

    @operation(write=True)
    def update_collection(
        self,
        collection_key: ItemKey,
        version: Version,
        name: Annotated[str | None, Field(description="Collection name.")] = None,
        parent_collection: Annotated[
            str | None,
            Field(
                description="New parent collection key; empty string moves to root, null leaves the parent unchanged."
            ),
        ] = None,
    ) -> WriteResponse:
        """Rename/move a collection. Use parent_collection='' to move it to the root.

        A missing collection is NOT_FOUND. A concurrent change is VERSION_CONFLICT with
        error.details.current_version. On success, version (also in results[0].version)
        is the collection's new local version, or omitted if it could not be read back.
        """
        if name is None and parent_collection is None:
            raise ValueError("Provide a name or parent_collection to change.")
        if name is not None and not name.strip():
            raise ValueError("A collection name cannot be empty.")
        collection_key = key(collection_key)
        # Check existence first: PUT on an unknown key creates a new collection under it.
        try:
            current = self.zot.collection(collection_key)
        except ResourceNotFoundError as exc:
            raise _not_found_error("collection", collection_key, "update_collection") from exc
        if current["version"] != version:
            raise _version_conflict_error(
                "collection", collection_key, "update_collection", current["version"]
            )
        payload = {
            "key": collection_key,
            "version": version,
            "name": current["data"]["name"],
            "parentCollection": current["data"].get("parentCollection", False),
        }
        if name is not None:
            payload["name"] = name
        if parent_collection is not None:
            payload["parentCollection"] = key(parent_collection) if parent_collection else False
        try:
            confirmed = self.zot.update_collection(payload)
        except (PreConditionFailedError, ResourceNotFoundError) as exc:
            raise self._write_failure(
                exc, "collection", collection_key, "update_collection"
            ) from exc
        if confirmed is not True:
            raise RuntimeError("Zotero did not confirm the collection update.")
        return _single_write_result(
            collection_key, self._read_version("collection", collection_key)
        )

    @operation(write=True)
    def delete_collection(self, collection_key: ItemKey, version: Version) -> WriteResponse:
        """Permanently delete a collection. Library items remain; child collections are also removed.

        A missing collection is NOT_FOUND; a concurrent change is VERSION_CONFLICT with
        error.details.current_version.
        """
        payload = {"key": key(collection_key), "version": version}
        try:
            confirmed = self.zot.delete_collection(payload)
        except (PreConditionFailedError, ResourceNotFoundError) as exc:
            raise self._write_failure(
                exc, "collection", payload["key"], "delete_collection"
            ) from exc
        if confirmed is not True:
            raise RuntimeError("Zotero did not confirm collection deletion.")
        return _single_write_result(payload["key"])

    def _edit_lists(
        self,
        item_keys: list[str],
        field: str,
        change: Callable[[list], list],
        *,
        current_items: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        indexed_keys: list[tuple[int, str]] = []
        seen: set[str] = set()
        for input_index, raw_key in enumerate(item_keys):
            normalized = key(raw_key)
            if normalized not in seen:
                indexed_keys.append((input_index, normalized))
                seen.add(normalized)
        result = {
            "ok": True,
            "status": "success",
            "results": [],
        }
        for position, (input_index, item_key) in enumerate(indexed_keys):
            write_started = False
            try:
                item = (
                    current_items[item_key]
                    if current_items is not None
                    else self.zot.item(item_key)
                )
                original = item["data"].get(field, [])
                updated = change(copy.deepcopy(original))
                if updated == original:
                    result["results"].append(
                        {
                            "input_index": input_index,
                            "key": item_key,
                            "status": "unchanged",
                            "version": item["version"],
                        }
                    )
                    continue
                write_started = True
                stored = self._update_item(
                    {"key": item_key, "version": item["version"], field: updated}
                )
                version = _response_version(stored)
                entry = {"input_index": input_index, "key": item_key, "status": "success"}
                if version is not None:
                    entry["version"] = version
                result["results"].append(entry)
            except Exception as exc:
                # Keep completed work visible; never silently replay a partial batch.
                error = from_exception(
                    exc,
                    write=write_started,
                    operation=f"batch_{field}",
                )
                payload = error.as_dict()["error"]
                result["ok"] = False
                status = "unknown" if error.code == UNKNOWN_WRITE_RESULT else "failed"
                if status == "unknown":
                    result["status"] = "unknown"
                elif any(r["status"] in {"success", "unchanged"} for r in result["results"]):
                    result["status"] = "partial"
                else:
                    # Nothing was written or confirmed; later keys stay pending.
                    result["status"] = "failed"
                result["results"].append(
                    {
                        "input_index": input_index,
                        "key": item_key,
                        "status": status,
                        "error": payload,
                    }
                )
                for pending_index, pending_key in indexed_keys[position + 1 :]:
                    result["results"].append(
                        {
                            "input_index": pending_index,
                            "key": pending_key,
                            "status": "pending",
                        }
                    )
                break
        return result

    @operation(write=True)
    def batch_update_tags(
        self,
        item_keys: ItemKeyList,
        add: Annotated[
            list[str] | None,
            Field(description="Tag names to add, preserving existing tags and tag types."),
        ] = None,
        remove: Annotated[
            list[str] | None, Field(description="Tag names to remove, preserving all other tags.")
        ] = None,
    ) -> WriteResponse:
        """Add/remove tags while preserving other tags and tag types.

        Read each item's current local version before writing. On the first error,
        stop and report per-entry statuses and known local versions. At most 50 items.
        """
        add, remove = add or [], remove or []
        if not add and not remove:
            raise ValueError("Provide tags to add or remove.")
        if any(not tag.strip() for tag in add + remove):
            raise ValueError("Tags cannot be empty.")
        if set(add) & set(remove):
            raise ValueError("A tag cannot be added and removed in the same call.")

        def change(tags):
            tags = [t for t in tags if t["tag"] not in remove]
            existing = {t["tag"] for t in tags}
            for tag in dict.fromkeys(add):
                if tag not in existing:
                    tags.append({"tag": tag})
            return tags

        return self._edit_lists(item_keys, "tags", change)

    def _collection_items_for_write(self, item_keys: list[str]) -> dict[str, Any]:
        """Read items once and reject child keys before any collection membership write."""
        children = []
        items = {}
        for item_key in dict.fromkeys(key(k) for k in item_keys):
            items[item_key] = self.zot.item(item_key)
            parent_key = items[item_key].get("data", {}).get("parentItem")
            if parent_key:
                children.append({"key": item_key, "parent_key": parent_key})
        if children:
            listed = ", ".join(f"{c['key']} (parent {c['parent_key']})" for c in children)
            raise LibraryError(
                CHILD_ITEM_NOT_ALLOWED,
                "Child notes and attachments cannot be added to a collection; add their "
                f"parent items instead. No items were changed. Child items: {listed}.",
                details={"children": children},
            )
        return items

    @operation(write=True)
    def manage_collection_items(
        self,
        collection_key: ItemKey,
        item_keys: ItemKeyList,
        action: Annotated[
            Literal["add", "remove"],
            Field(
                description="Add or remove collection membership; removing does not delete items."
            ),
        ],
    ) -> WriteResponse:
        """Add/remove up to 50 items from a collection, preserving other memberships.

        Stop at the first failure and report completed and pending keys.
        Removing membership does not delete an item. Zotero keeps child notes and
        attachments out of collections: when adding, the whole batch is rejected with
        CHILD_ITEM_NOT_ALLOWED before any write if any key is a child item. The error
        details list each child's key and parent_key; add the parent item instead.
        """
        collection_key = key(collection_key)
        current_items = None
        if action == "add":
            self.zot.collection(collection_key)
            current_items = self._collection_items_for_write(item_keys)

        def change(collections):
            if action == "add" and collection_key not in collections:
                return [*collections, collection_key]
            if action == "remove":
                return [c for c in collections if c != collection_key]
            return collections

        return self._edit_lists(item_keys, "collections", change, current_items=current_items)

    def _attach_file(
        self,
        parent_key: ItemKey,
        file_path: str,
        title: str | None = None,
        *,
        content_type_override: str | None = None,
    ) -> WriteResponse:
        """Copy an existing absolute-path file into Zotero's local attachment storage.

        No URL download. If upload fails after creation, returns the attachment key
        so you can inspect it; do not resubmit blindly and create a duplicate.
        """
        parent_key = key(parent_key)
        path = Path(file_path)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("file_path must be an absolute path to an existing file.")
        metadata = {
            "itemType": "attachment",
            "parentItem": parent_key,
            "linkMode": "imported_file",
            "title": title or path.name,
            "filename": path.name,
            "contentType": content_type_override
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream",
        }
        created = write_result(self.zot.create_items([metadata]), input_count=1, single=True)
        if not created["ok"]:
            return created
        attachment_key = next(
            (
                entry.get("key")
                for entry in created["results"]
                if entry["status"] == "success" and entry.get("key")
            ),
            None,
        )
        if not attachment_key:
            raise LibraryError(
                UNKNOWN_WRITE_RESULT,
                "Zotero created an attachment without returning its key; inspect the library.",
            )
        try:
            upload = self.zot.upload_attachments(
                [{**metadata, "key": attachment_key}], basedir=str(path.parent)
            )
        except Exception as exc:
            cause = from_exception(exc, write=True, operation="attach_file.upload")
            error = LibraryError(
                UNKNOWN_WRITE_RESULT,
                "Attachment was created but upload was not confirmed; inspect it before retrying.",
                details={
                    "attachment_key": attachment_key,
                    "stage": "upload",
                    "cause_code": cause.code,
                    "cause_details": cause.details,
                },
            )
            payload = error.as_dict()["error"]
            return _single_write_result(
                attachment_key, status="unknown", stage="upload", error=payload
            )

        # Pyzotero returns three lists here, distinct from create_items' indexed maps.
        if not isinstance(upload, dict) or not all(
            isinstance(upload.get(field), list) for field in ("success", "failure", "unchanged")
        ):
            failure = LibraryError(
                UNKNOWN_WRITE_RESULT,
                "Attachment was created but upload returned no usable result; inspect it before retrying.",
                details={"attachment_key": attachment_key, "stage": "upload"},
            )
            payload = failure.as_dict()["error"]
            return _single_write_result(
                attachment_key, status="unknown", stage="upload", error=payload
            )

        success, failures, unchanged = (
            upload["success"],
            upload["failure"],
            upload["unchanged"],
        )
        if failures or (success and unchanged) or not (success or unchanged):
            failure = LibraryError(
                UNKNOWN_WRITE_RESULT,
                "Attachment was created but upload was not confirmed; inspect it before retrying.",
                details={
                    "attachment_key": attachment_key,
                    "stage": "upload",
                    "upload_failure": failures,
                },
            )
            payload = failure.as_dict()["error"]
            return _single_write_result(
                attachment_key, status="unknown", stage="upload", error=payload
            )

        status = "success" if success else "unchanged"
        return _single_write_result(attachment_key, status=status, stage="upload")

    @operation(write=True)
    def attach_file(
        self,
        parent_key: ItemKey,
        file_path: Annotated[
            str,
            Field(
                description="Absolute path to an existing local file to copy into Zotero attachment storage."
            ),
        ],
        title: Annotated[
            str | None,
            Field(description="Attachment title; omit or use an empty string to use the filename."),
        ] = None,
    ) -> WriteResponse:
        """Copy a local file into Zotero attachment storage.

        No version is returned after upload; read the attachment before editing it.
        """
        return self._attach_file(parent_key, file_path, title)

    @operation(write=True)
    def attach_markdown(self, parent_key: ItemKey, content: AttachmentTextContent) -> WriteResponse:
        """Create or reuse the fixed Markdown attachment for one paper.

        Uploads return no version; read the attachment before editing it. Reused
        attachments may include their last-read version.
        """
        parent_key = key(parent_key)
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("content must be valid UTF-8 text.") from exc
        if len(raw) > MAX_ATTACHMENT_TEXT_BYTES:
            raise ValueError("content must not exceed 50 MiB when encoded as UTF-8.")
        digest = hashlib.sha256(raw).hexdigest()

        try:
            children = self.zot.everything(self.zot.children(parent_key, itemType="attachment"))
        except Exception as exc:
            error = from_exception(exc, operation="attach_markdown.inspect")
            payload = error.as_dict()["error"]
            return _single_write_result(status="unknown", error=payload)

        matches = []
        for item in children:
            if not isinstance(item, Mapping):
                continue
            data = item.get("data")
            if isinstance(data, Mapping) and data.get("filename") == PAPER_CHEW_MARKDOWN_FILENAME:
                matches.append(item)

        if len(matches) > 1:
            error = {
                "code": ATTACHMENT_NAME_AMBIGUOUS,
                "message": "Multiple fixed Markdown attachments already exist; no duplicate was created.",
                "retryable": False,
                "details": {
                    "parent_key": parent_key,
                    "filename": PAPER_CHEW_MARKDOWN_FILENAME,
                    "attachment_keys": [item.get("key") for item in matches],
                },
            }
            return _single_write_result(status="failed", error=error)

        if matches:
            existing = matches[0]
            existing_key = existing.get("key")
            if not isinstance(existing_key, str):
                error = {
                    "code": ATTACHMENT_CONTENT_UNAVAILABLE,
                    "message": "The existing fixed Markdown attachment has no key; no duplicate was created.",
                    "retryable": False,
                }
                return _single_write_result(status="unknown", error=error)
            try:
                self._safe_attachment_text_metadata(existing)
                existing_raw = self._read_attachment_bytes(existing_key)
                self._decode_attachment_text(existing_raw, existing_key)
            except LibraryError as exc:
                error = {
                    "code": ATTACHMENT_CONTENT_UNAVAILABLE,
                    "message": "The existing fixed Markdown attachment could not be read; no duplicate was created.",
                    "retryable": False,
                    "details": {
                        "attachment_key": existing_key,
                        "cause": exc.as_dict()["error"],
                    },
                }
                return _single_write_result(existing_key, status="unknown", error=error)
            existing_digest = hashlib.sha256(existing_raw).hexdigest()
            if existing_digest == digest:
                return _single_write_result(
                    existing_key, _response_version(existing), status="unchanged"
                )
            error = {
                "code": ATTACHMENT_CONTENT_CONFLICT,
                "message": "A different fixed Markdown attachment already exists; no duplicate was created.",
                "retryable": False,
                "details": {
                    "attachment_key": existing_key,
                    "existing_sha256": existing_digest,
                    "requested_sha256": digest,
                },
            }
            return _single_write_result(existing_key, status="failed", error=error)

        with tempfile.TemporaryDirectory(prefix="zotero-mcp-markdown-") as directory:
            path = Path(directory) / PAPER_CHEW_MARKDOWN_FILENAME
            path.write_bytes(raw)
            result = self._attach_file(
                parent_key,
                str(path),
                title=PAPER_CHEW_MARKDOWN_FILENAME,
                content_type_override="text/markdown",
            )
        return result
