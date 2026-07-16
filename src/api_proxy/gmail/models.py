"""Gmail-specific Pydantic models."""

from pydantic import BaseModel, ConfigDict, field_validator


class ModifyMessageRequest(BaseModel):
    """Request body for modifying a message's labels."""

    addLabelIds: list[str] | None = None
    removeLabelIds: list[str] | None = None


class MessagePartBody(BaseModel):
    """Body of a message part."""

    attachmentId: str | None = None
    size: int = 0
    data: str | None = None


class MessagePartHeader(BaseModel):
    """Header of a message part."""

    name: str
    value: str


class MessagePart(BaseModel):
    """Part of a message."""

    partId: str | None = None
    mimeType: str | None = None
    filename: str | None = None
    headers: list[MessagePartHeader] | None = None
    body: MessagePartBody | None = None
    parts: list["MessagePart"] | None = None


class Message(BaseModel):
    """Gmail message."""

    id: str
    threadId: str
    labelIds: list[str] | None = None
    snippet: str | None = None
    historyId: str | None = None
    internalDate: str | None = None
    payload: MessagePart | None = None
    sizeEstimate: int | None = None
    raw: str | None = None


class MessageListResponse(BaseModel):
    """Response from listing messages."""

    messages: list[dict] | None = None  # Simplified: just id and threadId
    nextPageToken: str | None = None
    resultSizeEstimate: int | None = None


class Label(BaseModel):
    """Gmail label."""

    id: str
    name: str
    messageListVisibility: str | None = None
    labelListVisibility: str | None = None
    type: str | None = None
    messagesTotal: int | None = None
    messagesUnread: int | None = None
    threadsTotal: int | None = None
    threadsUnread: int | None = None
    color: dict | None = None


class LabelListResponse(BaseModel):
    """Response from listing labels."""

    labels: list[Label] | None = None


class DraftMessageBody(BaseModel):
    """Message body within a draft create/update request."""

    # Unknown keys are rejected so a misspelled or misplaced threadId fails
    # loudly instead of silently detaching the draft from its conversation.
    model_config = ConfigDict(extra="forbid")

    raw: str
    # Gmail thread to attach the draft to (reply threading). Optional and
    # forwarded as-is; without it Gmail puts the draft in a fresh thread.
    threadId: str | None = None
    # Output-only Message fields a conformant client round-trips from
    # drafts.get(format="raw"); accepted for compatibility, never forwarded.
    id: str | None = None
    labelIds: list[str] | None = None
    snippet: str | None = None
    historyId: str | None = None
    internalDate: str | None = None
    sizeEstimate: int | None = None

    @field_validator("threadId", mode="before")
    @classmethod
    def _coerce_numeric_thread_id(cls, value: object) -> object:
        # Accept a JSON number for threadId; bool is an int subclass and
        # stays invalid.
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value


class DraftRequest(BaseModel):
    """Request body for creating or updating a draft."""

    model_config = ConfigDict(extra="forbid")

    message: DraftMessageBody
    # Output-only Draft id (the target draft comes from the URL path);
    # accepted for compatibility, never forwarded.
    id: str | None = None

    def gmail_payload(self) -> dict:
        """The body to forward to Gmail: only the writable draft fields."""
        return self.model_dump(
            exclude_none=True,
            include={"message": {"raw", "threadId"}},
        )
