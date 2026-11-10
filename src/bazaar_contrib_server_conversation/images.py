"""Pictures in conversations, brought to the page by the server: those that
came in with a message, read from the agent's computer a segment at a time,
and those written in Markdown, fetched from the web on the page's behalf, so
whoever put one in a chat sees the server come for it, never the person
looking."""

from __future__ import annotations

lazy import base64
lazy import socket
lazy from collections.abc import AsyncIterator
lazy from ipaddress import ip_address
lazy from urllib.parse import urlencode, urljoin, urlsplit

lazy from aiohttp import ClientError, ClientResponse, ClientSession, DefaultResolver
lazy from aiohttp.abc import AbstractResolver, ResolveResult
lazy from bazaar_contrib_server_access import Access
lazy from bazaar_contrib_server_agent import Agent
lazy from bazaar_contrib_sidecar import RemoteError
lazy from bazaar_contrib_timer import Timer, timeout
lazy from pydantic import ValidationError
lazy from starlette.requests import Request
lazy from starlette.responses import Response, StreamingResponse

lazy from .models import Segment
lazy from .reader import Reader

# a picture in a chat, not a download: what a phone would post
MAX_BYTES = 10 * 1024 * 1024
CHUNK = 64 * 1024
LIMIT_MS = 20_000
REDIRECTS = 3
REDIRECTED = (301, 302, 303, 307, 308)
# a picture by its address never changes; opened as a page of its own, one
# with scripts in it - an SVG - runs none, and is no part of this site
HEADERS = {
    "Cache-Control": "private, max-age=86400",
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
}


def public(host: str) -> bool:
    return ip_address(host).is_global


def on_the_web(url: str) -> bool:
    """Whether the address is one the server may go to: on the web, and not
    given as a private address in numbers, which no name would be asked for."""
    if not url.startswith(("http://", "https://")):
        return False
    host = urlsplit(url).hostname or ""
    try:
        ip_address(host)
    except ValueError:
        return True
    return public(host)


class Public(AbstractResolver):
    """Names resolve to public addresses only: a picture said to live on the
    server's own network is not fetched, whatever the name says."""

    def __init__(self) -> None:
        self._resolver = DefaultResolver()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        found = await self._resolver.resolve(host, port, family)
        reached = [item for item in found if public(item["host"])]
        if not reached:
            raise OSError(f"{host} is not on the public internet")
        return reached

    async def close(self) -> None:
        await self._resolver.close()


class Images:
    """The page holds a signed address for each picture, so only a picture
    the page itself showed can be asked for through the server."""

    def __init__(
        self, access: Access, timer: Timer, session: ClientSession, reader: Reader
    ) -> None:
        self._access = access
        self._timer = timer
        self._session = session
        self._reader = reader

    def address(self, url: str) -> str | None:
        """Where the page asks for a picture written in Markdown; nothing for
        one that is not on the web."""
        if not url.startswith(("http://", "https://")):
            return None
        return "/ui/chats/images?" + urlencode(
            {"u": url, "s": self._access.sign("image", url)}
        )

    def sign(self, agent_id: str, attachment_id: str) -> str:
        """The signature of a picture that came in with a message, which the
        page's address for it carries."""
        return self._access.sign("attachment", f"{agent_id}/{attachment_id}")

    def signed(self, agent_id: str, attachment_id: str, signature: str) -> bool:
        return self._access.verify(
            "attachment", f"{agent_id}/{attachment_id}", signature
        )

    async def _open(self, url: str) -> ClientResponse | None:
        """The answer at the address, redirects followed a few times, each
        address on the way looked at: one given as a number goes to the
        network without a name to resolve."""
        for _ in range(REDIRECTS + 1):
            if not on_the_web(url):
                return None
            upstream = await self._session.get(
                url, headers={"User-Agent": "bcs"}, allow_redirects=False
            )
            onward = upstream.headers.get("Location")
            if upstream.status not in REDIRECTED or onward is None:
                return upstream
            upstream.release()
            url = urljoin(url, onward)
        return None

    async def fetch(self, request: Request) -> Response:
        url = request.query_params.get("u", "")
        if not self._access.verify("image", url, request.query_params.get("s", "")):
            return Response(status_code=403)
        deadline = self._timer.now_ms + LIMIT_MS
        try:
            async with timeout(self._timer, LIMIT_MS):
                upstream = await self._open(url)
        except ClientError, OSError, TimeoutError:
            return Response(status_code=502)
        if upstream is None:
            return Response(status_code=502)
        if not (
            upstream.ok
            and upstream.content_type.startswith("image/")
            and (upstream.content_length or 0) <= MAX_BYTES
        ):
            upstream.release()
            return Response(status_code=502)

        async def body() -> AsyncIterator[bytes]:
            sent = 0
            try:
                while True:
                    try:
                        async with timeout(self._timer, deadline - self._timer.now_ms):
                            chunk = await upstream.content.read(CHUNK)
                    except ClientError, TimeoutError:
                        return
                    sent += len(chunk)
                    if not chunk or sent > MAX_BYTES:
                        return
                    yield chunk
            finally:
                upstream.release()

        return StreamingResponse(
            body(), media_type=upstream.content_type, headers=HEADERS
        )

    async def stream(self, agent: Agent, attachment_id: str) -> Response:
        """An attachment as its computer reads it, a segment a call, the
        first read before the answer starts so a failure is told."""
        try:
            first = Segment.model_validate(
                await self._reader.call(agent, "attachment", attachment_id, 0)
            )
        except RemoteError, TimeoutError, ValidationError:
            return Response(status_code=502)

        async def body() -> AsyncIterator[bytes]:
            segment = first
            sent = 0
            while True:
                data = base64.b64decode(segment.data)
                sent += len(data)
                yield data
                if not data or sent >= first.size_bytes:
                    return
                try:
                    segment = Segment.model_validate(
                        await self._reader.call(
                            agent, "attachment", attachment_id, sent
                        )
                    )
                except RemoteError, TimeoutError, ValidationError:
                    return

        return StreamingResponse(
            body(),
            media_type=first.media_type or "application/octet-stream",
            headers=HEADERS | {"Content-Length": str(first.size_bytes)},
        )
