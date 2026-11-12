"""A real node in a process of its own, as a computer of the served server:
connected through starlink with the token its page gives, running the
teahouse's channel and runtime for the agents it is given, and the node's
side of the conversations unless it is to go without; with what people
say to its agents, and what the agents themselves do, as a test has it."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
lazy import asyncio
lazy import json
lazy import os
lazy import sys
lazy import tomllib
lazy from collections.abc import AsyncIterator, Sequence
lazy from pathlib import Path
lazy from typing import Any, cast
lazy from uuid import uuid4, uuid7

import httpx
import tomli_w
from served import Served, enrol

# how long a node takes to come up and its computer to show online
WAIT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class Computer:
    ref: str
    id: str
    home: Path
    node: asyncio.subprocess.Process


def agent(id: str, name: str) -> dict[str, Any]:
    """An agent talking on the teahouse, every conversation its own."""
    return {
        "id": id,
        "name": name,
        "mode": "dangerous_individual",
        "channel": [{"kind": "teahouse-channel", "bot": f"{id}-bot"}],
        "harness": [{"kind": "teahouse-runtime"}],
    }


async def _run(*arguments: str, environment: dict[str, str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode()
    return output.decode()


async def configure(
    home: Path,
    python: str,
    environment: dict[str, str],
    agents: Sequence[dict[str, Any]],
    *contribs: dict[str, Any],
    indexes: Sequence[str] = (),
) -> Path:
    """The node's defaults, as its first run writes them, with the agents,
    a short sidecar address, the contribs given and, when given, the indexes
    its manager finds packages in."""
    config = home / "node.toml"
    await asyncio.to_thread(home.mkdir, parents=True, exist_ok=True)
    await _run(
        python,
        "-m",
        "bazaar_compute_node",
        "--configer-options",
        str(config),
        "--help",
        environment=environment,
    )
    document = tomllib.loads(await asyncio.to_thread(config.read_text))
    for package in document["contribs"]:
        for entry in package["enabled"]:
            if entry["entry"] == "agent":
                entry["config"]["agents"] = list(agents)
            elif entry["entry"] == "sidecar-unix":
                # a socket path stays short whatever the temporary directory
                entry["config"] = {
                    "endpoint": f"/tmp/bcn-chats-{uuid4().hex[:12]}.sock"
                }
    document["contribs"] += list(contribs)
    if indexes:
        document["contrib_indexes"] = list(indexes)
    await asyncio.to_thread(config.write_text, tomli_w.dumps(document))
    return config


@asynccontextmanager
async def running(
    config: Path, python: str, environment: dict[str, str]
) -> AsyncIterator[asyncio.subprocess.Process]:
    """The node with its configuration, until the test is done with it."""
    output = config.with_suffix(".log")
    log = await asyncio.to_thread(output.open, "wb")
    process = await asyncio.create_subprocess_exec(
        python,
        str(Path(__file__).with_name("run_node.py")),
        "config-filesystem",
        str(config),
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=log,
        stderr=asyncio.subprocess.STDOUT,
    )
    await asyncio.to_thread(log.close)
    try:
        async with asyncio.timeout(WAIT_SECONDS):
            while b"bcn ready" not in await asyncio.to_thread(output.read_bytes):
                assert process.returncode is None, await asyncio.to_thread(
                    output.read_text
                )
                await asyncio.sleep(0.5)
        yield process
    finally:
        if process.returncode is None:
            assert process.stdin is not None
            process.stdin.close()
            await process.wait()


def linked(served: Served, token: str) -> dict[str, Any]:
    return {
        "package": "bazaar-contrib-starlink",
        "enabled": [
            {"entry": "starlink", "config": {"server": served.url, "token": token}}
        ],
    }


TEAHOUSE = {
    "package": "teahouse",
    "enabled": [
        {"entry": "teahouse-channel-builder"},
        {"entry": "teahouse-runtime-builder"},
    ],
}
CONVERSATION = {
    "package": "bazaar-contrib-starlink-conversation",
    "enabled": [{"entry": "starlink-conversation"}],
}


@asynccontextmanager
async def computer(
    served: Served,
    session: httpx.AsyncClient,
    home: Path,
    name: str,
    agents: Sequence[dict[str, Any]],
) -> AsyncIterator[Computer]:
    """A computer the account connects, its node running the agents and the
    node's side of the conversations."""
    ref, token = await enrol(served, session, name)
    environment = dict(os.environ) | {"BCN_HOME": str(home), "HOME": str(home)}
    config = await configure(
        home,
        sys.executable,
        environment,
        agents,
        TEAHOUSE,
        linked(served, token),
        CONVERSATION,
    )
    async with running(config, sys.executable, environment) as process:
        yield Computer(ref=ref, id=token.partition(":")[0], home=home, node=process)


