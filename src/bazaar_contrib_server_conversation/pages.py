"""The conversation pages: every agent's conversations together, a page of
them at a time as their agents give them, the messages of one with what
it is about beside them, the first contacts waiting for a decision and the
search through all of them; an agent's own conversations and reminders on
its page, and what needs a decision on the overview. Each follows what it
shows through the update bus, for agents the account may see."""

from __future__ import annotations

from dataclasses import dataclass
lazy from collections.abc import Mapping, Sequence
lazy from datetime import datetime, timedelta
lazy from time import time_ns
lazy from typing import Any, Literal
lazy from urllib.parse import urlencode

lazy from bazaar_contrib_server import Account, Pages, Server
lazy from bazaar_contrib_server_access import Access
lazy from bazaar_contrib_server_activity import Activity, Receipt, Turn
lazy from bazaar_contrib_server_agent import Agent, Agents
lazy from bazaar_contrib_server_computer import Computers
lazy from bazaar_contrib_sidecar import RemoteError
lazy from jinja2 import pass_context
lazy from jinja2.runtime import Context
lazy from pydantic import TypeAdapter
lazy from starlette.exceptions import HTTPException
lazy from starlette.requests import Request
lazy from starlette.responses import Response
lazy from starlette.routing import Route

lazy from .images import Images
lazy from .models import (
    Conversation,
    Detail,
    Entry,
    Found,
    History,
    Hit,
    Page,
    Reminder,
)
lazy from .reader import PAGE, Away, Reader
lazy from .service import Service

VIEW = "agents.view"
UPDATE = "agents.update"
APPROVE = "agents.approve"
# the setting of what a contact waiting for a decision is told, as 0.2's
REPLY = "review.reply"
# how many hits a page of search results holds, as the node's own page
HITS = 20
# the messages read when a conversation opens, and around one met by search;
# scrolled further, the half of a window around the last one shown
SHOWN = 50
FURTHER = 25
# what the list keeps across pages: the agent chosen, the tab, and a search
LISTED = ("agent", "tab", "q", "in", "from", "when", "sort")
_REMINDERS = TypeAdapter(tuple[Reminder, ...])


def size(count: int) -> str:
    """An attachment's size: in bytes, whole kilobytes, or megabytes to a
    tenth."""
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{round(count / 1024)} KB"
    return f"{count / 1024 / 1024:.1f} MB"


@pass_context
def minute(context: Context, at_ms: int) -> str:
    """The time of day of a moment on the viewer's clock, to the minute."""
    return datetime.fromtimestamp(at_ms / 1000, context["tz"]).strftime("%H:%M")


@pass_context
def day(context: Context, at_ms: int) -> tuple[str, str]:
    """The day a moment falls on, on the viewer's calendar, and its words:
    today and yesterday by name, any other day by its date."""
    moment = datetime.fromtimestamp(at_ms / 1000, context["tz"])
    today = datetime.now(context["tz"]).date()
    days = (moment.date() - today).days
    t = context["t"]
    if days in (0, -1):
        return moment.date().isoformat(), t.text(
            "server.today" if days == 0 else "server.yesterday"
        )
    return moment.date().isoformat(), t.text(
        "server.date" if moment.year == today.year else "server.date.year",
        {"year": moment.year, "month": moment.month, "day": moment.day},
    )


def repeat(rule: str) -> dict[str, Any]:
    """A reminder's repeat rule, in the parts its words are made of."""
    kind, _, rest = rule.partition(":")
    if kind == "every":
        return {"kind": "every." + rest[-1], "n": int(rest[:-1])}
    if rule.startswith("daily@"):
        return {"kind": "daily", "time": rule.removeprefix("daily@")}
    days, _, at = rest.partition("@")
    return {"kind": "weekly", "days": days.split(","), "time": at}


def failed(agent: Agent) -> bool:
    """Whether the agent failed to start, as its computer, which is here,
    last said."""
    return (
        agent.online
        and agent.activity is not None
        and agent.activity.state in ("failed", "blocked")
    )


@dataclass(frozen=True, slots=True)
class Row:
    """A conversation in a list: the agent it is with, and how it is."""

    agent: Agent
    conversation: Conversation


