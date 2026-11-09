"""What a computer's node answers about its agents' conversations, as
starlink-conversation gives them, and about an install its manager runs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True)


class Sender(_Answer):
    id: str | None
    name: str | None
    display_name: str | None


class Preview(_Answer):
    message_id: str
    sender: Sender | None
    sender_kind: str
    at_ms: int
    text: str


class Conversation(_Answer):
    thread_id: str
    name: str | None
    # the peer's, in a DM
    handle: str | None
    kind: Literal["dm", "group"]
    channel: str
    review: Literal["pending", "approved", "denied"]
    last_activity_at_ms: int
    pending_count: int
    member_count: int
    first: Preview | None
    latest: Preview | None


class Page(_Answer):
    conversations: tuple[Conversation, ...]
    total: int
    pending_review: int
    has_more: bool


class Member(_Answer):
    sender: Sender | None
    sender_kind: str
    is_self: bool


class Detail(_Answer):
    conversation: Conversation
    created_at_ms: int
    message_count: int
    # the latest to speak first
    members: tuple[Member, ...]


class Attachment(_Answer):
    attachment_id: str | None
    name: str
    kind: str
    state: str
    media_type: str | None
    size_bytes: int | None


class Entry(_Answer):
    message_id: str
    seq: int
    direction: Literal["inbound", "outbound"]
    sender: Sender | None
    sender_kind: str
    at_ms: int
    body: str
    system_message_kind: str | None
    attachments: tuple[Attachment, ...]


class History(_Answer):
    messages: tuple[Entry, ...]
    has_before: bool
    has_after: bool


class Hit(_Answer):
    message_id: str
    thread_id: str
    conversation: str | None
    kind: Literal["dm", "group"]
    channel: str
    direction: Literal["inbound", "outbound"]
    sender: Sender | None
    sender_kind: str
    at_ms: int
    snippet: str
    # the snippet in pieces, each saying whether it is a fragment of the query
    parts: tuple[tuple[str, bool], ...]
    score: float


class Found(_Answer):
    messages: tuple[Hit, ...]
    total: int
    has_more: bool


class Segment(_Answer):
    media_type: str | None
    size_bytes: int
    # base64
    data: str


class Reminder(_Answer):
    reminder_id: str
    thread_id: str
    # what the conversation is called and what kind it is, as the list has them
    conversation: str | None
    kind: Literal["dm", "group"]
    title: str
    next_fire_at_ms: int
    # every:<N><m|h|d>, daily@HH:MM or weekly:<days>@HH:MM
    repeat_rule: str | None
    timezone: str


class Operation(_Answer):
    """How an install the computer's manager runs goes."""

    id: str
    phase: str
    error: str | None = None