async def say(
    computer: Computer,
    agent_id: str,
    chat: str,
    sender: dict[str, str],
    text: str,
    *,
    kind: str = "dm",
    title: str | None = None,
    review: str = "approved",
    mention: bool = False,
    at_ms: int | None = None,
    attachments: Sequence[dict[str, str]] = (),
) -> str:
    """Leave a message in the agent's inbox; its id."""
    inbox = computer.home / "teahouse" / agent_id / "inbox"
    await asyncio.to_thread(inbox.mkdir, parents=True, exist_ok=True)
    id = str(uuid7())
    message = {
        "chat": chat,
        "kind": kind,
        "title": title,
        "sender": sender,
        "text": text,
        "review": review,
        "mention": mention,
        "at_ms": at_ms,
        "attachments": list(attachments),
    }
    # written aside and moved in, so the channel never reads half of it
    staged = inbox / f"{id}.part"
    await asyncio.to_thread(staged.write_text, json.dumps(message))
    await asyncio.to_thread(staged.rename, inbox / f"{id}.json")
    return id


async def heard(computer: Computer, agent_id: str) -> None:
    """Wait for the agent's channel to have taken in all that was said."""
    inbox = computer.home / "teahouse" / agent_id / "inbox"
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            if not await asyncio.to_thread(lambda: any(inbox.glob("*.json"))):
                return
            await asyncio.sleep(0.05)


async def act(
    served: Served,
    computer: Computer,
    agent_id: str,
    service: str,
    resource: str,
    command: str,
    **values: Any,
) -> dict[str, Any]:
    """A command the agent runs in its session, as bcc runs it, sent to the
    node the way the server calls it."""
    session = computer.home / "teahouse" / agent_id / "session.json"
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            if await asyncio.to_thread(session.exists):
                break
            await asyncio.sleep(0.2)
    environment = json.loads(await asyncio.to_thread(session.read_text))
    return cast(
        dict[str, Any],
        await served.computers.call(
            computer.id,
            service,
            "request",
            {
                "kind": "command",
                "resource": resource,
                "command": command,
                "agent_id": environment["BCN_AGENT_ID"],
                "actor_id": environment["BCN_ACTOR_ID"],
                "runtime_session_id": environment["BCN_RUNTIME_SESSION_ID"],
                "session_capability": environment["BCN_COMMAND_CAPABILITY"],
                **values,
            },
        ),
    )


async def hold(computer: Computer, agent_id: str, held: bool) -> None:
    """Keep the agent's turns from ending, or let them."""
    directory = computer.home / "teahouse" / agent_id
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    if held:
        await asyncio.to_thread((directory / "hold").touch)
    else:
        await asyncio.to_thread((directory / "hold").unlink, missing_ok=True)


async def calls(computer: Computer, agent_id: str, made: Sequence[Any]) -> None:
    """The tool calls the agent's next turns make, as the runtime reports them."""
    directory = computer.home / "teahouse" / agent_id
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        (directory / "calls.json").write_text,
        json.dumps([call.model_dump(mode="json") for call in made]),
    )


async def sent(computer: Computer, agent_id: str) -> list[dict[str, str]]:
    """What the agent sent on its channel, oldest first."""
    outbox = computer.home / "teahouse" / agent_id / "outbox.jsonl"
    if not await asyncio.to_thread(outbox.exists):
        return []
    return [
        json.loads(line)
        for line in (await asyncio.to_thread(outbox.read_text)).splitlines()
    ]
