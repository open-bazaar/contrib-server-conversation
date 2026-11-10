"""The server's entries as installed, server-conversation among them, with
the door as a way in for people other than the admin."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
lazy import asyncio
lazy import json
lazy import logging
lazy import re
lazy from collections.abc import AsyncIterator, Awaitable, Callable
lazy from importlib.resources import files
lazy from pathlib import Path
lazy from typing import Any

import httpx
from bazaar_contrib_http import HTTP
from bazaar_contrib_server import SERVER
from bazaar_contrib_server_access import ACCESS
from bazaar_contrib_server_computer import COMPUTERS, Computers
from bazaar_contrib_server_database import SERVER_DB
from bazaar_core import Context, Contrib, ContribEntry, ContribHub, ContribSpec, load
from door import Door
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

ENTRIES = (
    ("timer-wheel", {}),
    ("http", {"listen": "127.0.0.1:0"}),
    ("server-database", {}),
    ("server-migrations", {}),
    ("server", {}),
    ("server-theme-night", {}),
    ("server-theme-day", {}),
    ("server-access", {}),
    ("server-overview", {}),
    ("server-relay-memory", {}),
    ("server-computer", {}),
    ("server-agent", {}),
    ("server-activity", {}),
    ("server-conversation", {}),
)


@dataclass
class Served:
    hub: ContribHub
    url: str
    password: str
    door: Door
    computers: Computers


@asynccontextmanager
async def serving() -> AsyncIterator[Served]:
    found: dict[str, Any] = {}
    printed: list[str] = []

    class Printed(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            printed.append(record.getMessage())

    @asynccontextmanager
    async def look(ctx: Context) -> AsyncIterator[None]:
        found["url"] = ctx.accept(HTTP).url
        found["computers"] = ctx.accept(COMPUTERS)
        yield

    @asynccontextmanager
    async def enter(ctx: Context) -> AsyncIterator[None]:
        server = ctx.accept(SERVER)
        pages = await server.pages("door", "door")
        locales = (
            (await asyncio.to_thread(lambda: Path(str(files("door")))))
            / "resources"
            / "locales"
        )

        async def settings(request: Request) -> Response:
            return await pages.render(request, "door/settings.html")

        found["door"] = Door(ctx.accept(ACCESS), ctx.accept(SERVER_DB))
        async with (
            server.module(
                "door", ui=pages.application([Route("/", settings)]), locales=locales
            ),
            ctx.accept(ACCESS).method(found["door"]),
        ):
            yield

    composition = await asyncio.to_thread(
        load,
        [ContribSpec(contrib=name, config=config) for name, config in ENTRIES],
    )
    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="door",
                    definition=Contrib(
                        accepts=[SERVER, ACCESS, SERVER_DB], lifespan=enter
                    ),
                ),
                ContribEntry(
                    name="look",
                    definition=Contrib(accepts=[HTTP, COMPUTERS], lifespan=look),
                ),
            )
        }
    )
    logger = logging.getLogger("bazaar_contrib_server_access")
    handler = Printed()
    logger.addHandler(handler)
    try:
        async with ContribHub() as hub, hub.enable(composition):
            password = next(
                (
                    created.group(1)
                    for message in printed
                    if (created := re.search(r"with password (\S+);", message))
                ),
                "",
            )
            yield Served(hub, found["url"], password, found["door"], found["computers"])
    finally:
        logger.removeHandler(handler)


@asynccontextmanager
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # building the client loads certificates from disk, which is not the loop's work
    async with await asyncio.to_thread(httpx.AsyncClient, timeout=90) as session:
        yield session


async def admin(served: Served, session: httpx.AsyncClient) -> None:
    await session.post(
        f"{served.url}/login/", data={"name": "admin", "password": served.password}
    )


async def role(served: Served, session: httpx.AsyncClient, *points: str) -> str:
    """A new role holding the points; the address of its page."""
    made = await session.post(
        f"{served.url}/ui/access/roles/",
        data={"name": "holding-" + "-".join(points), "permissions": list(points)},
    )
    assert made.status_code == 303
    return made.headers["location"]


async def through(served: Served, session: httpx.AsyncClient, person: str) -> None:
    """Log in through the door as the person."""
    sent = await session.get(f"{served.url}/login/door/?person={person}")
    while (
        sent.status_code in (302, 303, 307)
        and "/login/door/" in sent.headers["location"]
    ):
        sent = await session.get(f"{served.url}{sent.headers['location']}")


async def enrol(
    served: Served, session: httpx.AsyncClient, name: str
) -> tuple[str, str]:
    """A computer the account connects; its ref and its token, as the page
    showing the commands gives them."""
    page = await session.post(f"{served.url}/ui/computers/", data={"name": name})
    assert page.status_code == 200, (page.status_code, page.text[:3000])
    token = re.search(r"--option token=([^\"&\s]+)\"", page.text)
    ref = re.search(r'hx-get="/ui/computers/(\d+)"', page.text)
    assert token is not None and ref is not None
    return ref.group(1), token.group(1)


def seen(page: str, topic: str) -> object:
    """What the page follows of the topic, as it was when drawn."""
    found = re.search(rf"data-topic=\"{topic}\"[^>]*data-seen='([^']*)'", page)
    assert found, page[:3000]
    return json.loads(found.group(1))


async def told(
    served: Served, session: httpx.AsyncClient, *followed: dict[str, object]
) -> set[str]:
    """Which of what a page follows the update bus says has changed since
    it was drawn."""
    answer = await session.post(
        f"{served.url}/api/server/poll", json={"subscriptions": list(followed)}
    )
    return {change["id"] for change in answer.json()["changes"]}


async def until[T](read: Callable[[], Awaitable[T]], done: Callable[[T], bool]) -> T:
    """What a read gives once the computer has caught up."""
    value = None
    try:
        async with asyncio.timeout(60):
            while True:
                value = await read()
                if done(value):
                    return value
                await asyncio.sleep(0.5)
    except TimeoutError:
        raise AssertionError(value) from None


def names(page: str) -> list[str]:
    """The conversations a list shows, in its order."""
    return re.findall(r'class="tn">([^<]*)<', page)


def opened(page: str, name: str) -> str:
    """The address of a conversation the list shows, as its agent's ref and
    its own."""
    for address, row in re.findall(
        r'<a class="conv" href="/ui/chats/(\d+/\d+)[^"]*"(.*?)</a>', page, re.DOTALL
    ):
        if f'class="tn">{name}<' in row:
            return address
    raise AssertionError(page[:3000])


async def page(served: Served, session: httpx.AsyncClient, path: str) -> str:
    answer = await session.get(f"{served.url}{path}")
    assert answer.status_code == 200, (answer.status_code, answer.text[:2000])
    return answer.text
