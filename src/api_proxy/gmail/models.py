"""Gmail-specific Pydantic models."""

from pydantic import BaseModel


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
