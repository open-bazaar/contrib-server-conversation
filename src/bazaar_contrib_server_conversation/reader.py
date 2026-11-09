"""What each agent's computer says about its conversations, through the
computer's starlink-conversation, installed there through its manager when
the computer lacks it. How many conversations each agent has and which wait
for a decision are kept in the process: asked again once the agent's message
events moved on or what was said is a minute old, one ask at a time for each
agent, what was said before kept when the computer does not answer."""

from __future__ import annotations

from dataclasses import dataclass
lazy import asyncio
lazy import logging
lazy from collections.abc import Awaitable, Callable, Mapping, Sequence
lazy from dataclasses import replace
lazy from time import time_ns
lazy from typing import Literal

from pydantic import ValidationError
lazy from bazaar_contrib_server import Account
lazy from bazaar_contrib_server_activity import Activity
lazy from bazaar_contrib_server_agent import Agent
lazy from bazaar_contrib_server_computer import Computers
lazy from bazaar_contrib_sidecar import RemoteError
lazy from bazaar_contrib_timer import Timer
lazy from pydantic import JsonValue

from .models import Operation, Page

# the node's side of the conversations, in the versions this package reads
PACKAGE = "bazaar-contrib-starlink-conversation"
VERSIONS = "~=0.3.0rc2"
ENTRY = "starlink-conversation"
# what was said, and an install that failed, hold for a minute
FRESH_MS = 60_000
# an install is asked how it goes as often as the update bus asks
POLL_MS = 1_000
# a page of conversations, as the node gives them
PAGE = 50


def now_ms() -> int:
    return time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class Counted:
    """An agent's conversations as its computer last said: how many are let
    in, and the first page of those waiting for a decision."""

    total: int
    pending: Page
    # the agent's newest message event when asked; no newer one of it came
    # after while the newest of any agent seen since is no newer than upto
    cursor: int
    upto: int
    at_ms: int


@dataclass(frozen=True, slots=True)
class Away:
    """Why an agent's conversations are not there for now: its computer is
    away or does not answer, the conversations are being enabled on it,
    enabling them failed, or reading them did, and how."""

    agent: Agent
    reason: Literal["offline", "enabling", "failed", "silent", "error"]
    error: str | None = None


