"""Who may see an agent's conversations, decide on its first contacts and
change what it says to them, over real HTTP with a real node: the points
a role holds and what is shared with it."""

from __future__ import annotations

lazy import re
lazy from pathlib import Path

from computer import agent, computer, say
from served import admin, client, names, opened, page, role, serving, through, until

KANA = agent("kana", "有马佳奈")
LI = {"id": "u-li", "name": "lilei", "display_name": "李雷"}
YU = {"id": "u-yu", "name": "yuchanns", "display_name": "yuchanns"}


async def test_a_role_shared_viewing_sees_and_shared_approving_decides(
    tmp_path: Path,
):
    async with serving() as served, client() as boss, client() as bob:
        await admin(served, boss)
        reviewers = await role(
            served, boss, "agents.view", "agents.approve", "agents.update"
        )
        await boss.post(f"{served.url}{reviewers}/default")
        await through(served, bob, "bob")
        given = reviewers.rsplit("/", 1)[1]
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA]
        ) as pc:
            await say(pc, "kana", "yuchanns", YU, "早上好")
            await say(pc, "kana", "lilei", LI, "你好", review="pending")
            listing = await until(
                lambda: page(served, boss, "/ui/chats/?tab=contacts"),
                lambda shown: names(shown) == ["李雷"],
            )
            kana = re.search(r'<option value="(\d+)"[^>]*>有马佳奈<', listing)
            assert kana, listing[:3000]
            sharing = f"{served.url}/ui/access/shares/agent/{kana.group(1)}"
            # the agent's share page offers deciding on first contacts
            assert f"{given} agents.approve" in (await boss.get(sharing)).text
            await boss.post(
                sharing, data={"roles": [given], "grant": [f"{given} agents.view"]}
            )
            await until(
                lambda: page(served, bob, "/ui/chats/?tab=contacts"),
                lambda shown: names(shown) == ["李雷"],
            )
            address = opened(listing, "李雷")
            # shared for viewing alone, the decision and the auto-reply are not bob's
            refused = await bob.post(
                f"{served.url}/ui/chats/{address}/review", data={"decision": "approved"}
            )
            assert refused.status_code == 404
            chats = await page(served, bob, "/ui/chats/")
            assert (
                await bob.get(
                    f"{served.url}/ui/chats/{opened(chats, 'yuchanns')}/revoke"
                )
            ).status_code == 404
            assert (
                await bob.post(
                    f"{served.url}/ui/chats/reply/{kana.group(1)}",
                    data={"text": "稍等"},
                )
            ).status_code == 404
            waiting = await page(served, bob, f"/ui/chats/{address}")
            assert 'value="approved"' not in waiting

            await boss.post(
                sharing,
                data={
                    "roles": [given],
                    "grant": [f"{given} agents.view", f"{given} agents.approve"],
                },
            )
            assert 'value="approved"' in await page(served, bob, f"/ui/chats/{address}")
            assert (
                await bob.post(
                    f"{served.url}/ui/chats/reply/{kana.group(1)}",
                    data={"text": "稍等"},
                )
            ).status_code == 404
            allowed = await bob.post(
                f"{served.url}/ui/chats/{address}/review", data={"decision": "approved"}
            )
            assert allowed.status_code == 204
            await until(
                lambda: page(served, bob, "/ui/chats/"),
                lambda shown: "李雷" in names(shown),
            )

            await boss.post(
                sharing,
                data={
                    "roles": [given],
                    "grant": [
                        f"{given} agents.view",
                        f"{given} agents.approve",
                        f"{given} agents.update",
                    ],
                },
            )
            changed = await bob.post(
                f"{served.url}/ui/chats/reply/{kana.group(1)}", data={"text": "稍等"}
            )
            assert changed.status_code == 200
            assert (
                await served.computers.call(
                    pc.id, "collaboration", "setting", "kana", "review.reply"
                )
                == "稍等"
            )
