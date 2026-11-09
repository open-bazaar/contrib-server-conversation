"""The conversations of the Bazaar Compute server: every agent's
conversations, read from its computer as they are and never kept here, the
first contacts let in or turned away and what an agent says to them
meanwhile, searching all of them, the pictures in them, and CONVERSATIONS
for the modules counting those waiting. A computer without the node's side
of it has bazaar-contrib-starlink-conversation installed by its manager."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy from collections.abc import AsyncIterator
lazy from importlib.resources import files
lazy from pathlib import Path

from bazaar_contrib_server import SERVER, PermissionItem, SidebarItem, Topic
from bazaar_contrib_server_access import ACCESS
from bazaar_contrib_server_activity import ACTIVITY
from bazaar_contrib_server_agent import AGENTS, AgentSection, AgentTab
from bazaar_contrib_server_computer import COMPUTERS
from bazaar_contrib_server_overview import OVERVIEW, OverviewItem
from bazaar_contrib_timer import TIMER
from bazaar_core import Context, Contrib
lazy from aiohttp import ClientSession, TCPConnector

from .contracts import CONVERSATIONS, Conversations
lazy from .images import Images, Public
lazy from .pages import APPROVE, VIEW, Views
lazy from .reader import Reader
lazy from .service import Service

__all__ = ["CONVERSATIONS", "Conversations", "conversation"]


@asynccontextmanager
async def _conversation(ctx: Context) -> AsyncIterator[None]:
    server = ctx.accept(SERVER)
    access = ctx.accept(ACCESS)
    agents = ctx.accept(AGENTS)
    computers = ctx.accept(COMPUTERS)
    activity = ctx.accept(ACTIVITY)
    timer = ctx.accept(TIMER)
    resources = Path(str(files(__name__))) / "resources"
    icon = await asyncio.to_thread(
        (resources / "templates" / "chats" / "icons" / "messages-square.svg").read_text,
        encoding="utf-8",
    )
    async with (
        asyncio.TaskGroup() as tasks,
        ClientSession(connector=TCPConnector(resolver=Public())) as session,
    ):
        reader = Reader(computers, activity, timer, tasks)
        service = Service(agents, reader)
        pages = await server.pages("chats", __name__)
        views = Views(
            server,
            pages,
            access,
            agents,
            computers,
            activity,
            reader,
            Images(access, timer, session, reader),
            service,
        )
        async with (
            server.module(
                "chats",
                ui=pages.application(views.routes),
                static=resources / "static",
                locales=resources / "locales",
            ),
            server.permission(
                PermissionItem(point=APPROVE, description="chats.permission.approve")
            ),
            # who may let an agent's contacts in is shared with the agent
            access.points("agent", (APPROVE,)),
            server.sidebar(
                SidebarItem(
                    text="chats.title",
                    icon=icon,
                    path="/chats",
                    group="server.sidebar.workbench",
                    permission=VIEW,
                    # between the agents and the computers
                    order=25,
                    badge="chats.count",
                )
            ),
            server.topic(
                Topic(name="chats.count", fetch=views.counted, permission=VIEW)
            ),
            server.topic(
                Topic(
                    name="chats.list",
                    fetch=views.listed,
                    fields=("agent",),
                    permission=VIEW,
                )
            ),
            server.topic(
                Topic(
                    name="chats.thread",
                    fetch=views.followed,
                    fields=("agent", "thread"),
                    permission=VIEW,
                )
            ),
            server.topic(
                Topic(
                    name="chats.agent",
                    fetch=views.tallied,
                    fields=("agent",),
                    permission=VIEW,
                )
            ),
            # after the activity
            agents.tab(
                AgentTab(
                    key="chats",
                    text="chats.title",
                    icon=icon,
                    src="/ui/chats/agent",
                    permission=VIEW,
                    order=20,
                    count="chats.agent",
                )
            ),
            agents.section(
                AgentSection(
                    key="reminders",
                    src="/ui/chats/reminders",
                    column="side",
                    permission=VIEW,
                    order=30,
                    narrow=False,
                )
            ),
            ctx.accept(OVERVIEW).register(
                OverviewItem(
                    key="attention",
                    title="chats.attention.title",
                    src="/ui/chats/attention",
                    permission=VIEW,
                    order=30,
                )
            ),
        ):
            ctx.offer(CONVERSATIONS, service)
            try:
                yield
            finally:
                reader.close()


conversation = Contrib(
    accepts=[SERVER, ACCESS, OVERVIEW, COMPUTERS, AGENTS, ACTIVITY, TIMER],
    offers=[CONVERSATIONS],
    lifespan=_conversation,
)
