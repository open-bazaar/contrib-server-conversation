"""CONVERSATIONS: how many conversations wait for a decision, of the agents
an account may see, as their computers last said."""

from __future__ import annotations

lazy from bazaar_contrib_server import Account
lazy from bazaar_contrib_server_agent import Agents

lazy from .reader import Reader


class Service:
    def __init__(self, agents: Agents, reader: Reader) -> None:
        self._agents = agents
        self._reader = reader

    async def pending(self, account: Account) -> int:
        counts = await self._reader.counts(account, await self._agents.agents(account))
        return sum(counted.pending.total for counted in counts.values())
