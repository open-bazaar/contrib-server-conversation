"""Every agent's conversations through the server, over real HTTP with a
real node: the list merged across agents a page at a time, first contacts
let in and turned away, a conversation revoked, and what the agents say to
contacts waiting."""

from __future__ import annotations

lazy import asyncio
lazy import html
lazy import re
lazy from pathlib import Path
lazy from typing import Any, cast

import httpx
from bazaar_contrib_harness import CommandDetail, ToolCall
from computer import act, agent, calls, computer, heard, hold, say, sent
from served import (
    Served,
    admin,
    client,
    names,
    opened,
    page,
    seen,
    serving,
    told,
    until,
)

KANA = agent("kana", "有马佳奈")
TIFA = agent("tifa", "Tifa")
YU = {"id": "u-yu", "name": "yuchanns", "display_name": "yuchanns"}
LI = {"id": "u-li", "name": "lilei", "display_name": "李雷"}
CHEN = {"id": "u-chen", "name": "chenchen", "display_name": "陈晨"}


async def test_the_list_merges_the_agents_newest_first_a_page_at_a_time(
    tmp_path: Path,
):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA, TIFA]
        ) as pc:
            # sixty groups, every other one each agent's, each newer than the last
            for n in range(1, 61):
                await say(
                    pc,
                    "kana" if n % 2 else "tifa",
                    f"room-{n:02}",
                    YU,
                    f"第 {n} 个群的消息",
                    kind="group",
                    title=f"群 {n:02}",
                )
                await heard(pc, "kana" if n % 2 else "tifa")
                await asyncio.sleep(0.01)
            first = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: (
                    names(shown)[:1] == ["群 60"] and 'class="n">60<' in shown
                ),
            )
            assert names(first) == [f"群 {n:02}" for n in range(60, 10, -1)]
            onward = re.search(r'hx-get="(/ui/chats/rows\?[^"]*)"', first)
            assert onward, first[:3000]
            rest = await page(served, boss, onward.group(1).replace("&amp;", "&"))
            assert names(rest) == [f"群 {n:02}" for n in range(10, 0, -1)]
            assert "/ui/chats/rows?" not in rest
            # one agent's alone
            kana = re.search(r'<option value="(\d+)"[^>]*>有马佳奈<', first)
            assert kana, first[:3000]
            alone = await page(served, boss, f"/ui/chats/?agent={kana.group(1)}")
            assert names(alone) == [f"群 {n:02}" for n in range(59, 0, -2)]


async def test_first_contacts_are_let_in_turned_away_and_revoked(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA]
        ) as pc:
            await say(pc, "kana", "yuchanns", YU, "早上好")
            await say(
                pc, "kana", "lilei", LI, "你好，想请你帮忙看看排期", review="pending"
            )
            await say(
                pc,
                "kana",
                "design",
                CHEN,
                "帮我们汇总评审意见",
                kind="group",
                title="设计组",
                review="pending",
            )
            contacts = await until(
                lambda: page(served, boss, "/ui/chats/?tab=contacts"),
                lambda shown: sorted(names(shown)) == ["李雷", "设计组"],
            )
            badge = {"id": "badge", "topic": "chats.count", "seen": None}
            state = await until(
                lambda: served_state(served, boss, badge),
                lambda state: state == {"text": "2", "kind": "unread"},
            )
            assert state
            # what the agent says to contacts waiting, saved on its computer
            short = opened(contacts, "李雷").split("/")[0]
            saved = await boss.post(
                f"{served.url}/ui/chats/reply/{short}",
                data={"text": "你好，我先确认一下，稍后回复你。"},
            )
            assert saved.status_code == 200 and "稍后回复你" in saved.text
            setting = await served.computers.call(
                pc.id, "collaboration", "setting", "kana", "review.reply"
            )
            assert setting == "你好，我先确认一下，稍后回复你。"
            listing = seen(contacts, "chats.list")

            let_in = await boss.post(
                f"{served.url}/ui/chats/{opened(contacts, '李雷')}/review?tab=contacts",
                data={"decision": "approved", "on": "page"},
            )
            assert let_in.status_code == 204
            assert let_in.headers["HX-Redirect"] == (
                f"/ui/chats/{opened(contacts, '李雷')}"
            )
            turned = await boss.post(
                f"{served.url}/ui/chats/{opened(contacts, '设计组')}/review",
                data={"decision": "denied"},
            )
            assert turned.status_code == 204
            assert turned.headers["HX-Trigger"] == "chats:reviewed"
            chats = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: "李雷" in names(shown),
            )
            assert "设计组" not in names(chats)
            await until(
                lambda: page(served, boss, "/ui/chats/?tab=contacts"),
                lambda shown: names(shown) == [],
            )
            # a list open meanwhile is told
            assert "list" in await told(
                served,
                boss,
                {
                    "id": "list",
                    "topic": "chats.list",
                    "scope": {"agent": ""},
                    "seen": listing,
                },
            )

            # the agent stops replying to yuchanns; the node holds it denied
            listed = cast(
                dict[str, Any],
                await served.computers.call(
                    pc.id, "conversation", "conversations", "kana", "approved", 0, 50
                ),
            )
            (thread_id,) = [
                item["thread_id"]
                for item in listed["conversations"]
                if item["name"] == "yuchanns"
            ]
            address = opened(chats, "yuchanns")
            asked = await page(served, boss, f"/ui/chats/{address}/revoke")
            assert 'name="decision" value="denied"' in asked
            revoked = await boss.post(
                f"{served.url}/ui/chats/{address}/review",
                data={"decision": "denied", "on": "page"},
            )
            assert revoked.headers["HX-Redirect"] == "/ui/chats/"
            await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: "yuchanns" not in names(shown),
            )
            denied = cast(
                dict[str, Any],
                await served.computers.call(
                    pc.id, "conversation", "conversation", "kana", thread_id
                ),
            )
            assert denied["conversation"]["review"] == "denied"
            # writing again, yuchanns waits for a decision and is told the auto-reply first
            await say(pc, "kana", "yuchanns", YU, "还在吗？")
            back = await until(
                lambda: page(served, boss, "/ui/chats/?tab=contacts"),
                lambda shown: "yuchanns" in names(shown),
            )
            assert names(back) == ["yuchanns"]
            told_first = await until(
                lambda: sent(pc, "kana"),
                lambda said: any("稍后回复你" in item["text"] for item in said),
            )
            assert told_first


