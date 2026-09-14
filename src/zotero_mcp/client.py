"""Local API operations. Pyzotero owns HTTP, authorization headers and versions."""

import copy
import html
import mimetypes
import re
import threading
from collections.abc import Callable, Mapping
from functools import wraps
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from pyzotero import ResourceNotFoundError, Zotero

from zotero_mcp.config import Settings, load_auth
from zotero_mcp.errors import LibraryError, from_exception
from zotero_mcp.fulltext import (
    FulltextError,
    make_chunk,
    make_find_result,
    parse_fulltext_response,
    select_attachment,
    text_revision,
)
from zotero_mcp.fulltext_models import FulltextChunk, FulltextFindResult
from zotero_mcp.models import ItemDataList, ItemKey, ItemKeyList, Version, WriteResponse
from zotero_mcp.presentation import ReadView, normalize_view, project_items


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
    if not re.fullmatch(r"[A-Z0-9]{8}", value):
        raise ValueError("A Zotero item or collection key must contain 8 letters/digits.")
    return value


def version_number(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("version must be a nonnegative local API version.")
    return value


def pagination(limit: int, offset: int) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer.")
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
        if entry.lstrip().startswith("<") and ">" in entry:
            return entry.lstrip()[1 : entry.index(">")]
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
) -> dict[str, Any]:
    """Build a projected page using native response pagination when available."""
    normalize_view(view)
    projected = project_items(items, view)
    upstream_count = len(items) if consumed_count is None else consumed_count
    if type(upstream_count) is not int or upstream_count < 0:
        raise ValueError("consumed_count must be a nonnegative integer.")

    total = _total_results(headers)
    next_link = _next_link(links, headers)
    next_offset = _next_offset_from_link(next_link)
    if next_offset is None and total is not None:
        next_offset = offset + upstream_count if offset + upstream_count < total else None
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
        tag["tag"]
        for tag in tags
        if isinstance(tag, Mapping) and isinstance(tag.get("tag"), str)
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


def _result_status(entries: list[dict[str, Any]]) -> str:
    statuses = {entry["status"] for entry in entries}
    if not statuses or statuses == {"success"} or statuses == {"unchanged"}:
        return "success"
    if statuses <= {"failed"}:
        return "failed"
    return "partial"


