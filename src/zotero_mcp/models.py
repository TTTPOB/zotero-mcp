"""Small write contracts used by both the Python and MCP interfaces."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

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


class _WriteModel(BaseModel):
    """Serialize supplied write fields only, without null padding or extra fields."""

    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="wrap")
    def _serialize_fields_set(self, handler):
        return {
            name: value
            for name, value in handler(self).items()
            if name in self.model_fields_set and value is not None
        }


class ErrorPayload(_WriteModel):
    """Stable error fields carried by a failed MCP tool call."""

    code: str
    message: str
    retryable: bool = False
    details: Any = None


class ErrorResult(BaseModel):
    """MCP structuredContent shape for a failed tool call."""

    model_config = ConfigDict(extra="allow")

    error: ErrorPayload


class WriteEntry(_WriteModel):
    """Outcome for one input item, retaining its original position and key."""

    input_index: int = Field(ge=0)
    status: WriteStatus
    key: str | None = None
    version: int | None = Field(
        default=None,
        description="Known local entity version; omitted when unavailable.",
    )
    stage: WriteStage | None = None
    error: Any = None


class WriteResult(_WriteModel):
    """Compact per-input outcomes shared by single and batch write tools."""

    ok: bool
    status: Literal["success", "partial", "failed", "unknown"] = "success"
    results: list[WriteEntry] = Field(default_factory=list)
    key: str | None = None
    version: int | None = Field(
        default=None,
        description="Known local entity version; omitted when unavailable.",
    )
    error: ErrorPayload | None = None

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
