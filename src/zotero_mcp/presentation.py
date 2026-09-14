"""Stable task views and schemas for Zotero read results."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_serializer

ReadView = Literal["summary", "metadata", "raw"]
ReadItemKey = Annotated[str, Field(min_length=8, max_length=8, pattern=r"^[A-Za-z0-9]{8}$")]
ReadLimit = Annotated[int, Field(ge=1, le=100)]
ReadOffset = Annotated[int, Field(ge=0)]
ReadTag = Annotated[str, Field(min_length=1)]


def _serialize_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_unset=True)
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    return value


class _FieldsSetModel(BaseModel):
    """Serialize only fields supplied by the business result, retaining raw extras."""

    model_config = ConfigDict(extra="allow")

    @model_serializer(mode="plain")
    def _serialize_fields_set(self) -> dict[str, Any]:
        result = {name: _serialize_value(getattr(self, name)) for name in self.model_fields_set}
        result.update(
            {name: _serialize_value(value) for name, value in (self.model_extra or {}).items()}
        )
        return result


class FulltextEntry(_FieldsSetModel):
    """A lightweight route to Zotero's fulltext reader."""

    read_key: str | None = None
    candidate_attachment_key: str | None = None
    index_state: Literal["unknown"] | None = None


class ItemSummary(_FieldsSetModel):
    """Stable discovery fields shared by search and list views."""

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
    fulltext: FulltextEntry | None = None


class ItemMetadata(_FieldsSetModel):
    """Semantic item metadata while allowing an explicit raw escape hatch."""

    key: str | None = None
    version: int | None = None
    item_type: str | None = None
    parent_key: str | None = None
    data: dict[str, Any] | None = None


class ItemPage(_FieldsSetModel):
    """A paged list whose item union preserves summary, metadata, and raw views."""

    items: list[dict[str, Any] | ItemSummary | ItemMetadata] = Field(default_factory=list)
    offset: int = 0
    next_offset: int | None = None
    total: int | None = None


_VALID_VIEWS = {"summary", "metadata", "raw"}
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_ENCLOSURE_KEY_RE = re.compile(r"/items/([A-Za-z0-9]{8})(?:/|$)")


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


def _enclosure_key(item: Mapping[str, Any]) -> str | None:
    links = item.get("links")
    enclosure = links.get("enclosure") if isinstance(links, Mapping) else None
    href = enclosure.get("href") if isinstance(enclosure, Mapping) else enclosure
    if not isinstance(href, str):
        return None
    path = urlparse(href).path
    matches = _ENCLOSURE_KEY_RE.findall(path)
    return matches[-1].upper() if matches else None


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
    candidate_key = item_key if item_type == "attachment" else _enclosure_key(item)
    if candidate_key:
        fulltext["candidate_attachment_key"] = candidate_key
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