async def served_state(
    served: Served, session: httpx.AsyncClient, followed: dict[str, object]
) -> object:
    """The state the update bus gives for what a page follows."""
    answer = await session.post(
        f"{served.url}/api/server/poll", json={"subscriptions": [followed]}
    )
    changes = answer.json()["changes"]
    return changes[0].get("state") if changes else followed["seen"]


async def test_what_the_pages_follow_holds_still_until_something_changes(
    tmp_path: Path,
):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA]
        ) as pc:
            await say(pc, "kana", "yuchanns", YU, "早上好")
            await say(pc, "kana", "lilei", LI, "你好", review="pending")
            listing = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: (
                    names(shown) == ["yuchanns"] and 'class="badge">1<' in shown
                ),
            )
            kana = re.search(r'<option value="(\d+)"[^>]*>有马佳奈<', listing)
            assert kana, listing[:3000]
            address = opened(listing, "yuchanns")
            agent_scope = {"agent": kana.group(1)}
            pages = {
                "list": ("/ui/chats/", "chats.list", {"agent": ""}),
                "thread": (
                    f"/ui/chats/{address}",
                    "chats.thread",
                    dict(zip(("agent", "thread"), address.split("/"), strict=True)),
                ),
                "tab": (f"/ui/chats/agent/{kana.group(1)}", "chats.list", agent_scope),
                "reminders": (
                    f"/ui/chats/reminders/{kana.group(1)}",
                    "chats.list",
                    agent_scope,
                ),
                "attention": ("/ui/chats/attention", "chats.list", {"agent": ""}),
            }
            followed = [
                {
                    "id": key,
                    "topic": topic,
                    "scope": scope,
                    "seen": seen(await page(served, boss, path), topic),
                }
                for key, (path, topic, scope) in pages.items()
            ]
            assert await told(served, boss, *followed) == set()
            # a message in yuchanns's conversation changes what each follows
            await say(pc, "kana", "yuchanns", YU, "还在吗？")
            changed = await until(
                lambda: told(served, boss, *followed),
                lambda changed: changed == set(pages),
            )
            assert changed


async def test_a_reply_links_to_its_turn_and_a_reminder_names_its_conversation(
    tmp_path: Path,
):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA]
        ) as pc:
            # kana's turn makes a call and replies before it ends
            await hold(pc, "kana", True)
            await calls(
                pc,
                "kana",
                [
                    ToolCall(
                        call_id="c-1",
                        name="Bash",
                        duration_ms=800,
                        detail=CommandDetail(command="git fetch", exit_code=0),
                    )
                ],
            )
            await say(
                pc,
                "kana",
                "dev",
                YU,
                "合一下",
                kind="group",
                title="#开发",
                mention=True,
            )
            await heard(pc, "kana")
            await say(pc, "kana", "yuchanns", YU, "晚点给我回个电话")
            # what kana reads, a check at a time, until both have come in
            by_kind: dict[str, dict[str, Any]] = {}
            async with asyncio.timeout(60):
                while True:
                    checked = await act(
                        served, pc, "kana", "messages", "message", "check"
                    )
                    by_kind |= {
                        message["target_kind"]: message
                        for message in checked["result"]["messages"]
                    }
                    if set(by_kind) == {"dm", "group"}:
                        break
                    await asyncio.sleep(0.2)
            asked = by_kind["group"]
            replied = await act(
                served,
                pc,
                "kana",
                "messages",
                "message",
                "send",
                target=asked["target"],
                body="合好了",
            )
            reminded = await act(
                served,
                pc,
                "kana",
                "reminder",
                "reminder",
                "schedule",
                title="看一眼结果",
                message_id=asked["message_id"],
                delay_seconds=3600,
            )
            called = await act(
                served,
                pc,
                "kana",
                "reminder",
                "reminder",
                "schedule",
                title="回个电话",
                message_id=by_kind["dm"]["message_id"],
                delay_seconds=7200,
            )
            assert replied["ok"] and reminded["ok"] and called["ok"], (
                replied,
                reminded,
                called,
            )
            await hold(pc, "kana", False)
            listing = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: "#开发" in names(shown),
            )
            address = opened(listing, "#开发")
            kana = address.split("/")[0]
            shown = await until(
                lambda: page(served, boss, f"/ui/chats/{address}"),
                lambda shown: 'class="receipt"' in shown,
            )
            link = re.search(r'<a class="receipt" href="([^"]+)"', shown)
            assert link, shown[:3000]
            assert re.fullmatch(
                rf"/agents/{kana}\?tab=log&turn=[^&]+", html.unescape(link.group(1))
            )
            reminders = await boss.get(
                f"{served.url}/ui/chats/reminders/{kana}",
                headers={"Accept-Language": "zh-CN"},
            )
            # the group by its name, the private one as who it is with, as the design writes it
            group, private = re.findall(
                r'<div class="what">(.*?)</div>', reminders.text
            )
            assert "看一眼结果" in group and "#开发" in group
            assert "回个电话" in private and "与 yuchanns 的私聊" in private
