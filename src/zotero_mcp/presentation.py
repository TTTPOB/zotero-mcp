"""Stable task views for Zotero read results."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

ReadView = Literal["summary", "metadata", "raw"]


class FulltextEntry(TypedDict, total=False):
    read_key: str
    candidate_attachment_key: str
    index_state: Literal["unknown"]


class MatchDetails(TypedDict):
    kind: Literal["native"]
    matched_attachment_key: str | None
    parent_key: str | None


class ItemSummary(TypedDict):
    key: str | None
    version: int | None
    item_type: str | None
    title: str
    year: int | None
    creator_summary: str
    doi: str | None
    parent_key: str | None
    matched_attachment_key: str | None
    match: MatchDetails
    fulltext: FulltextEntry


class ItemMetadata(TypedDict):
    key: str | None
    version: int | None
    item_type: str | None
    parent_key: str | None
    data: dict[str, Any]


class ItemPage(TypedDict, total=False):
    items: list[dict[str, Any]]
    offset: int
    next_offset: int | None
    total: int


_VALID_VIEWS = {"summary", "metadata", "raw"}
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def normalize_view(view: str) -> ReadView:
    if not isinstance(view, str) or view not in _VALID_VIEWS:
        raise ValueError("view must be one of: summary, metadata, raw.")
    return view  # type: ignore[return-value]


def _data(item: Mapping[str, Any]) -> Mapping[str, Any]:
    value = item.get("data")
    return value if isinstance(value, Mapping) else {}


def _parent_key(data: Mapping[str, Any]) -> str | None:
    parent = data.get("parentItem")
    return parent if isinstance(parent, str) and parent else None


def _year(data: Mapping[str, Any]) -> int | None:
    explicit = data.get("year")
    if isinstance(explicit, int) and not isinstance(explicit, bool):
        return explicit
    if isinstance(explicit, str) and explicit.isdigit() and len(explicit) == 4:
        return int(explicit)
    date = data.get("date")
    if isinstance(date, str):
        match = _YEAR_RE.search(date)
        if match:
            return int(match.group(0))
    return None


def _creator_name(creator: Mapping[str, Any]) -> str:
    last = creator.get("lastName")
    first = creator.get("firstName")
    name = " ".join(str(part).strip() for part in (first, last) if part)
    if name:
        return name
    literal = creator.get("name")
    return str(literal).strip() if literal else ""


def _creator_summary(data: Mapping[str, Any]) -> str:
    creators = data.get("creators")
    if not isinstance(creators, Sequence) or isinstance(creators, (str, bytes)):
        return ""
    names = [_creator_name(creator) for creator in creators if isinstance(creator, Mapping)]
    names = [name for name in names if name]
    if not names:
        return ""
    return names[0] if len(names) == 1 else f"{names[0]} et al."


def _doi(data: Mapping[str, Any]) -> str | None:
    value = data.get("DOI", data.get("doi"))
    return value if isinstance(value, str) and value else None


def _summary(item: Mapping[str, Any]) -> ItemSummary:
    data = _data(item)
    item_key = item.get("key")
    item_key = item_key if isinstance(item_key, str) else None
    item_type = data.get("itemType")
    item_type = item_type if isinstance(item_type, str) else None
    parent_key = _parent_key(data)
    matched_attachment_key = item_key if item_type == "attachment" else None
    title = data.get("title")
    if not isinstance(title, str):
        title = data.get("filename") if isinstance(data.get("filename"), str) else ""
    match: MatchDetails = {
        "kind": "native",
        "matched_attachment_key": matched_attachment_key,
        "parent_key": parent_key,
    }
    fulltext: FulltextEntry = {
        "read_key": item_key or "",
        "index_state": "unknown",
    }
    if matched_attachment_key:
        fulltext["candidate_attachment_key"] = matched_attachment_key
    return {
        "key": item_key,
        "version": item.get("version") if isinstance(item.get("version"), int) else None,
        "item_type": item_type,
        "title": title,
        "year": _year(data),
        "creator_summary": _creator_summary(data),
        "doi": _doi(data),
        "parent_key": parent_key,
        "matched_attachment_key": matched_attachment_key,
        "match": match,
        "fulltext": fulltext,
    }


def _metadata(item: Mapping[str, Any]) -> ItemMetadata:
    data = copy.deepcopy(dict(_data(item)))
    item_type = data.get("itemType")
    item_type = item_type if isinstance(item_type, str) else None
    return {
        "key": item.get("key") if isinstance(item.get("key"), str) else None,
        "version": item.get("version") if isinstance(item.get("version"), int) else None,
        "item_type": item_type,
        "parent_key": _parent_key(data),
        "data": data,
    }


def project_item(item: Mapping[str, Any], view: ReadView = "summary") -> dict[str, Any]:
    """Project one native Zotero item without mutating the source object."""
    view = normalize_view(view)
    if view == "raw":
        return copy.deepcopy(dict(item))
    if view == "metadata":
        return dict(_metadata(item))
    return dict(_summary(item))


def project_items(items: Sequence[Mapping[str, Any]], view: ReadView = "summary") -> list[dict[str, Any]]:
    """Project items while preserving the upstream order and item count."""
    return [project_item(item, view) for item in items]
