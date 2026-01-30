"""Google Calendar API Pydantic models."""

from pydantic import BaseModel


class EventDateTime(BaseModel):
    """DateTime specification for calendar events."""

    date: str | None = None  # For all-day events (YYYY-MM-DD)
    dateTime: str | None = None  # For timed events (RFC3339)
    timeZone: str | None = None


class EventAttendee(BaseModel):
    """Event attendee."""

    email: str
    displayName: str | None = None
    responseStatus: str | None = None  # needsAction, declined, tentative, accepted
    optional: bool | None = None
    organizer: bool | None = None
    self: bool | None = None


class EventReminder(BaseModel):
    """Event reminder."""

    method: str  # "email" or "popup"
    minutes: int


class EventReminders(BaseModel):
    """Event reminders configuration."""

    useDefault: bool = True
    overrides: list[EventReminder] | None = None


class ConferenceData(BaseModel):
    """Conference/meeting data."""

    conferenceId: str | None = None
    conferenceSolution: dict | None = None
    entryPoints: list[dict] | None = None


class EventRequest(BaseModel):
    """Request body for creating/updating an event."""

    summary: str | None = None
    description: str | None = None
    location: str | None = None
    start: EventDateTime | None = None
    end: EventDateTime | None = None
    attendees: list[EventAttendee] | None = None
    reminders: EventReminders | None = None
    recurrence: list[str] | None = None
    colorId: str | None = None
    transparency: str | None = None  # opaque, transparent
    visibility: str | None = None  # default, public, private, confidential
    guestsCanInviteOthers: bool | None = None
    guestsCanModify: bool | None = None
    guestsCanSeeOtherGuests: bool | None = None


class Event(BaseModel):
    """Google Calendar event (response)."""

    id: str | None = None
    status: str | None = None  # confirmed, tentative, cancelled
    htmlLink: str | None = None
    created: str | None = None
    updated: str | None = None
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    colorId: str | None = None
    creator: dict | None = None
    organizer: dict | None = None
    start: EventDateTime | None = None
    end: EventDateTime | None = None
    recurrence: list[str] | None = None
    recurringEventId: str | None = None
    attendees: list[EventAttendee] | None = None
    reminders: EventReminders | None = None
    conferenceData: ConferenceData | None = None
    iCalUID: str | None = None


class EventListResponse(BaseModel):
    """Response from listing events."""

    kind: str | None = None
    summary: str | None = None
    description: str | None = None
    updated: str | None = None
    timeZone: str | None = None
    accessRole: str | None = None
    nextPageToken: str | None = None
    nextSyncToken: str | None = None
    items: list[Event] | None = None


class Calendar(BaseModel):
    """Calendar metadata."""

    id: str
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    timeZone: str | None = None
    colorId: str | None = None
    backgroundColor: str | None = None
    foregroundColor: str | None = None
    accessRole: str | None = None
    primary: bool | None = None


class CalendarListEntry(BaseModel):
    """Entry in calendar list."""

    id: str
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    timeZone: str | None = None
    colorId: str | None = None
    backgroundColor: str | None = None
    foregroundColor: str | None = None
    accessRole: str | None = None
    primary: bool | None = None
    selected: bool | None = None
    hidden: bool | None = None


class CalendarListResponse(BaseModel):
    """Response from listing calendars."""

    kind: str | None = None
    nextPageToken: str | None = None
    nextSyncToken: str | None = None
    items: list[CalendarListEntry] | None = None
