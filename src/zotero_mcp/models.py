"""Small write contracts used by both the Python and MCP interfaces."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ItemKey = Annotated[
    str,
    Field(
        description="Zotero item or collection key.",
        min_length=8,
        max_length=8,
        pattern=r"^[A-Za-z0-9]{8}$",
    ),
]
Version = Annotated[
    int,
    Field(
        description="Last-read LOCAL version of the item or collection; never use a cloud version.",
        ge=0,
    ),
]
ItemKeyList = Annotated[
    list[ItemKey], Field(description="1–50 Zotero item keys.", min_length=1, max_length=50)
]
ItemData = dict[str, Any]
ItemDataList = Annotated[
    list[ItemData],
    Field(
        description="1–50 new Zotero item-data objects; use get_item_schema to check fields.",
        min_length=1,
        max_length=50,
    ),
]

WriteStatus = Literal["success", "unchanged", "failed", "pending", "unknown"]
WriteStage = Literal["create", "upload"]


class ErrorPayload(BaseModel):
    """Stable error fields carried by a failed MCP tool call."""

    model_config = ConfigDict(extra="allow")

    code: str
    message: str
    retryable: bool = False
    details: Any = None


class ErrorResult(BaseModel):
    """MCP structuredContent shape for a failed tool call."""

    model_config = ConfigDict(extra="allow")

    error: ErrorPayload


class WriteEntry(BaseModel):
    """Outcome for one input item, retaining its original position and key."""

    model_config = ConfigDict(extra="allow")

    input_index: int = Field(ge=0)
    status: WriteStatus
    key: str | None = None
    version: int | None = Field(
        default=None,
        description="New local version after update_item/update_collection; null when unknown.",
    )
    stage: WriteStage | None = None
    error: Any = None


class WriteResult(BaseModel):
    """Success schema shared by single and batch write tools.

    The legacy aggregate fields stay available for callers while ``results`` is
    the canonical per-input representation.
    """

    model_config = ConfigDict(extra="allow")

    ok: bool
    status: Literal["success", "partial", "failed", "unknown"] = "success"
    results: list[WriteEntry] = Field(default_factory=list)
    key: str | None = None
    version: int | None = Field(
        default=None,
        description="New local version after update_item/update_collection; null when unknown.",
    )
    attachment_key: str | None = None
    stage: WriteStage | None = None
    successful: dict[str, Any] = Field(default_factory=dict)
    success: dict[str, Any] = Field(default_factory=dict)
    unchanged: list[str] | dict[str, Any] = Field(default_factory=list)
    failed: dict[str, Any] = Field(default_factory=dict)
    unknown: dict[str, Any] = Field(default_factory=dict)
    pending: list[str] = Field(default_factory=list)
    error: ErrorPayload | str | dict[str, Any] | None = None


# Keep writes as one direct object schema so MCP errors and successes have the
# same top-level shape and are not hidden under FastMCP's generic ``result`` key.
WriteResponse = WriteResult


__all__ = [
    "ErrorPayload",
    "ErrorResult",
    "ItemData",
    "ItemDataList",
    "ItemKey",
    "ItemKeyList",
    "Version",
    "WriteEntry",
    "WriteResponse",
    "WriteResult",
]
