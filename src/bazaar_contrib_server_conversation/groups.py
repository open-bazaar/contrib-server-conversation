"""Conversations and messages seen by several agents in the same group."""

from __future__ import annotations

from dataclasses import dataclass
lazy import base64
lazy import json
lazy from collections.abc import Mapping, Sequence

lazy from bazaar_contrib_server_agent import Agent
lazy from pydantic import RootModel

from .models import Conversation, Entry, Preview, Sender


@dataclass(frozen=True, slots=True)
class Source:
    agent: Agent
    conversation: Conversation


@dataclass(frozen=True, slots=True)
class Row:
    agent: Agent
    conversation: Conversation
    sources: tuple[Source, ...] = ()

    @property
    def key(self) -> str:
        from uuid import NAMESPACE_URL, uuid5

        return str(
            uuid5(
                NAMESPACE_URL, json.dumps(group(Source(self.agent, self.conversation)))
            )
        )


class Said(Entry):
    source_message_id: str
    cursor: str
    agent_id: str
    author: str
    known: bool


def group(source: Source) -> tuple[str, ...]:
    """The external group and topic, with its bot kept on the source."""
    conversation = source.conversation
    if conversation.kind == "dm":
        return (source.agent.id, conversation.thread_id)
    address = conversation.provider_thread_id.split(":")
    scope = ""
    if conversation.channel == "lark":
        scope = next(
            (
                str(provider.values.get("region", "feishu"))
                for provider in source.agent.config.channel
                if provider.kind == "lark"
            ),
            "feishu",
        )
    match address:
        case ["telegram", _, chat, topic]:
            address = [chat, topic]
        case ["lark", "bot", _, "chat", chat, "thread", topic]:
            address = [chat, topic]
        case ["wecom", _, *peer]:
            address = peer
        case _:
            return (conversation.channel, scope, conversation.provider_thread_id)
    return (conversation.channel, scope, *address)


def identities(
    agents: Sequence[Agent], sources: Sequence[Source]
) -> dict[tuple[str, str], Agent]:
    found = {
        (status.kind, status.identity.id): agent
        for agent in agents
        if agent.activity is not None
        for status in agent.activity.channels
        if status.identity is not None
    }
    found.update(
        {
            (
                source.conversation.channel,
                source.conversation.channel_identity,
            ): source.agent
            for source in sources
            if source.conversation.channel_identity is not None
        }
    )
    return found


def who(
    source: Source,
    sender: Sender | None,
    direction: str,
    known: Mapping[tuple[str, str], Agent],
) -> tuple[Sender | None, Agent | None]:
    agent = (
        source.agent
        if direction == "outbound"
        else (
            known.get((source.conversation.channel, sender.id))
            if sender is not None and sender.id is not None
            else None
        )
    )
    if agent is not None:
        return Sender(id=agent.id, name=agent.name, display_name=agent.name), agent
    return sender, None


def rows(sources: Sequence[Source], agents: Sequence[Agent]) -> list[Row]:
    grouped: dict[tuple[str, ...], list[Source]] = {}
    for source in sources:
        grouped.setdefault(group(source), []).append(source)
    known = identities(agents, sources)
    result: list[Row] = []
    for peers in grouped.values():
        peers.sort(
            key=lambda source: (
                -source.conversation.last_activity_at_ms,
                source.agent.id,
                source.conversation.thread_id,
            )
        )
        source = peers[0]
        conversation = source.conversation
        previews: dict[str, Preview | None] = {}
        for name in ("first", "latest"):
            preview = getattr(conversation, name)
            previews[name] = (
                preview.model_copy(
                    update={
                        "sender": who(source, preview.sender, preview.direction, known)[
                            0
                        ]
                    }
                )
                if preview is not None
                else None
            )
        result.append(
            Row(source.agent, conversation.model_copy(update=previews), tuple(peers))
        )
    return sorted(
        result,
        key=lambda row: (-row.conversation.last_activity_at_ms, group(row.sources[0])),
    )


def position(message: Entry) -> tuple[int, str, str]:
    return (
        message.at_ms,
        message.provider_message_id
        or (
            message.source_message_id
            if isinstance(message, Said)
            else message.message_id
        ),
        message.edit_version or "",
    )


def cursor(value: tuple[int, str, str]) -> str:
    return "g:" + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip(
        "="
    )


def message_id(source: Source, entry: Entry) -> str:
    from uuid import NAMESPACE_URL, uuid5

    return str(
        uuid5(
            NAMESPACE_URL,
            json.dumps(
                (
                    group(source),
                    entry.provider_message_id
                    or f"{source.agent.id}:{entry.message_id}",
                    entry.edit_version,
                )
            ),
        )
    )


def located(value: str) -> tuple[int, str, str]:
    return (
        RootModel[tuple[int, str, str]]
        .model_validate_json(
            base64.urlsafe_b64decode(
                value.removeprefix("g:") + "=" * (-len(value.removeprefix("g:")) % 4)
            )
        )
        .root
    )


def messages(
    entries: Sequence[tuple[Source, Entry]],
    agents: Sequence[Agent],
    sources: Sequence[Source],
    *,
    grouped: bool,
) -> list[Said]:
    known = identities(agents, sources)
    selected: dict[tuple[str, str], tuple[Source, Entry]] = {}
    for source, entry in entries:
        key = (
            entry.provider_message_id or f"{source.agent.id}:{entry.message_id}",
            entry.edit_version or "",
        )
        previous = selected.get(key)
        if previous is None or (
            entry.direction == "outbound" and previous[1].direction != "outbound"
        ):
            selected[key] = source, entry
    result: list[Said] = []
    for source, entry in selected.values() if grouped else entries:
        sender, agent = who(source, entry.sender, entry.direction, known)
        result.append(
            Said(
                **(
                    entry.model_dump()
                    | {
                        "message_id": message_id(source, entry)
                        if grouped
                        else entry.message_id,
                        "sender": sender,
                    }
                ),
                source_message_id=entry.message_id,
                cursor=cursor(position(entry)) if grouped else entry.message_id,
                agent_id=source.agent.id,
                author=agent.name
                if agent is not None
                else (
                    (sender.display_name or sender.name or sender.id or "")
                    if sender
                    else ""
                ),
                known=agent is not None,
            )
        )
    return (
        sorted(result, key=lambda message: (*position(message), message.agent_id))
        if grouped
        else result
    )
