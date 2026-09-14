"""Local API operations. Pyzotero owns HTTP, authorization headers and versions."""

import copy
import html
import mimetypes
import re
import threading
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, Literal

from pyzotero import (
    LocalAPIKeyRequiredError,
    PreConditionFailedError,
    ResourceNotFoundError,
    ServerIDMismatchError,
    Zotero,
)

from zotero_mcp.config import Settings, load_auth
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


def page(
    items: list,
    limit: int,
    offset: int,
    *,
    view: ReadView = "raw",
    consumed_count: int | None = None,
) -> dict[str, Any]:
    """Build a projected page using a bounded native-count fallback."""
    normalize_view(view)
    projected = project_items(items, view)
    upstream_count = len(items) if consumed_count is None else consumed_count
    if type(upstream_count) is not int or upstream_count < 0:
        raise ValueError("consumed_count must be a nonnegative integer.")
    # A full last page can require one final empty request; no unbounded scan.
    return {
        "items": projected,
        "offset": offset,
        "next_offset": offset + upstream_count if upstream_count == limit else None,
    }


def write_result(result: dict) -> dict[str, Any]:
    if not isinstance(result, dict) or not {"successful", "success", "failed"} & result.keys():
        raise RuntimeError(
            "Zotero returned an unexpected write response; check the library before retrying."
        )
    if not any(result.get(field) for field in ("successful", "success", "unchanged", "failed")):
        raise RuntimeError("Zotero returned no item results; check the library before retrying.")
    return {
        "ok": not bool(result.get("failed")),
        "successful": result.get("successful", {}),
        "success": result.get("success", {}),
        "unchanged": result.get("unchanged", {}),
        "failed": result.get("failed", {}),
    }