@dataclass(frozen=True, slots=True)
class Opened:
    """One conversation as the pages show it: its agent and what it is
    about, its messages or the first of them while it waits, the reminders
    in it, the receipts of the agent's replies, the turns it is having, and
    what the account may do about it."""

    agent: Agent
    detail: Detail
    history: History
    reminders: tuple[Reminder, ...]
    receipts: Mapping[str, Receipt]
    working: Sequence[Turn]
    reply: str | None
    approve: bool
    update: bool
    around: str | None


class Views:
    def __init__(
        self,
        server: Server,
        pages: Pages,
        access: Access,
        agents: Agents,
        computers: Computers,
        activity: Activity,
        reader: Reader,
        images: Images,
        service: Service,
    ) -> None:
        self._server = server
        self._pages = pages
        self._access = access
        self._agents = agents
        self._computers = computers
        self._activity = activity
        self._reader = reader
        self._images = images
        self._service = service
        pages.environment.filters |= {
            "size": size,
            "minute": minute,
            "day": day,
            "repeat": repeat,
        }
        pages.environment.globals |= {
            "proxied": images.address,
            "signature": images.sign,
        }
        self.routes = [
            Route("/", self._chats),
            Route("/rows", self._rows),
            Route("/hits", self._hits),
            Route("/images", images.fetch),
            Route("/files/{agent}/{attachment}", self._file),
            Route("/attention", self._attention),
            Route("/agent/{agent}", self._agent),
            Route("/agent/{agent}/rows", self._agent_rows),
            Route("/reminders/{agent}", self._reminders),
            Route("/reply/{agent}", self._replied, methods=["POST"]),
            Route("/{agent}/{thread}", self._opened),
            Route("/{agent}/{thread}/panes", self._panes),
            Route("/{agent}/{thread}/info", self._info),
            Route("/{agent}/{thread}/messages", self._messages),
            Route("/{agent}/{thread}/revoke", self._revoking),
            Route("/{agent}/{thread}/review", self._review, methods=["POST"]),
        ]

    async def _seen(self, request: Request) -> list[Agent]:
        """The agents the account may see, their refs loaded for the page."""
        account: Account = request.state.account
        if not account.allows(VIEW):
            raise HTTPException(status_code=403)
        agents = list(await self._agents.agents(account))
        await self._server.refs.load([agent.id for agent in agents])
        return agents

    async def _named(self, *shorts: str) -> list[str] | None:
        return await self._server.refs.values(list(shorts))

    async def _within(self, request: Request) -> tuple[str, str] | None:
        """The conversation a search is kept to, as its agent's ref and its
        own joined by a dot."""
        shorts = request.query_params.get("in", "").split(".")
        found = await self._named(*shorts) if len(shorts) == 2 else None
        return None if found is None else (found[0], found[1])

    async def _scoped(self, request: Request) -> tuple[list[Agent], list[Agent]]:
        """Every agent the account may see, and those the filter keeps."""
        agents = await self._seen(request)
        chosen = request.query_params.get("agent", "")
        found = await self._named(chosen) if chosen else None
        return agents, [
            agent for agent in agents if found is None or agent.id == found[0]
        ]

    async def _one(self, request: Request) -> tuple[Agent, str]:
        """The agent and conversation a page is about, for an account that
        may see the agent; any other is not there."""
        account: Account = request.state.account
        if not account.allows(VIEW):
            raise HTTPException(status_code=403)
        found = await self._named(
            request.path_params["agent"], request.path_params["thread"]
        )
        agent = None if found is None else await self._agents.agent(account, found[0])
        if found is None or agent is None:
            raise HTTPException(status_code=404)
        return agent, found[1]

    async def _agent_of(self, request: Request) -> Agent:
        account: Account = request.state.account
        if not account.allows(VIEW):
            raise HTTPException(status_code=403)
        found = await self._named(request.path_params["agent"])
        agent = None if found is None else await self._agents.agent(account, found[0])
        if agent is None:
            raise HTTPException(status_code=404)
        return agent

    async def _can(self, account: Account, agent: Agent, point: str) -> bool:
        return account.allows(point) and await self._access.can(
            account, "agent", agent.id, point
        )

    def _kept(self, request: Request, *without: str) -> str:
        """The list's query as the page was asked, for the links it hands on."""
        return urlencode(
            [
                (key, value)
                for key, value in request.query_params.multi_items()
                if key in LISTED and key not in without and (value or key == "q")
            ]
        )

    async def _merged(
        self,
        agents: Sequence[Agent],
        review: Literal["approved", "pending"],
        positions: Mapping[str, int] | None,
    ) -> tuple[list[Row], dict[str, int], list[Away]]:
        """A page of the agents' conversations newest first, read from each
        where it was left; how far each is read after it, and why some are
        not there."""
        asked = [
            agent for agent in agents if positions is None or agent.id in positions
        ]

        async def read(agent: Agent) -> Page:
            return Page.model_validate(
                await self._reader.call(
                    agent,
                    "conversations",
                    review,
                    (positions or {}).get(agent.id, 0),
                    PAGE,
                )
            )

        pages, away = await self._reader.each(asked, read)
        by_id = {agent.id: agent for agent in asked}
        rows = sorted(
            (
                Row(by_id[agent_id], conversation)
                for agent_id, page in pages.items()
                for conversation in page.conversations
            ),
            key=lambda row: (
                -row.conversation.last_activity_at_ms,
                row.conversation.thread_id,
            ),
        )[:PAGE]
        taken: dict[str, int] = {}
        for row in rows:
            taken[row.agent.id] = taken.get(row.agent.id, 0) + 1
        after = {
            agent_id: (positions or {}).get(agent_id, 0) + taken.get(agent_id, 0)
            for agent_id, page in pages.items()
            if taken.get(agent_id, 0) < len(page.conversations) or page.has_more
        }
        await self._server.refs.load([row.conversation.thread_id for row in rows])
        return rows, after, away

    async def _searched(
        self,
        request: Request,
        agents: Sequence[Agent],
        positions: Mapping[str, int] | None,
    ) -> tuple[list[tuple[Agent, Hit]], dict[str, int], list[Away], int]:
        """A page of what the search finds through the agents' messages, the
        most relevant or the newest first, read from each where it was left;
        how far each is read after it, why some are not there, and how many
        were found in all."""
        query = request.query_params
        if not query.get("q", "").strip():
            return [], {}, [], 0
        sort = "time" if query.get("sort") == "time" else "relevance"
        within = await self._within(request)
        if within is not None:
            agents = [agent for agent in agents if agent.id == within[0]]
        asked = [
            agent for agent in agents if positions is None or agent.id in positions
        ]
        after_ms = await self._since(request)

        async def read(agent: Agent) -> Found:
            return Found.model_validate(
                await self._reader.call(
                    agent,
                    "search",
                    query.get("q", ""),
                    sort,
                    thread_id=None if within is None else within[1],
                    sender=query.get("from") or None,
                    after_ms=after_ms,
                    offset=(positions or {}).get(agent.id, 0),
                    limit=HITS,
                )
            )

        found, away = await self._reader.each(asked, read)
        by_id = {agent.id: agent for agent in asked}
        hits = sorted(
            (
                (by_id[agent_id], hit)
                for agent_id, page in found.items()
                for hit in page.messages
            ),
            key=lambda pair: (
                (-pair[1].at_ms,)
                if sort == "time"
                else (-pair[1].score, -pair[1].at_ms)
            ),
        )[:HITS]
        taken: dict[str, int] = {}
        for agent, _ in hits:
            taken[agent.id] = taken.get(agent.id, 0) + 1
        after = {
            agent_id: (positions or {}).get(agent_id, 0) + taken.get(agent_id, 0)
            for agent_id, page in found.items()
            if taken.get(agent_id, 0) < len(page.messages) or page.has_more
        }
        await self._server.refs.load([hit.thread_id for _, hit in hits])
        return hits, after, away, sum(page.total for page in found.values())

    async def _since(self, request: Request) -> int | None:
        """When the search's time filter starts: today on the viewer's clock,
        the last seven days or thirty, from that day's midnight."""
        when = request.query_params.get("when")
        back = {"today": 0, "week": 6, "month": 29}.get(when or "")
        if back is None:
            return None
        start = datetime.now(await self._pages.zone(request)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=back)
        return int(start.timestamp() * 1000)

    async def _read_positions(
        self, request: Request, agents: Sequence[Agent]
    ) -> dict[str, int]:
        """How far each agent was read, as the link to the next page says it:
        each agent's ref and how many were read; one left out has no more."""
        named = {self._server.refs.ref(agent.id): agent for agent in agents}
        positions: dict[str, int] = {}
        for item in request.query_params.getlist("o"):
            short, _, offset = item.partition(".")
            if short.isdecimal() and offset.isdecimal() and int(short) in named:
                positions[named[int(short)].id] = int(offset)
        return positions

    def _onward(self, after: Mapping[str, int]) -> str:
        return urlencode(
            [
                ("o", f"{self._server.refs.ref(agent_id)}.{n}")
                for agent_id, n in after.items()
            ]
        )

    async def _computer_names(self, agents: Sequence[Agent]) -> dict[str, str]:
        return {
            computer.id: computer.name
            for computer in await self._computers.computers(
                sorted({agent.computer_id for agent in agents})
            )
        }

    async def _list(
        self, request: Request, agents: list[Agent], scoped: list[Agent]
    ) -> dict[str, Any]:
        """What the list's pane shows: the tab's conversations, or what the
        search finds, with why some agents' are not there."""
        account: Account = request.state.account
        query = request.query_params
        tab = "contacts" if query.get("tab") == "contacts" else "chats"
        counts = await self._reader.counts(account, scoped, wait=True)
        shown: dict[str, Any] = {
            "tab": tab,
            "agents": agents,
            "scoped": scoped,
            "chosen": query.get("agent", ""),
            "kept": self._kept(request),
            "total": sum(counted.total for counted in counts.values()),
            "pending": sum(counted.pending.total for counted in counts.values()),
            "query": query.get("q", ""),
        }
        if "q" in query:
            hits, after, away, found = await self._searched(request, scoped, None)
            chosen, members = await self._chosen(scoped, await self._within(request))
            threads = await self._threads(scoped)
            if chosen is not None and all(
                row.conversation.thread_id != chosen.conversation.thread_id
                for row in threads
            ):
                # one kept to though older than the first page lists
                threads = [chosen, *threads]
            shown |= {
                "hits": hits,
                "found": found,
                "onward": self._onward(after) if after else "",
                "away": away,
                "threads": threads,
                "within": query.get("in", ""),
                "members": members,
                "sort": query.get("sort", "relevance"),
                "when": query.get("when", ""),
                "sender": query.get("from", ""),
            }
        else:
            rows, after, away = await self._merged(
                scoped, "pending" if tab == "contacts" else "approved", None
            )
            shown |= {
                "rows": rows,
                "onward": self._onward(after) if after else "",
                "away": away,
            }
        shown["computers"] = await self._computer_names(
            [missing.agent for missing in shown["away"]]
        )
        return shown

    async def _threads(self, agents: Sequence[Agent]) -> list[Row]:
        """The conversations the search can be kept to: the latest of the
        agents in scope, as their first pages list them."""
        rows, _, _ = await self._merged(agents, "approved", None)
        return rows

    async def _chosen(
        self, agents: Sequence[Agent], within: tuple[str, str] | None
    ) -> tuple[Row | None, list[tuple[str, str]]]:
        """The conversation the search is kept to, and who in it the search
        can be kept to: the agent itself, and the others who spoke in it,
        each by what the search takes and the name it shows."""
        agent = (
            None
            if within is None
            else next((agent for agent in agents if agent.id == within[0]), None)
        )
        if within is None or agent is None or self._reader.away(agent) is not None:
            return None, [("self", "")]
        try:
            detail = Detail.model_validate(
                await self._reader.call(agent, "conversation", within[1])
            )
        except RemoteError, TimeoutError:
            return None, [("self", agent.name)]
        await self._server.refs.load([detail.conversation.thread_id])
        return Row(agent, detail.conversation), [("self", agent.name)] + [
            (
                member.sender.id,
                member.sender.display_name or member.sender.name or member.sender.id,
            )
            for member in detail.members
            if not member.is_self and member.sender is not None and member.sender.id
        ]

    async def _open(
        self, request: Request, agent: Agent, thread_id: str, around: str | None
    ) -> Opened | None:
        """The conversation as the pages show it; nothing while its agent's
        conversations cannot be read."""
        account: Account = request.state.account
        if self._reader.away(agent) is not None:
            return None
        try:
            detail = Detail.model_validate(
                await self._reader.call(agent, "conversation", thread_id)
            )
            waiting = detail.conversation.review == "pending"
            if waiting:
                # the first message whole, as 0.2 read it
                history = History.model_validate(
                    await self._reader.call(agent, "history", thread_id, limit=1)
                )
            else:
                anchor = around or (
                    detail.conversation.latest.message_id
                    if detail.conversation.latest
                    else None
                )
                history = History.model_validate(
                    await self._reader.call(
                        agent, "history", thread_id, around=anchor, limit=SHOWN
                    )
                )
            reminders = (
                ()
                if waiting
                else _REMINDERS.validate_python(
                    await self._reader.call(agent, "reminders", thread_id)
                )
            )
            reply = (
                await self._computers.call(
                    agent.computer_id, "collaboration", "setting", agent.id, REPLY
                )
                if waiting
                else None
            )
        except RemoteError, TimeoutError:
            return None
        await self._server.refs.load([thread_id])
        return Opened(
            agent=agent,
            detail=detail,
            history=history,
            reminders=reminders,
            receipts=await self._receipts(account, agent, history.messages),
            working=await self._activity.working(
                account, agent_id=agent.id, thread_id=thread_id
            ),
            reply=reply if isinstance(reply, str) else None,
            approve=await self._can(account, agent, APPROVE),
            update=await self._can(account, agent, UPDATE),
            around=around,
        )

    async def _receipts(
        self, account: Account, agent: Agent, messages: Sequence[Entry]
    ) -> Mapping[str, Receipt]:
        return await self._activity.receipts(
            account,
            agent.id,
            [
                message.message_id
                for message in messages
                if message.direction == "outbound"
            ],
        )

    async def _page(
        self,
        request: Request,
        layer: Literal["list", "thread", "info"],
        opened: Opened | None = None,
        thread_seen: Mapping[str, object] | None = None,
    ) -> Response:
        """The conversations page: the list, and the conversation opened
        beside it; a phone shows the layer asked for alone. What the page
        follows is taken before it is read, so nothing that comes between
        goes unseen."""
        seen = await self.listed(
            request.state.account, {"agent": request.query_params.get("agent", "")}
        )
        agents, scoped = await self._scoped(request)
        listed = await self._list(request, agents, scoped)
        return await self._pages.render(
            request,
            "chats/page.html",
            layer=layer,
            opened=opened,
            now_ms=time_ns() // 1_000_000,
            seen=seen,
            thread_seen=thread_seen if opened is not None else None,
            **listed,
        )

    async def _chats(self, request: Request) -> Response:
        """The conversations of every agent, or one, together; a wide screen
        opens the newest beside them."""
        return await self._page(request, "list")

    async def _opened(self, request: Request) -> Response:
        agent, thread_id = await self._one(request)
        seen = await self._thread_state(request.state.account, agent, thread_id)
        opened = await self._open(
            request, agent, thread_id, request.query_params.get("around")
        )
        return await self._page(request, "thread", opened, seen)

    async def _info(self, request: Request) -> Response:
        """A phone's page of what a conversation is about."""
        agent, thread_id = await self._one(request)
        seen = await self._thread_state(request.state.account, agent, thread_id)
        opened = await self._open(request, agent, thread_id, None)
        return await self._page(request, "info", opened, seen)

    async def _panes(self, request: Request) -> Response:
        """A conversation's messages and what it is about, for the page
        that opens it beside its list."""
        agent, thread_id = await self._one(request)
        seen = await self._thread_state(request.state.account, agent, thread_id)
        opened = await self._open(
            request, agent, thread_id, request.query_params.get("around")
        )
        return await self._pages.render(
            request,
            "chats/opened.html",
            opened=opened,
            agent=agent,
            kept=self._kept(request),
            now_ms=time_ns() // 1_000_000,
            thread_seen=seen if opened is not None else None,
        )

    async def _messages(self, request: Request) -> Response:
        """The messages before the first shown or after the last, half a
        window of them; or, when what the conversation follows changed, the
        newer ones and the turns it is having."""
        agent, thread_id = await self._one(request)
        account: Account = request.state.account
        query = request.query_params
        anchor = query.get("before") or query.get("after") or ""
        older = "before" in query
        try:
            window = History.model_validate(
                await self._reader.call(
                    agent,
                    "history",
                    thread_id,
                    around=anchor,
                    limit=2 * FURTHER + 1,
                )
            )
        except RemoteError, TimeoutError:
            raise HTTPException(status_code=404) from None
        seq = next(
            (
                message.seq
                for message in window.messages
                if message.message_id == anchor
            ),
            None,
        )
        if seq is None:
            raise HTTPException(status_code=404)
        messages = [
            message
            for message in window.messages
            if (message.seq < seq if older else message.seq > seq)
        ]
        return await self._pages.render(
            request,
            "chats/more.html",
            agent=agent,
            thread_id=thread_id,
            messages=messages,
            older=older,
            more=window.has_before if older else window.has_after,
            receipts=await self._receipts(account, agent, messages),
            working=[]
            if older
            else await self._activity.working(
                account, agent_id=agent.id, thread_id=thread_id
            ),
            now_ms=time_ns() // 1_000_000,
        )

    async def _rows(self, request: Request) -> Response:
        """The next page of a list, from where each agent was left."""
        _, scoped = await self._scoped(request)
        positions = await self._read_positions(request, scoped)
        review = (
            "pending" if request.query_params.get("tab") == "contacts" else "approved"
        )
        rows, after, _ = await self._merged(scoped, review, positions)
        return await self._pages.render(
            request,
            "chats/rows.html",
            rows=rows,
            tab="contacts" if review == "pending" else "chats",
            kept=self._kept(request),
            onward=self._onward(after) if after else "",
        )

    async def _hits(self, request: Request) -> Response:
        """The next page of what the search found."""
        _, scoped = await self._scoped(request)
        positions = await self._read_positions(request, scoped)
        hits, after, _, _ = await self._searched(request, scoped, positions)
        return await self._pages.render(
            request,
            "chats/hits.html",
            hits=hits,
            kept=self._kept(request),
            onward=self._onward(after) if after else "",
        )

    async def _file(self, request: Request) -> Response:
        """A picture that came in, for the page that signed its address and
        an account that may see its agent."""
        found = await self._named(request.path_params["agent"])
        attachment_id = request.path_params["attachment"]
        if found is None or not self._images.signed(
            found[0], attachment_id, request.query_params.get("s", "")
        ):
            raise HTTPException(status_code=403)
        account: Account = request.state.account
        agent = (
            await self._agents.agent(account, found[0])
            if account.allows(VIEW)
            else None
        )
        if agent is None or self._reader.away(agent) is not None:
            raise HTTPException(status_code=404)
        return await self._images.stream(agent, attachment_id)

    async def _review(self, request: Request) -> Response:
        """Let a conversation in, turn it away, or stop replying in it, as
        an account that may decide for its agent. On the conversations page,
        one let in opens among the others and the list shows after one
        turned away; elsewhere, the parts showing it are told."""
        agent, thread_id = await self._one(request)
        account: Account = request.state.account
        if not await self._can(account, agent, APPROVE):
            raise HTTPException(status_code=404)
        form = await request.form()
        decision = form.get("decision")
        if decision not in ("approved", "denied"):
            raise HTTPException(status_code=422)
        try:
            # the node reviews by conversation alone: this one must be the agent's
            await self._reader.call(agent, "conversation", thread_id)
            await self._computers.call(
                agent.computer_id,
                "collaboration",
                "review_contact",
                thread_id,
                decision,
            )
        except (RemoteError, TimeoutError) as error:
            return await self._pages.render(
                request,
                "chats/failed.html",
                error=error.message if isinstance(error, RemoteError) else None,
            )
        self._reader.forget(agent.id)
        if form.get("on") != "page":
            return Response(status_code=204, headers={"HX-Trigger": "chats:reviewed"})
        if decision == "approved":
            kept = self._kept(request, "tab")
            onward = (
                f"/ui/chats/{request.path_params['agent']}/"
                f"{request.path_params['thread']}" + (f"?{kept}" if kept else "")
            )
        else:
            kept = self._kept(request)
            onward = "/ui/chats/" + (f"?{kept}" if kept else "")
        return Response(status_code=204, headers={"HX-Redirect": onward})

    async def _revoking(self, request: Request) -> Response:
        """The question before the agent stops replying in a conversation."""
        agent, thread_id = await self._one(request)
        account: Account = request.state.account
        if not await self._can(account, agent, APPROVE):
            raise HTTPException(status_code=404)
        try:
            detail = Detail.model_validate(
                await self._reader.call(agent, "conversation", thread_id)
            )
        except RemoteError, TimeoutError:
            raise HTTPException(status_code=404) from None
        return await self._pages.render(
            request,
            "chats/revoke.html",
            agent=agent,
            detail=detail,
            kept=self._kept(request),
        )

    async def _reply_card(
        self, request: Request, agent: Agent, named: bool, failed: bool = False
    ) -> Response:
        try:
            reply = await self._computers.call(
                agent.computer_id, "collaboration", "setting", agent.id, REPLY
            )
        except RemoteError, TimeoutError:
            reply = None
        return await self._pages.render(
            request,
            "chats/reply.html",
            agent=agent,
            reply=reply if isinstance(reply, str) else None,
            update=await self._can(request.state.account, agent, UPDATE),
            named=named,
            failed=failed,
        )

    async def _replied(self, request: Request) -> Response:
        """What the agent says to contacts waiting, changed by an account
        that may change the agent."""
        agent = await self._agent_of(request)
        if not await self._can(request.state.account, agent, UPDATE):
            raise HTTPException(status_code=404)
        form = await request.form()
        named = form.get("named") == "1"
        text = str(form.get("text") or "").strip()
        try:
            await self._computers.call(
                agent.computer_id,
                "collaboration",
                "set_setting",
                agent.id,
                REPLY,
                text,
            )
        except RemoteError, TimeoutError:
            return await self._reply_card(request, agent, named, failed=True)
        return await self._reply_card(request, agent, named)

    async def _agent_lists(
        self, request: Request, agent: Agent, review: Literal["approved", "pending"]
    ) -> tuple[list[Row], dict[str, int], list[Away]]:
        """A page of the agent's own conversations, from where it was left."""
        return await self._merged(
            [agent],
            review,
            await self._read_positions(request, [agent])
            if "o" in request.query_params
            else None,
        )

    async def _agent(self, request: Request) -> Response:
        """An agent's own conversations on its page, those waiting for a
        decision beside them with what it says to them meanwhile."""
        agent = await self._agent_of(request)
        account: Account = request.state.account
        seen = await self.listed(
            account, {"agent": str(self._server.refs.ref(agent.id))}
        )
        tab = "contacts" if request.query_params.get("tab") == "contacts" else "chats"
        counts = await self._reader.counts(account, [agent], wait=True)
        counted = counts.get(agent.id)
        rows, after, away = await self._agent_lists(
            request, agent, "pending" if tab == "contacts" else "approved"
        )
        try:
            reply = (
                await self._computers.call(
                    agent.computer_id, "collaboration", "setting", agent.id, REPLY
                )
                if agent.online
                else None
            )
        except RemoteError, TimeoutError:
            reply = None
        pending = list(counted.pending.conversations) if counted else []
        await self._server.refs.load(
            [conversation.thread_id for conversation in pending]
        )
        return await self._pages.render(
            request,
            "chats/agent.html",
            agent=agent,
            tab=tab,
            rows=rows,
            onward=self._onward(after) if after else "",
            away=away,
            computers=await self._computer_names([agent]),
            total=counted.total if counted else 0,
            pending=pending,
            waiting=counted.pending.total if counted else 0,
            reply=reply if isinstance(reply, str) else None,
            approve=await self._can(account, agent, APPROVE),
            update=await self._can(account, agent, UPDATE),
            seen=seen,
        )

    async def _agent_rows(self, request: Request) -> Response:
        agent = await self._agent_of(request)
        tab = "contacts" if request.query_params.get("tab") == "contacts" else "chats"
        rows, after, _ = await self._agent_lists(
            request, agent, "pending" if tab == "contacts" else "approved"
        )
        return await self._pages.render(
            request,
            "chats/agent_rows.html",
            agent=agent,
            tab=tab,
            rows=rows,
            onward=self._onward(after) if after else "",
        )

    async def _reminders(self, request: Request) -> Response:
        """The activity tab's section of the agent's reminders still to
        fire, each with the conversation it is in."""
        agent = await self._agent_of(request)
        seen = await self.listed(
            request.state.account, {"agent": request.path_params["agent"]}
        )
        reminders: tuple[Reminder, ...] = ()
        if self._reader.away(agent) is None:
            try:
                reminders = _REMINDERS.validate_python(
                    await self._reader.call(agent, "reminders")
                )
            except RemoteError, TimeoutError:
                pass
        return await self._pages.render(
            request,
            "chats/reminders.html",
            agent=agent,
            reminders=reminders,
            seen=seen,
        )

    async def _attention(self, request: Request) -> Response:
        """What waits for the account on the overview: first contacts of the
        agents it sees, newest first, and agents that failed to start."""
        account: Account = request.state.account
        seen = await self.listed(account, {"agent": ""})
        agents = await self._seen(request)
        counts = await self._reader.counts(account, agents, wait=True)
        by_id = {agent.id: agent for agent in agents}
        waiting = sorted(
            (
                Row(by_id[agent_id], conversation)
                for agent_id, counted in counts.items()
                for conversation in counted.pending.conversations
            ),
            key=lambda row: -row.conversation.last_activity_at_ms,
        )
        await self._server.refs.load([row.conversation.thread_id for row in waiting])
        stopped = [agent for agent in agents if failed(agent)]
        approvable = await self._access.targets(account, "agent", APPROVE)
        return await self._pages.render(
            request,
            "chats/attention.html",
            waiting=waiting,
            failed=stopped,
            computers=await self._computer_names(stopped),
            approve={
                agent.id
                for agent in agents
                if account.allows(APPROVE)
                and (approvable is None or agent.id in approvable)
            },
            seen=seen,
            now_ms=time_ns() // 1_000_000,
        )

    async def _thread_state(
        self, account: Account, agent: Agent, thread_id: str
    ) -> Mapping[str, object] | None:
        return await self.followed(
            account,
            {
                "agent": str(self._server.refs.ref(agent.id)),
                "thread": str(self._server.refs.ref(thread_id)),
            },
        )

    async def counted(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """The sidebar badge: how many conversations wait for a decision."""
        del scope
        waiting = await self._service.pending(account)
        return {"text": str(waiting) if waiting else "", "kind": "unread"}

    async def listed(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """What a list follows: the newest message event of the agents it
        shows, which those are, which of their computers are away, which
        failed to start, and where the conversations are being enabled or
        failed to be."""
        agents = list(await self._agents.agents(account))
        if scope["agent"]:
            found = await self._named(scope["agent"])
            if found is None:
                return None
            agents = [agent for agent in agents if agent.id == found[0]]
            if not agents:
                return None
        counts = await self._reader.counts(account, agents)
        return {
            "cursor": await self._activity.cursor(
                account,
                agent_id=agents[0].id if scope["agent"] else None,
                messages=True,
            ),
            "agents": sorted(agent.id for agent in agents),
            "away": sorted({agent.computer_id for agent in agents if not agent.online}),
            "unstarted": sorted(agent.id for agent in agents if failed(agent)),
            "counts": {
                agent_id: [counted.total, counted.pending.total]
                for agent_id, counted in counts.items()
            },
            **self._reader.installs(),
        }

    async def followed(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """What an open conversation follows: its newest message event and
        the turns it is having."""
        found = await self._named(scope["agent"], scope["thread"])
        agent = None if found is None else await self._agents.agent(account, found[0])
        if found is None or agent is None:
            return None
        return {
            "cursor": await self._activity.cursor(
                account, agent_id=agent.id, thread_id=found[1], messages=True
            ),
            "running": [
                turn.turn_id
                for turn in await self._activity.working(
                    account, agent_id=agent.id, thread_id=found[1]
                )
            ],
        }

    async def tallied(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """The agent's tab count: how many conversations it has let in."""
        found = await self._named(scope["agent"])
        agent = None if found is None else await self._agents.agent(account, found[0])
        if agent is None:
            return None
        counted = (await self._reader.counts(account, [agent])).get(agent.id)
        return {"text": str(counted.total) if counted else "", "kind": "count"}
