"""What other modules hold server-conversation to: how many conversations
wait for an account's decision."""

from __future__ import annotations

from typing import Protocol

from bazaar_core import ServiceKey
lazy from bazaar_contrib_server import Account


class Conversations(Protocol):
    async def pending(self, account: Account) -> int:
        """How many conversations of the agents the account may see wait to
        be let in or turned away, as their computers last said."""
        ...


CONVERSATIONS = ServiceKey[Conversations]("server.conversations")
