"""Stable task views and schemas for Zotero read results."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ReadView = Literal["summary", "metadata", "raw"]
ReadItemKey = Annotated[str, Field(min_length=8, max_length=8, pattern=r"^[A-Za-z0-9]{8}$")]
ReadLimit = Annotated[int, Field(ge=1, le=100)]
ReadOffset = Annotated[int, Field(ge=0)]
ReadMaxCharacters = Annotated[int, Field(ge=1, le=50000)]
ReadContextCharacters = Annotated[int, Field(ge=0, le=5000)]
ReadMaxMatches = Annotated[int, Field(ge=1, le=100)]
ReadTag = Annotated[str, Field(min_length=1)]


class FulltextEntry(BaseModel):
    """A lightweight route to Zotero's fulltext reader."""

    model_config = ConfigDict(extra="allow")

    read_key: str | None = None
    candidate_attachment_key: str | None = None
    index_state: Literal["unknown"] | None = None


class MatchDetails(BaseModel):
    """Optional native match relation, never a guessed field or score."""

    model_config = ConfigDict(extra="allow")

    kind: Literal["native"] | None = None
    matched_attachment_key: str | None = None
    parent_key: str | None = None


class ItemSummary(BaseModel):
    """Stable discovery fields shared by search and list views."""

    model_config = ConfigDict(extra="allow")

    key: str | None = None
    version: int | None = None
    item_type: str | None = None
    title: str | None = None
    year: int | None = None
    creator_summary: str | None = None
    doi: str | None = None
    parent_key: str | None = None
    matched_attachment_key: str | None = None
    match_details_available: bool | None = None
    match: MatchDetails | None = None
    fulltext: FulltextEntry | None = None


class ItemMetadata(BaseModel):
    """Semantic item metadata while allowing an explicit raw escape hatch."""

    model_config = ConfigDict(extra="allow")

    key: str | None = None
    version: int | None = None
    item_type: str | None = None
    parent_key: str | None = None
    data: dict[str, Any] | None = None


class ItemPage(BaseModel):
    """A paged list whose item union preserves summary, metadata, and raw views."""

    model_config = ConfigDict(extra="allow")

    items: list[dict[str, Any] | ItemSummary | ItemMetadata] = Field(default_factory=list)
    offset: int = 0
    next_offset: int | None = None
    total: int | None = None


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


def _summary(item: Mapping[str, Any], *, search: bool) -> dict[str, Any]:
    data = _data(item)
    item_key = item.get("key")
    item_key = item_key if isinstance(item_key, str) else None
    item_type = data.get("itemType")
    item_type = item_type if isinstance(item_type, str) else None
    parent_key = _parent_key(data)
    title = data.get("title")
    if not isinstance(title, str):
        title = data.get("filename") if isinstance(data.get("filename"), str) else ""
    fulltext: dict[str, Any] = {"read_key": item_key or "", "index_state": "unknown"}
    if item_type == "attachment" and item_key:
        fulltext["candidate_attachment_key"] = item_key
    result: dict[str, Any] = {
        "key": item_key,
        "version": item.get("version") if isinstance(item.get("version"), int) else None,
        "item_type": item_type,
        "title": title,
        "year": _year(data),
        "creator_summary": _creator_summary(data),
        "doi": _doi(data),
        "parent_key": parent_key,
        "fulltext": fulltext,
    }
    if search:
        result.update(
            matched_attachment_key=item_key if item_type == "attachment" else None,
            match_details_available=False,
        )
    return result


def _metadata(item: Mapping[str, Any]) -> dict[str, Any]:
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


def project_item(
    item: Mapping[str, Any], view: ReadView = "summary", *, search: bool = False
) -> dict[str, Any]:
    """Project one native Zotero item without mutating the source object."""
    view = normalize_view(view)
    if view == "raw":
        return copy.deepcopy(dict(item))
    if view == "metadata":
        return _metadata(item)
    return _summary(item, search=search)


def project_items(
    items: Sequence[Mapping[str, Any]], view: ReadView = "summary", *, search: bool = False
) -> list[dict[str, Any]]:
    """Project items while preserving the upstream order and item count."""
    return [project_item(item, view, search=search) for item in items]