def operation(*, write: bool = False):
    """Serialize the shared pyzotero client, including its mutable query state."""

    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            with self._lock:
                zot = self.zot
                try:
                    if write and not zot.local_api_key:
                        raise ValueError(
                            "Run zotero-mcp authorize and approve Always Allow in Zotero first."
                        )
                    return method(self, *args, **kwargs)
                except LocalAPIKeyRequiredError as exc:
                    raise ValueError(
                        "Local permission expired or was revoked. Run zotero-mcp authorize again."
                    ) from exc
                except ServerIDMismatchError as exc:
                    raise ValueError(
                        "Zotero's database/server ID changed. Check the active profile, then authorize again."
                    ) from exc
                except PreConditionFailedError as exc:
                    raise ValueError(
                        "The item or collection changed. Read its current local version before retrying."
                    ) from exc
                finally:
                    # pyzotero clears these on success, but some failures leave them set.
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
                raise ValueError("Only the Zotero local API is supported.")
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
        return page(
            items,
            limit,
            offset,
            view=view,
            consumed_count=consumed_count,
        )

    @operation()
    def search_items(
        self,
        query: str = "",
        tags: list[str] | None = None,
        item_type: str = "-attachment",
        qmode: Literal["titleCreatorYear", "everything"] = "titleCreatorYear",
        limit: int = 25,
        offset: int = 0,
        view: ReadView = "summary",
    ) -> dict[str, Any]:
        """Search local metadata, tags or Zotero-indexed text as a read view.

        qmode='everything' includes indexed attachment text. Tag filters use Zotero's
        syntax: separate list entries are AND; 'a || b' is OR; '-tag' excludes a tag.
        """
        normalize_view(view)
        if not query.strip() and not tags:
            raise ValueError("Provide a query or tags; use zotero_get_recent to browse.")
        params = pagination(limit, offset)
        params.update(q=query, qmode=qmode, itemType=item_type)
        if tags:
            params["tag"] = tags
        return self._page(self.zot.items(**params), limit, offset, view=view)

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

    @operation()
    def get_item_fulltext(
        self, item_key: str, offset: int = 0, max_characters: int = 20000
    ) -> dict[str, Any]:
        """Read Zotero's indexed text. Accept a paper or attachment key.

        For a paper, select its first indexed attachment (PDFs first). For other
        attachments, pass their key directly. Offsets count Unicode characters.
        No OCR or separate PDF extraction is performed.
        """
        pagination(1, offset)
        if type(max_characters) is not int or not 1 <= max_characters <= 50000:
            raise ValueError("max_characters must be between 1 and 50000.")
        item = self.zot.item(key(item_key))
        if item["data"]["itemType"] == "attachment":
            attachments = [item]
        else:
            attachments = self.zot.everything(
                self.zot.children(key(item_key), itemType="attachment")
            )
            attachments.sort(key=lambda a: a["data"].get("contentType") != "application/pdf")
        for attachment in attachments:
            attachment_key = attachment["key"]
            try:
                fulltext = self.zot.fulltext_item(attachment_key)
            except ResourceNotFoundError:
                continue
            content = fulltext.get("content", "")
            if not content:
                continue
            end = min(offset + max_characters, len(content))
            return {
                "attachment_key": attachment_key,
                "content": content[offset:end],
                "offset": offset,
                "next_offset": end if end < len(content) else None,
                "total_characters": len(content),
                "index": {k: v for k, v in fulltext.items() if k != "content"},
            }
        raise ValueError(
            "No indexed attachment text is available. Index the attachment in Zotero first."
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
    def add_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
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
        return write_result(self.zot.create_items(copy.deepcopy(items)))

    @operation(write=True)
    def update_item(self, item_key: str, version: int, changes: dict[str, Any]) -> dict[str, Any]:
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
            raise RuntimeError("Zotero did not confirm the update; check the item before retrying.")
        return {"ok": True, "key": payload["key"]}

    @operation(write=True)
    def delete_item(self, item_key: str, version: int) -> dict[str, Any]:
        """Permanently delete an item (including child items), note or annotation.

        This does not move it to Trash. Supply its last-read local version.
        """
        payload = {"key": key(item_key), "version": version_number(version)}
        if self.zot.delete_item(payload) is not True:
            raise RuntimeError(
                "Zotero did not confirm deletion; check the library before retrying."
            )
        return {"ok": True, "key": payload["key"]}

    @operation(write=True)
    def create_note(
        self,
        parent_key: str,
        text: str,
        title: str = "",
        as_html: bool = False,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
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
    def create_collection(self, name: str, parent_collection: str | None = None) -> dict[str, Any]:
        """Create a collection, optionally under a parent collection."""
        if not name.strip():
            raise ValueError("A collection name cannot be empty.")
        payload = {
            "name": name,
            "parentCollection": key(parent_collection) if parent_collection else False,
        }
        return write_result(self.zot.create_collections([payload]))

    @operation(write=True)
    def update_collection(
        self,
        collection_key: str,
        version: int,
        name: str | None = None,
        parent_collection: str | None = None,
    ) -> dict[str, Any]:
        """Rename/move a collection. Use parent_collection='' to move it to the root."""
        if name is None and parent_collection is None:
            raise ValueError("Provide a name or parent_collection to change.")
        if name is not None and not name.strip():
            raise ValueError("A collection name cannot be empty.")
        collection_key = key(collection_key)
        version_number(version)
        current = self.zot.collection(collection_key)
        if current["version"] != version:
            raise ValueError(
                "The collection changed. Read its current local version before retrying."
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
        return {"ok": True, "key": collection_key}

    @operation(write=True)
    def delete_collection(self, collection_key: str, version: int) -> dict[str, Any]:
        """Permanently delete a collection. Library items remain; child collections are also removed."""
        payload = {"key": key(collection_key), "version": version_number(version)}
        if self.zot.delete_collection(payload) is not True:
            raise RuntimeError("Zotero did not confirm collection deletion.")
        return {"ok": True, "key": payload["key"]}

    def _edit_lists(
        self, item_keys: list[str], field: str, change: Callable[[list], list]
    ) -> dict[str, Any]:
        if not 1 <= len(item_keys) <= 50:
            raise ValueError("Provide 1 to 50 item keys.")
        keys = list(dict.fromkeys(key(k) for k in item_keys))
        result = {"ok": True, "updated": [], "unchanged": [], "failed": {}, "pending": []}
        for index, item_key in enumerate(keys):
            try:
                item = self.zot.item(item_key)
                original = item["data"].get(field, [])
                updated = change(copy.deepcopy(original))
                if updated == original:
                    result["unchanged"].append(item_key)
                    continue
                self.update_item(item_key, item["version"], {field: updated})
                result["updated"].append(item_key)
            except Exception as exc:
                # Keep completed work visible; never silently replay a partial batch.
                result.update(ok=False, failed={item_key: str(exc)}, pending=keys[index + 1 :])
                break
        return result

    @operation(write=True)
    def batch_update_tags(
        self, item_keys: list[str], add: list[str] | None = None, remove: list[str] | None = None
    ) -> dict[str, Any]:
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
        self, collection_key: str, item_keys: list[str], action: Literal["add", "remove"]
    ) -> dict[str, Any]:
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
        self, parent_key: str, file_path: str, title: str | None = None
    ) -> dict[str, Any]:
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
        created = write_result(self.zot.create_items([metadata]))
        if not created["ok"]:
            return created
        attachment_key = created["success"].get("0")
        if not attachment_key:
            attachment_key = created["successful"].get("0", {}).get("key")
        if not attachment_key:
            raise RuntimeError(
                "Zotero created an attachment without returning its key; check the library."
            )
        try:
            result = self.zot.upload_attachments(
                [{**metadata, "key": attachment_key}], basedir=str(path.parent)
            )
            return {"ok": not bool(result["failure"]), "attachment_key": attachment_key, **result}
        except Exception as exc:
            return {
                "ok": False,
                "attachment_key": attachment_key,
                "error": f"Attachment created but upload failed: {exc}. Inspect it before retrying.",
            }