def write_result(
    result: dict,
    *,
    input_keys: list[str | None] | None = None,
    input_count: int | None = None,
) -> dict[str, Any]:
    """Normalize pyzotero's indexed response while preserving legacy fields."""
    if not isinstance(result, dict):
        raise LibraryError(
            "UNKNOWN_WRITE_RESULT",
            "Zotero returned an unexpected write response; inspect the library before retrying.",
        )
    fields = ("successful", "success", "unchanged", "failed", "pending")
    if not any(field in result for field in fields):
        raise LibraryError(
            "UNKNOWN_WRITE_RESULT",
            "Zotero returned an unexpected write response; inspect the library before retrying.",
        )

    successful = result.get("successful") or {}
    success = result.get("success") or {}
    unchanged = result.get("unchanged") or {}
    failed = result.get("failed") or {}
    pending = result.get("pending") or []
    if (
        not isinstance(successful, dict)
        or not isinstance(success, dict)
        or not isinstance(failed, dict)
    ):
        raise LibraryError(
            "UNKNOWN_WRITE_RESULT",
            "Zotero returned malformed indexed write results; inspect the library before retrying.",
        )
    if not isinstance(unchanged, (dict, list)) or not isinstance(pending, (dict, list)):
        raise LibraryError(
            "UNKNOWN_WRITE_RESULT",
            "Zotero returned malformed write results; inspect the library before retrying.",
        )
    if not any((successful, success, unchanged, failed, pending)):
        raise LibraryError(
            "UNKNOWN_WRITE_RESULT",
            "Zotero returned no item results; inspect the library before retrying.",
        )

    if input_keys is not None:
        input_count = len(input_keys)
    indexes: set[int] = set()
    for values in (successful, success, failed):
        for raw_index in values:
            if not str(raw_index).isdigit():
                raise LibraryError(
                    "UNKNOWN_WRITE_RESULT",
                    "Zotero returned a non-indexed write result; inspect the library before retrying.",
                )
            indexes.add(int(raw_index))
    if isinstance(unchanged, dict):
        for raw_index in unchanged:
            if not str(raw_index).isdigit():
                raise LibraryError(
                    "UNKNOWN_WRITE_RESULT",
                    "Zotero returned a non-indexed write result; inspect the library before retrying.",
                )
            indexes.add(int(raw_index))
    elif isinstance(unchanged, list):
        indexes.update(range(len(unchanged)))
    if isinstance(pending, dict):
        for raw_index in pending:
            if not str(raw_index).isdigit():
                raise LibraryError(
                    "UNKNOWN_WRITE_RESULT",
                    "Zotero returned a non-indexed pending result; inspect the library before retrying.",
                )
            indexes.add(int(raw_index))
    elif isinstance(pending, list):
        for raw_index in pending:
            if isinstance(raw_index, bool) or not str(raw_index).isdigit():
                raise LibraryError(
                    "UNKNOWN_WRITE_RESULT",
                    "Zotero returned a non-indexed pending result; inspect the library before retrying.",
                )
            indexes.add(int(raw_index))
    if input_count is not None:
        indexes.update(range(input_count))

    entries: list[dict[str, Any]] = []
    for index in sorted(indexes):
        source_key = (
            input_keys[index] if input_keys is not None and index < len(input_keys) else None
        )
        successful_value = (
            successful.get(str(index), successful.get(index))
            if isinstance(successful, dict)
            else None
        )
        success_value = (
            success.get(str(index), success.get(index)) if isinstance(success, dict) else None
        )
        failed_value = (
            failed.get(str(index), failed.get(index)) if isinstance(failed, dict) else None
        )
        unchanged_value = (
            unchanged.get(str(index), unchanged.get(index))
            if isinstance(unchanged, dict)
            else (
                unchanged[index] if isinstance(unchanged, list) and index < len(unchanged) else None
            )
        )
        pending_value = (
            pending.get(str(index), pending.get(index))
            if isinstance(pending, dict)
            else (index in pending if isinstance(pending, list) else False)
        )
        if failed_value is not None:
            status = "failed"
            value = failed_value
        elif pending_value is not False and pending_value is not None:
            status = "pending"
            value = pending_value
        elif successful_value is not None:
            status = "success"
            value = successful_value
        elif success_value is not None:
            status = "success"
            value = success_value
        elif unchanged_value is not None:
            status = "unchanged"
            value = unchanged_value
        else:
            # A response that omits an input is an unexecuted input, not success.
            status = "pending"
            value = None
        entries.append(
            {
                "input_index": index,
                "status": status,
                "key": source_key or _response_key(value),
                **({"error": value} if status == "failed" else {}),
            }
        )

    pending_keys = [
        entry["key"]
        for entry in entries
        if entry["status"] == "pending" and entry.get("key") is not None
    ]
    normalized = {
        "ok": not any(entry["status"] in {"failed", "pending"} for entry in entries),
        "status": _result_status(entries),
        "results": entries,
        "successful": successful,
        "success": success,
        "unchanged": unchanged,
        "failed": failed,
        "pending": pending_keys,
    }
    return normalized


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

    def _page(
        self,
        items: list,
        limit: int,
        offset: int,
        *,
        view: ReadView = "raw",
        consumed_count: int | None = None,
    ) -> dict[str, Any]:
        normalize_view(view)
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
        query: str = "",
        tags: list[str] | None = None,
        item_type: str | None = None,
        qmode: Literal["titleCreatorYear", "everything"] = "titleCreatorYear",
        limit: int = 25,
        offset: int = 0,
        tag_scope: Literal["matched_item", "parent", "either"] = "matched_item",
        view: ReadView = "summary",
    ) -> dict[str, Any]:
        """Search metadata, tags or Zotero-indexed text and return native hits.

        ``qmode='everything'`` includes attachment hits by default: no item type
        filter is sent unless ``item_type`` is explicit. Metadata search keeps the
        historical ``-attachment`` default. ``matched_item`` applies tags to the
        native hit; ``parent`` applies them to its parent when it has one; ``either``
        accepts either level. Attachment hits are not deduplicated into parent items.
        Their summary includes ``matched_attachment_key`` and ``parent_key``.
        """
        normalize_view(view)
        if not isinstance(qmode, str) or qmode not in {"titleCreatorYear", "everything"}:
            raise ValueError("qmode must be titleCreatorYear or everything.")
        if not isinstance(tag_scope, str) or tag_scope not in {"matched_item", "parent", "either"}:
            raise ValueError("tag_scope must be matched_item, parent, or either.")
        if not isinstance(query, str):
            raise ValueError("query must be a string.")
        if tags is not None and (
            not isinstance(tags, list)
            or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
        ):
            raise ValueError("tags must be a list of non-empty strings.")
        if not query.strip() and not tags:
            raise ValueError("Provide a query or tags; use zotero_get_recent to browse.")
        if item_type is not None and (not isinstance(item_type, str) or not item_type.strip()):
            raise ValueError("item_type must be a non-empty string when provided.")

        params = pagination(limit, offset)
        params.update(q=query, qmode=qmode)
        if item_type is not None:
            params["itemType"] = item_type
        elif qmode != "everything":
            params["itemType"] = "-attachment"
        if tags and tag_scope == "matched_item":
            params["tag"] = tags
        native_items = self.zot.items(**params)
        filtered_items = native_items
        if tags and tag_scope != "matched_item":
            filtered_items = []
            for item in native_items:
                data = item.get("data", {}) if isinstance(item, Mapping) else {}
                parent_item = None
                parent = data.get("parentItem") if isinstance(data, Mapping) else None
                if isinstance(parent, str) and parent:
                    try:
                        parent_item = self.zot.item(key(parent))
                    except ResourceNotFoundError:
                        parent_item = None
                parent_item = parent_item or item
                item_match = _tags_match(item, tags)
                parent_match = _tags_match(parent_item, tags)
                if (tag_scope == "parent" and parent_match) or (
                    tag_scope == "either" and (item_match or parent_match)
                ):
                    filtered_items.append(item)
        return self._page(
            filtered_items,
            limit,
            offset,
            view=view,
            consumed_count=len(native_items),
        )

    @operation()
    def get_item_metadata(
        self, item_key: str, view: ReadView = "metadata"
    ) -> dict[str, Any]:
        """Read semantic metadata by default; use ``view='raw'`` for Zotero's object."""
        item = self.zot.item(key(item_key))
        return project_items([item], view)[0]

    @operation()
    def get_item_children(
        self, item_key: str, limit: int = 25, offset: int = 0, view: ReadView = "summary"
    ) -> dict[str, Any]:
        """List an item's child attachments, notes or annotations."""
        return self._page(
            self.zot.children(key(item_key), **pagination(limit, offset)),
            limit,
            offset,
            view=view,
        )

    @operation()
    def get_collections(self, limit: int = 25, offset: int = 0) -> dict[str, Any]:
        """List collections with keys, parentCollection and local versions."""
        return self._page(self.zot.collections(**pagination(limit, offset)), limit, offset)

    @operation()
    def get_collection_items(
        self,
        collection_key: str,
        limit: int = 25,
        offset: int = 0,
        view: ReadView = "summary",
    ) -> dict[str, Any]:
        """List collection items as summaries; request metadata or raw when needed."""
        return self._page(
            self.zot.collection_items(key(collection_key), **pagination(limit, offset)),
            limit,
            offset,
            view=view,
        )

    @operation()
    def get_tags(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List tags in the configured local library."""
        return self._page(self.zot.tags(**pagination(limit, offset)), limit, offset)

    @operation()
    def get_recent(
        self, limit: int = 25, offset: int = 0, view: ReadView = "summary"
    ) -> dict[str, Any]:
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
        parent_key: str | None = None,
        limit: int = 25,
        offset: int = 0,
        view: ReadView = "metadata",
    ) -> dict[str, Any]:
        """Read notes, optionally restricted to a parent item."""
        params = {**pagination(limit, offset), "itemType": "note"}
        items = (
            self.zot.children(key(parent_key), **params) if parent_key else self.zot.items(**params)
        )
        return self._page(items, limit, offset, view=view)

    @operation()
    def get_annotations(
        self,
        attachment_key: str,
        limit: int = 25,
        offset: int = 0,
        view: ReadView = "metadata",
    ) -> dict[str, Any]:
        """Read Zotero reader annotations belonging to an attachment."""
        items = self.zot.children(
            key(attachment_key), itemType="annotation", **pagination(limit, offset)
        )
        return self._page(items, limit, offset, view=view)

    def _read_indexed_attachment(self, attachment_key: str) -> tuple[str, dict[str, Any]]:
        """Read one attachment; callers decide whether selecting it is allowed."""
        try:
            response = self.zot.fulltext_item(attachment_key)
        except ResourceNotFoundError as exc:
            raise FulltextError(
                "FULLTEXT_NOT_INDEXED",
                "No indexed text is available for this attachment. Index it in Zotero first.",
                details={"attachment_key": attachment_key},
            ) from exc
        content, response = parse_fulltext_response(response, attachment_key=attachment_key)
        if not content:
            raise FulltextError(
                "FULLTEXT_NOT_INDEXED",
                "No indexed text is available for this attachment. Index it in Zotero first.",
                details={"attachment_key": attachment_key},
            )
        return content, response

    @staticmethod
    def _check_text_revision(
        content: str, expected: str | None, *, attachment_key: str
    ) -> str:
        actual = text_revision(content)
        if expected is not None and (not isinstance(expected, str) or expected != actual):
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
        item_key: str,
        offset: int = 0,
        max_characters: int = 20000,
        attachment_key: str | None = None,
        text_revision: str | None = None,
    ) -> FulltextChunk:
        """Read Zotero-indexed text from one stable attachment.

        Pass a parent item key to select a sole attachment or a sole PDF. If the
        parent has multiple plausible attachments, provide ``attachment_key`` from
        the returned candidate list. Offsets are zero-based Unicode code points;
        ``text_revision`` must be reused for subsequent chunks.
        """
        pagination(1, offset)
        if type(max_characters) is not int or not 1 <= max_characters <= 50000:
            raise ValueError("max_characters must be between 1 and 50000.")
        item_key = key(item_key)
        if attachment_key is not None:
            selected_key = key(attachment_key)
        else:
            item = self.zot.item(item_key)
            if item.get("data", {}).get("itemType") == "attachment":
                selected_key = item_key
            else:
                attachments = self.zot.everything(
                    self.zot.children(item_key, itemType="attachment")
                )
                selected = select_attachment(attachments, parent_key=item_key)
                selected_key = key(selected["key"])

        content, response = self._read_indexed_attachment(selected_key)
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
        attachment_key: str,
        query: str,
        context_characters: int = 120,
        max_matches: int = 20,
        text_revision: str | None = None,
    ) -> FulltextFindResult:
        """Find literal, case-insensitive text in one explicitly named attachment.

        Match and snippet positions are zero-based Unicode code points in the
        unmodified Zotero index text. This does not search parent items or the
        whole library, and does not use regular expressions.
        """
        attachment_key = key(attachment_key)
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must contain non-whitespace text.")
        if type(context_characters) is not int or not 0 <= context_characters <= 5000:
            raise ValueError("context_characters must be between 0 and 5000.")
        if type(max_matches) is not int or not 1 <= max_matches <= 100:
            raise ValueError("max_matches must be between 1 and 100.")
        content, response = self._read_indexed_attachment(attachment_key)
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
    def get_item_schema(self, item_type: str | None = None) -> dict[str, Any]:
        """List item types, or fields and creator types for one type, from local Zotero."""
        if item_type is None:
            return {"item_types": self.zot.item_types()}
        return {
            "fields": self.zot.item_type_fields(item_type),
            "creator_types": self.zot.item_creator_types(item_type),
        }

    @operation()
    def export_items(
        self, item_keys: list[str], format: Literal["bibtex", "ris", "csljson"] = "bibtex"
    ) -> dict[str, Any]:
        """Export up to 50 items using Zotero's built-in local translators."""
        if not 1 <= len(item_keys) <= 50:
            raise ValueError("Provide 1 to 50 item keys.")
        result = self.zot.items(itemKey=",".join(key(k) for k in item_keys), format=format)
        return {
            "format": format,
            "content": result if format == "csljson" else self.zot.request.text,
        }

    @operation(write=True)
    def add_items(self, items: ItemDataList) -> WriteResponse:
        """Create 1 to 50 items, notes or annotations from Zotero item-data objects.

        Use zotero_get_item_schema to check fields. Child notes/annotations need
        parentItem. Returns each successful and failed batch entry; a batch may
        partially succeed. Never resubmit successful entries.
        """
        if not 1 <= len(items) <= 50:
            raise ValueError("Provide 1 to 50 items per call.")
        for item in items:
            if not isinstance(item.get("itemType"), str) or not item["itemType"]:
                raise ValueError("Each item must have an itemType.")
            if {"key", "version", "data"} & item.keys():
                raise ValueError(
                    "Pass new item-data objects; use zotero_update_item to change existing items."
                )
        return write_result(
            self.zot.create_items(copy.deepcopy(items)),
            input_count=len(items),
        )

    @operation(write=True)
    def update_item(
        self, item_key: ItemKey, version: Version, changes: dict[str, Any]
    ) -> WriteResponse:
        """Patch item metadata, a note or annotation using its last-read local version.

        Only supplied fields change. Arrays such as tags, creators and collections
        replace the existing array. A concurrent change fails without a retry.
        """
        if not changes or {"key", "version", "data", "library"} & changes.keys():
            raise ValueError("Provide changed fields only, without key/version/data/library.")
        payload = {
            **copy.deepcopy(changes),
            "key": key(item_key),
            "version": version_number(version),
        }
        if self.zot.update_item(payload) is not True:
            raise LibraryError(
                "UNKNOWN_WRITE_RESULT",
                "Zotero did not confirm the update; inspect the item before retrying.",
            )
        return {
            "ok": True,
            "status": "success",
            "key": payload["key"],
            "results": [{"input_index": 0, "key": payload["key"], "status": "success"}],
        }

    @operation(write=True)
    def delete_item(self, item_key: ItemKey, version: Version) -> WriteResponse:
        """Permanently delete an item (including child items), note or annotation.

        This does not move it to Trash. Supply its last-read local version.
        """
        payload = {"key": key(item_key), "version": version_number(version)}
        if self.zot.delete_item(payload) is not True:
            raise RuntimeError(
                "Zotero did not confirm deletion; check the library before retrying."
            )
        return {
            "ok": True,
            "status": "success",
            "key": payload["key"],
            "results": [{"input_index": 0, "key": payload["key"], "status": "success"}],
        }

    @operation(write=True)
    def create_note(
        self,
        parent_key: ItemKey,
        text: str,
        title: str = "",
        as_html: bool = False,
        tags: list[str] | None = None,
    ) -> WriteResponse:
        """Create a child note. Escape plain text by default; as_html preserves Zotero HTML."""
        content = text if as_html else "<p>" + html.escape(text).replace("\n", "<br/>") + "</p>"
        if title:
            content = f"<h1>{html.escape(title)}</h1>{content}"
        return self.add_items(
            [
                {
                    "itemType": "note",
                    "parentItem": key(parent_key),
                    "note": content,
                    "tags": [{"tag": tag} for tag in (tags or [])],
                }
            ]
        )

    @operation(write=True)
    def create_collection(self, name: str, parent_collection: str | None = None) -> WriteResponse:
        """Create a collection, optionally under a parent collection."""
        if not name.strip():
            raise ValueError("A collection name cannot be empty.")
        payload = {
            "name": name,
            "parentCollection": key(parent_collection) if parent_collection else False,
        }
        return write_result(self.zot.create_collections([payload]), input_count=1)

    @operation(write=True)
    def update_collection(
        self,
        collection_key: ItemKey,
        version: Version,
        name: str | None = None,
        parent_collection: str | None = None,
    ) -> WriteResponse:
        """Rename/move a collection. Use parent_collection='' to move it to the root."""
        if name is None and parent_collection is None:
            raise ValueError("Provide a name or parent_collection to change.")
        if name is not None and not name.strip():
            raise ValueError("A collection name cannot be empty.")
        collection_key = key(collection_key)
        version_number(version)
        current = self.zot.collection(collection_key)
        if current["version"] != version:
            raise LibraryError(
                "VERSION_CONFLICT",
                "The collection changed. Re-read its current local version before retrying.",
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
        if self.zot.update_collection(payload) is not True:
            raise RuntimeError("Zotero did not confirm the collection update.")
        return {
            "ok": True,
            "status": "success",
            "key": collection_key,
            "results": [{"input_index": 0, "key": collection_key, "status": "success"}],
        }

    @operation(write=True)
    def delete_collection(self, collection_key: ItemKey, version: Version) -> WriteResponse:
        """Permanently delete a collection. Library items remain; child collections are also removed."""
        payload = {"key": key(collection_key), "version": version_number(version)}
        if self.zot.delete_collection(payload) is not True:
            raise RuntimeError("Zotero did not confirm collection deletion.")
        return {
            "ok": True,
            "status": "success",
            "key": payload["key"],
            "results": [{"input_index": 0, "key": payload["key"], "status": "success"}],
        }

    def _edit_lists(
        self, item_keys: list[str], field: str, change: Callable[[list], list]
    ) -> dict[str, Any]:
        if not 1 <= len(item_keys) <= 50:
            raise ValueError("Provide 1 to 50 item keys.")
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
            "updated": [],
            "unchanged": [],
            "failed": {},
            "pending": [],
        }
        for position, (input_index, item_key) in enumerate(indexed_keys):
            try:
                item = self.zot.item(item_key)
                original = item["data"].get(field, [])
                updated = change(copy.deepcopy(original))
                if updated == original:
                    result["unchanged"].append(item_key)
                    result["results"].append(
                        {"input_index": input_index, "key": item_key, "status": "unchanged"}
                    )
                    continue
                self.update_item(item_key, item["version"], {field: updated})
                result["updated"].append(item_key)
                result["results"].append(
                    {"input_index": input_index, "key": item_key, "status": "success"}
                )
            except Exception as exc:
                # Keep completed work visible; never silently replay a partial batch.
                error = from_exception(exc, write=True, operation=f"batch_{field}")
                result["ok"] = False
                result["status"] = "partial"
                result["failed"][item_key] = error.as_dict()["error"]
                result["results"].append(
                    {
                        "input_index": input_index,
                        "key": item_key,
                        "status": "failed",
                        "error": error.as_dict()["error"],
                    }
                )
                for pending_index, pending_key in indexed_keys[position + 1 :]:
                    result["pending"].append(pending_key)
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
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> WriteResponse:
        """Add/remove tags while preserving other tags and tag types.

        Read each item's current local version before writing. On the first error,
        stop and report updated, failed and pending keys. At most 50 items.
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

    @operation(write=True)
    def manage_collection_items(
        self,
        collection_key: ItemKey,
        item_keys: ItemKeyList,
        action: Literal["add", "remove"],
    ) -> WriteResponse:
        """Add/remove up to 50 items from a collection, preserving other memberships.

        Stop at the first failure and report completed and pending keys.
        Removing membership does not delete an item.
        """
        collection_key = key(collection_key)
        if action not in {"add", "remove"}:
            raise ValueError("action must be add or remove.")
        if action == "add":
            self.zot.collection(collection_key)

        def change(collections):
            if action == "add" and collection_key not in collections:
                return [*collections, collection_key]
            if action == "remove":
                return [c for c in collections if c != collection_key]
            return collections

        return self._edit_lists(item_keys, "collections", change)

    @operation(write=True)
    def attach_file(
        self, parent_key: ItemKey, file_path: str, title: str | None = None
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
            "contentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        }
        created = write_result(self.zot.create_items([metadata]), input_count=1)
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
                "UNKNOWN_WRITE_RESULT",
                "Zotero created an attachment without returning its key; inspect the library.",
            )
        try:
            result = self.zot.upload_attachments(
                [{**metadata, "key": attachment_key}], basedir=str(path.parent)
            )
            if not isinstance(result, dict):
                raise LibraryError(
                    "UNKNOWN_WRITE_RESULT",
                    "Zotero returned an unexpected attachment upload response; inspect the library.",
                )
            failed = result.get("failure")
            return {
                **result,
                "ok": not bool(failed),
                "status": "failed" if failed else "success",
                "stage": "upload",
                "attachment_key": attachment_key,
                "results": [
                    {
                        "input_index": 0,
                        "key": attachment_key,
                        "status": "failed" if failed else "success",
                        "stage": "upload",
                        **({"error": failed} if failed else {}),
                    }
                ],
            }
        except LibraryError:
            raise
        except Exception as exc:
            error = from_exception(exc, write=True, operation="attach_file.upload")
            return {
                "ok": False,
                "status": "failed",
                "stage": "upload",
                "attachment_key": attachment_key,
                "results": [
                    {
                        "input_index": 0,
                        "key": attachment_key,
                        "status": "failed",
                        "stage": "upload",
                        "error": error.as_dict()["error"],
                    }
                ],
                "error": f"Attachment created but upload failed: {exc}. Inspect it before retrying.",
                "error_details": error.as_dict()["error"],
            }