class Reader:
    def __init__(
        self,
        computers: Computers,
        activity: Activity,
        timer: Timer,
        tasks: asyncio.TaskGroup,
    ) -> None:
        self._computers = computers
        self._activity = activity
        self._timer = timer
        self._tasks = tasks
        self._kept: dict[str, Counted] = {}
        self._asking: dict[str, asyncio.Task[None]] = {}
        self._installing: dict[str, asyncio.Task[None]] = {}
        # when an install failed on a computer, and why: nothing for no answer
        self._failed: dict[str, tuple[int, str | None]] = {}

    def away(self, agent: Agent) -> Away | None:
        """Why the agent's conversations cannot be read now, if so."""
        if not agent.online:
            return Away(agent, "offline")
        if agent.computer_id in self._installing:
            return Away(agent, "enabling")
        failed = self._failed.get(agent.computer_id)
        if failed is not None and now_ms() - failed[0] < FRESH_MS:
            return Away(agent, "failed", failed[1])
        return None

    def installs(self) -> dict[str, object]:
        """The computers the conversations are being enabled on, and those
        where that failed lately, for the pages to follow."""
        return {
            "enabling": sorted(self._installing),
            "failed": {
                computer_id: at_ms
                for computer_id, (at_ms, _) in self._failed.items()
                if now_ms() - at_ms < FRESH_MS
            },
        }

    async def call(
        self, agent: Agent, member: str, *args: object, **kwargs: object
    ) -> JsonValue:
        """A member of the agent's conversations on its computer; one without
        them has them installed, and is told RemoteError meanwhile."""
        try:
            return await self._computers.call(
                agent.computer_id, "conversation", member, agent.id, *args, **kwargs
            )
        except RemoteError as error:
            if error.type == "LookupError":
                self.install(agent.computer_id)
            raise

    async def each[T](
        self, agents: Sequence[Agent], read: Callable[[Agent], Awaitable[T]]
    ) -> tuple[dict[str, T], list[Away]]:
        """What the read gives for each agent whose conversations can be read
        now, asked of them all at once, and why the others' are not there."""

        async def one(agent: Agent) -> T | Away:
            away = self.away(agent)
            if away is not None:
                return away
            try:
                return await read(agent)
            except RemoteError as error:
                # one for a computer without the conversations is being installed
                return self.away(agent) or Away(agent, "error", error.message)
            except TimeoutError:
                return Away(agent, "silent")

        async with asyncio.TaskGroup() as tasks:
            asked = [tasks.create_task(one(agent)) for agent in agents]
        answers: dict[str, T] = {}
        away: list[Away] = []
        for agent, task in zip(agents, asked, strict=True):
            answer = task.result()
            if isinstance(answer, Away):
                away.append(answer)
            else:
                answers[agent.id] = answer
        return answers, away

    def install(self, computer_id: str) -> None:
        if computer_id not in self._installing:
            self._installing[computer_id] = self._tasks.create_task(
                self._install(computer_id)
            )

    async def _install(self, computer_id: str) -> None:
        """Have the computer's manager add the package within the versions
        read here and enable its entry, then follow it to its end."""
        try:
            operation = Operation.model_validate(
                await self._computers.call(
                    computer_id,
                    "contrib-manager",
                    "add",
                    PACKAGE,
                    version=VERSIONS,
                    enable=ENTRY,
                )
            )
            while operation.phase not in ("complete", "failed", "cancelled"):
                await self._timer.create(POLL_MS).wait()
                operation = Operation.model_validate(
                    await self._computers.call(
                        computer_id, "contrib-manager", "operation", operation.id
                    )
                )
            if operation.phase == "complete":
                self._failed.pop(computer_id, None)
            else:
                self._failed[computer_id] = (
                    now_ms(),
                    operation.error or operation.phase,
                )
        except RemoteError as error:
            self._failed[computer_id] = (now_ms(), error.message)
        except TimeoutError:
            self._failed[computer_id] = (now_ms(), None)
        finally:
            del self._installing[computer_id]

    async def counts(
        self, account: Account, agents: Sequence[Agent], *, wait: bool = False
    ) -> Mapping[str, Counted]:
        """How many conversations each agent has and which wait, as last
        said; those whose message events moved on, or what was said is a
        minute old, asked again of their computers when these are here,
        waited for when the caller is to show them now."""
        latest = await self._activity.cursor(account, messages=True)
        now = now_ms()
        stale: list[Agent] = []
        for agent in agents:
            kept = self._kept.get(agent.id)
            if kept is None or now - kept.at_ms >= FRESH_MS:
                stale.append(agent)
            elif latest > kept.upto:
                # some agent's messages moved on: whether this one's did
                cursor = await self._activity.cursor(
                    account, agent_id=agent.id, messages=True
                )
                if cursor == kept.cursor:
                    self._kept[agent.id] = replace(kept, upto=latest)
                else:
                    stale.append(agent)
        asked = [
            self._ask(account, agent, latest)
            for agent in stale
            if self.away(agent) is None
        ]
        if wait and asked:
            await asyncio.wait(asked)
        return {
            agent.id: self._kept[agent.id] for agent in agents if agent.id in self._kept
        }

    def forget(self, agent_id: str) -> None:
        """What was said of the agent is asked again, kept till then."""
        kept = self._kept.get(agent_id)
        if kept is not None:
            self._kept[agent_id] = replace(kept, at_ms=0)

    def _ask(self, account: Account, agent: Agent, latest: int) -> asyncio.Task[None]:
        task = self._asking.get(agent.id)
        if task is None:
            task = self._asking[agent.id] = self._tasks.create_task(
                self._answer(account, agent, latest)
            )
        return task

    async def _answer(self, account: Account, agent: Agent, latest: int) -> None:
        try:
            cursor = await self._activity.cursor(
                account, agent_id=agent.id, messages=True
            )
            pending = Page.model_validate(
                await self.call(agent, "conversations", "pending", 0, PAGE)
            )
            approved = Page.model_validate(
                await self.call(agent, "conversations", "approved", 0, 0)
            )
            self._kept[agent.id] = Counted(
                total=approved.total,
                pending=pending,
                cursor=cursor,
                upto=max(latest, cursor),
                at_ms=now_ms(),
            )
        except RemoteError, TimeoutError, ValidationError:
            # a node that does not answer, or answers otherwise, keeps what
            # it said before
            logging.getLogger(__name__).info("conversations.unanswered", exc_info=True)
        finally:
            del self._asking[agent.id]

    def close(self) -> None:
        """Stop asking and installing: the server is going down."""
        for task in (*self._asking.values(), *self._installing.values()):
            task.cancel()
