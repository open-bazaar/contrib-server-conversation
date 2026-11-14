"""The same group as real agents on separate nodes see it, through HTTP."""

from __future__ import annotations

from html.parser import HTMLParser
lazy import asyncio
lazy import html
lazy import re
lazy from pathlib import Path
lazy from typing import Any, cast

from computer import act, agent, computer, heard, say
from served import admin, client, names, opened, page, role, serving, through, until

ALPHA = agent("alpha", "青禾")
BETA = agent("beta", "小雨")
PERSON = {"id": "person-1", "name": "lin", "display_name": "林"}


class Bubbles(HTMLParser):
    def __init__(self, document: str):
        super().__init__()
        self.depth = 0
        self.text: list[str] = []
        self.messages: list[str] = []
        self.feed(document)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "bubble" in (dict(attrs).get("class") or "").split():
                self.depth = 1
                self.text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.depth:
            self.depth -= 1
            if not self.depth:
                self.messages.append("".join(self.text).strip())

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.text.append(data)


async def test_a_group_unites_nodes_messages_names_paging_and_search(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with (
            computer(served, boss, tmp_path / "one", "one", [ALPHA]) as one,
            computer(served, boss, tmp_path / "two", "two", [BETA]) as two,
        ):
            # Identical platform messages reach both bots. Equal timestamps
            # also exercise paging by platform identity at a time boundary.
            for n in range(80):
                for pc, owner in ((one, "alpha"), (two, "beta")):
                    await say(
                        pc,
                        owner,
                        "room",
                        PERSON,
                        f"合并验收 {n:03}",
                        kind="group",
                        title="讨论群",
                        at_ms=1_600_000_000_000,
                        provider_message_id=f"message-{n:03}",
                        mention=n == 0,
                    )
            await heard(one, "alpha")
            await heard(two, "beta")
            for pc, owner in ((one, "alpha"), (two, "beta")):
                await until(
                    lambda pc=pc, owner=owner: served.computers.call(
                        pc.id, "conversation", "conversations", owner, "approved"
                    ),
                    lambda result: (
                        bool(cast(dict[str, Any], result)["conversations"])
                        and cast(dict[str, Any], result)["conversations"][0]["latest"][
                            "text"
                        ]
                        == "合并验收 079"
                    ),
                )
            listing = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: (
                    names(shown) == ["讨论群"] and "青禾" in shown and "小雨" in shown
                ),
            )
            address = opened(listing, "讨论群")
            shown = await page(served, boss, f"/ui/chats/{address}")
            found = [
                text.removeprefix("合并验收 ")
                for text in Bubbles(shown).messages
                if text.startswith("合并验收 ")
            ]
            while older := re.search(r'class="older paging" hx-get="([^"]+)"', shown):
                shown = await page(served, boss, html.unescape(older.group(1)))
                found = [
                    text.removeprefix("合并验收 ")
                    for text in Bubbles(shown).messages
                    if text.startswith("合并验收 ")
                ] + found
            assert found == [f"{n:03}" for n in range(80)], found[-10:]

            conversations = cast(
                dict[str, Any],
                await served.computers.call(
                    one.id, "conversation", "conversations", "alpha", "approved"
                ),
            )
            thread = conversations["conversations"][0]["thread_id"]
            checked = await act(served, one, "alpha", "messages", "message", "check")
            target = checked["result"]["messages"][0]["target"]
            await act(
                served,
                one,
                "alpha",
                "messages",
                "message",
                "send",
                target=target,
                body="我来整理结果",
            )
            history = cast(
                dict[str, Any],
                await until(
                    lambda: served.computers.call(
                        one.id, "conversation", "timeline", "alpha", thread
                    ),
                    lambda result: any(
                        message["direction"] == "outbound"
                        and message["provider_message_id"]
                        for message in cast(dict[str, Any], result)["messages"]
                    ),
                ),
            )
            reply = next(
                message
                for message in history["messages"]
                if message["direction"] == "outbound"
            )
            await say(
                two,
                "beta",
                "room",
                {
                    "id": "alpha-alpha-bot",
                    "name": "platform-alias",
                    "display_name": "平台别名",
                    "kind": "agent",
                },
                "我来整理结果",
                kind="group",
                title="讨论群",
                provider_message_id=reply["provider_message_id"],
                at_ms=reply["at_ms"],
            )
            await heard(two, "beta")
            shown = await until(
                lambda: page(served, boss, f"/ui/chats/{address}"),
                lambda shown: "我来整理结果" in shown,
            )
            assert Bubbles(shown).messages.count("我来整理结果") == 1
            assert "<b>青禾</b>" in shown
            assert "平台别名" not in shown
            assert "?agent=" in shown

            results = await page(served, boss, "/ui/chats/?q=合并验收")
            assert "<b>80</b>" in results
            assert "<b>160</b>" not in results
            hit = re.search(r'href="(/ui/chats/\d+/\d+\?[^\"]*around=[^\"]+)"', results)
            assert hit
            located = await page(served, boss, html.unescape(hit.group(1)))
            assert 'class="msg hit"' in located


async def test_group_catalogue_merges_before_paging_and_keeps_topics(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "node", "node", [ALPHA, BETA]
        ) as pc:
            for n in range(60):
                for owner in ("alpha", "beta"):
                    await say(
                        pc,
                        owner,
                        f"room-{n:02}",
                        PERSON,
                        "讨论",
                        kind="group",
                        title=f"群 {n:02}",
                        at_ms=1_600_000_000_000 + n,
                    )
            await heard(pc, "alpha")
            await heard(pc, "beta")
            for owner in ("alpha", "beta"):
                await until(
                    lambda owner=owner: served.computers.call(
                        pc.id, "conversation", "conversations", owner, "approved", 0, 0
                    ),
                    lambda result: cast(dict[str, Any], result)["total"] == 60,
                )
            shown = await until(
                lambda: page(served, boss, "/ui/chats/"),
                # Activity follows receipt, so fixture creation order is not
                # an ordering barrier. Wait for the complete merged catalogue.
                lambda shown: '<span class="n">60</span>' in shown,
            )
            found = names(shown)
            while onward := re.search(r'hx-get="(/ui/chats/rows\?[^\"]+)"', shown):
                shown = await page(served, boss, html.unescape(onward.group(1)))
                found.extend(names(shown))
            assert len(found) == 60
            assert set(found) == {f"群 {n:02}" for n in range(60)}
            for owner in ("alpha", "beta"):
                for topic in ("root-one", "root-two"):
                    await say(
                        pc,
                        owner,
                        f"shared/{topic}",
                        PERSON,
                        "话题",
                        kind="group",
                        title=f"话题 {topic}",
                    )
                for room in ("other-one", "other-two"):
                    await say(
                        pc, owner, room, PERSON, "同名", kind="group", title="同名群"
                    )
            await heard(pc, "alpha")
            await heard(pc, "beta")
            shown = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: (
                    "话题 root-one" in names(shown) and "话题 root-two" in names(shown)
                ),
            )
            assert names(shown).count("话题 root-one") == 1
            assert names(shown).count("话题 root-two") == 1
            assert names(shown).count("同名群") == 2


async def test_unkeyed_groups_and_private_chats_remain_independent(tmp_path: Path):
    owners = [agent("alpha", "青禾"), agent("beta", "小雨")]
    for owner in owners:
        owner["channel"][0].update(bot="shared-bot", space="")
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(served, boss, tmp_path / "one", "one", owners) as pc:
            for owner in ("alpha", "beta"):
                await say(
                    pc,
                    owner,
                    "room",
                    PERSON,
                    "群里的安排",
                    kind="group",
                    title="未确定身份的群",
                )
                await say(pc, owner, "person", PERSON, "我想单独确认一下安排")
                await heard(pc, owner)
            listing = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: (
                    names(shown).count("未确定身份的群") == 2
                    and names(shown).count("林") == 2
                ),
            )
            assert len(names(listing)) == 4
            conversations = []
            for owner in ("alpha", "beta"):
                result = cast(
                    dict[str, Any],
                    await served.computers.call(
                        pc.id, "conversation", "conversations", owner, "approved"
                    ),
                )
                conversations.append(
                    next(
                        item
                        for item in result["conversations"]
                        if item["kind"] == "group"
                    )
                )
            assert all(item["conversation_key"] is None for item in conversations)
            assert (
                conversations[0]["provider_thread_id"]
                == conversations[1]["provider_thread_id"]
            )


async def test_a_shared_group_contains_only_visible_sources(tmp_path: Path):
    async with serving() as served, client() as boss, client() as visitor:
        await admin(served, boss)
        viewers = await role(served, boss, "agents.view")
        await boss.post(f"{served.url}{viewers}/default")
        await through(served, visitor, "reader")
        given = viewers.rsplit("/", 1)[1]
        async with computer(
            served, boss, tmp_path / "node", "node", [ALPHA, BETA]
        ) as pc:
            await say(
                pc, "alpha", "room", PERSON, "可见来源", kind="group", title="共享群"
            )
            await say(
                pc,
                "beta",
                "room",
                PERSON,
                "另一个来源的内容",
                kind="group",
                title="共享群",
            )
            listing = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: names(shown) == ["共享群"] and "小雨" in shown,
            )
            alpha = re.search(r'<option value="(\d+)"[^>]*>青禾<', listing)
            assert alpha
            await boss.post(
                f"{served.url}/ui/access/shares/agent/{alpha.group(1)}",
                data={"roles": [given], "grant": [f"{given} agents.view"]},
            )
            listing = await until(
                lambda: page(served, visitor, "/ui/chats/"),
                lambda shown: names(shown) == ["共享群"],
            )
            shown = await page(
                served, visitor, f"/ui/chats/{opened(listing, '共享群')}"
            )
            assert "可见来源" in shown
            assert "另一个来源的内容" not in shown
            assert "小雨" not in shown


async def test_group_approval_changes_only_the_selected_bot(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "node", "node", [ALPHA, BETA]
        ) as pc:
            for owner in ("alpha", "beta"):
                await say(
                    pc,
                    owner,
                    "room",
                    PERSON,
                    "请加入这次讨论",
                    kind="group",
                    title="待审批群",
                    review="pending",
                    provider_message_id="intro",
                )
            listing = await until(
                lambda: page(served, boss, "/ui/chats/?tab=contacts"),
                lambda shown: names(shown) == ["待审批群"],
            )
            alpha = re.search(r'<option value="(\d+)"[^>]*>青禾<', listing)
            assert alpha
            single = await page(
                served, boss, f"/ui/chats/?tab=contacts&agent={alpha.group(1)}"
            )
            address = opened(single, "待审批群")
            allowed = await boss.post(
                f"{served.url}/ui/chats/{address}/review", data={"decision": "approved"}
            )
            assert allowed.status_code == 204
            for owner, expected in (("alpha", "approved"), ("beta", "pending")):
                found = cast(
                    dict[str, Any],
                    await served.computers.call(
                        pc.id, "conversation", "conversations", owner, expected
                    ),
                )
                assert [item["name"] for item in found["conversations"]] == ["待审批群"]
            revoked = await boss.post(
                f"{served.url}/ui/chats/{address}/review", data={"decision": "denied"}
            )
            assert revoked.status_code == 204
            still = cast(
                dict[str, Any],
                await served.computers.call(
                    pc.id, "conversation", "conversations", "beta", "pending"
                ),
            )
            assert [item["name"] for item in still["conversations"]] == ["待审批群"]


async def test_group_paging_keeps_unique_messages_between_skewed_copies(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with (
            computer(served, boss, tmp_path / "one", "one", [ALPHA]) as one,
            computer(served, boss, tmp_path / "two", "two", [BETA]) as two,
        ):
            await say(
                two,
                "beta",
                "room",
                PERSON,
                "另一端独有消息",
                kind="group",
                title="错时群",
                at_ms=1_600_000_000_100,
                provider_message_id="only-beta",
            )
            for n in range(80):
                for pc, owner, offset in ((one, "alpha", 0), (two, "beta", 100)):
                    if owner == "beta" and n >= 50:
                        continue
                    await say(
                        pc,
                        owner,
                        "room",
                        PERSON,
                        f"错时消息 {n:03}",
                        kind="group",
                        title="错时群",
                        at_ms=1_600_000_000_001 + offset + n,
                        provider_message_id=f"shared-{n:03}",
                    )
            for pc, owner in ((one, "alpha"), (two, "beta")):
                await heard(pc, owner)
                await until(
                    lambda pc=pc, owner=owner: served.computers.call(
                        pc.id, "conversation", "search", owner, "错时消息", "time"
                    ),
                    lambda answer, owner=owner: (
                        cast(dict[str, Any], answer)["total"]
                        == (80 if owner == "alpha" else 50)
                    ),
                )
            listing = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: (
                    names(shown) == ["错时群"] and "青禾" in shown and "小雨" in shown
                ),
            )
            address = opened(listing, "错时群")
            shown = await page(served, boss, f"/ui/chats/{address}")
            found = Bubbles(shown).messages
            assert len(found) == 50
            while older := re.search(r'class="older paging" hx-get="([^"]+)"', shown):
                shown = await page(served, boss, html.unescape(older.group(1)))
                found = Bubbles(shown).messages + found
            assert len(found) == 81, found
            assert set(found) == {
                "另一端独有消息",
                *(f"错时消息 {n:03}" for n in range(80)),
            }


async def test_an_open_group_keeps_paging_when_a_second_bot_arrives(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with (
            computer(served, boss, tmp_path / "one", "one", [ALPHA]) as one,
            computer(served, boss, tmp_path / "two", "two", [BETA]) as two,
        ):
            for n in range(60):
                await say(
                    one,
                    "alpha",
                    "room",
                    PERSON,
                    f"原有消息 {n:03}",
                    kind="group",
                    title="后来相聚",
                    at_ms=1_600_000_000_000 + n,
                    provider_message_id=f"first-{n:03}",
                )
            await heard(one, "alpha")
            await until(
                lambda: served.computers.call(
                    one.id, "conversation", "search", "alpha", "原有消息", "time"
                ),
                lambda answer: cast(dict[str, Any], answer)["total"] == 60,
            )
            listing = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: names(shown) == ["后来相聚"],
            )
            address = opened(listing, "后来相聚")
            shown = await page(served, boss, f"/ui/chats/{address}")
            cursors = re.findall(r'class="message"[^>]*data-cursor="([^"]+)"', shown)
            older = re.search(r'class="older paging" hx-get="([^"]+)"', shown)
            assert len(cursors) == 50 and older
            await say(
                two,
                "beta",
                "room",
                PERSON,
                "第二个机器人收到的新消息",
                kind="group",
                title="后来相聚",
                at_ms=1_600_000_000_100,
                provider_message_id="second-bot",
            )
            await heard(two, "beta")
            await until(
                lambda: served.computers.call(
                    two.id, "conversation", "search", "beta", "第二个机器人", "time"
                ),
                lambda answer: cast(dict[str, Any], answer)["total"] == 1,
            )
            # The list refresh learns the other bot, while the open pane
            # still holds exactly the links rendered before it joined.
            await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda listed: (
                    names(listed) == ["后来相聚"]
                    and set(
                        re.findall(
                            r'class="ag"><span[^>]*></span>([^<]+)</span>', listed
                        )
                    )
                    == {"青禾", "小雨"}
                ),
            )
            # Both requests carry links from the already open, single-bot pane.
            from urllib.parse import urlencode

            newer = await page(
                served,
                boss,
                f"/ui/chats/{address}/messages?"
                + urlencode({"after": html.unescape(cursors[-1])}),
            )
            assert Bubbles(newer).messages == ["第二个机器人收到的新消息"]
            earlier = await page(served, boss, html.unescape(older.group(1)))
            assert Bubbles(earlier).messages == [f"原有消息 {n:03}" for n in range(10)]


async def test_search_paging_resolves_a_thread_newer_than_the_directory(tmp_path: Path):
    from test_search import hits

    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(served, boss, tmp_path / "one", "one", [ALPHA]) as one:
            for n in range(25):
                await say(
                    one,
                    "alpha",
                    "room",
                    PERSON,
                    f"同词搜索 {n:03}",
                    kind="group",
                    title="已有会话",
                    at_ms=1_600_000_000_001 + n,
                )
            await heard(one, "alpha")
            shown = await until(
                lambda: page(served, boss, "/ui/chats/?q=同词搜索&sort=time"),
                lambda shown: "<b>25</b>" in shown,
            )
            onward = re.search(r'hx-get="(/ui/chats/hits\?[^"]+)"', shown)
            assert onward
            await say(
                one,
                "alpha",
                "new-room",
                PERSON,
                "同词搜索 新会话",
                kind="group",
                title="新建会话",
                at_ms=1_600_000_000_000,
            )
            await heard(one, "alpha")
            await until(
                lambda: served.computers.call(
                    one.id, "conversation", "search", "alpha", "同词搜索", "time"
                ),
                lambda answer: cast(dict[str, Any], answer)["total"] == 26,
            )
            later = await page(served, boss, html.unescape(onward.group(1)))
            assert len(hits(later)) == 6
            assert any(hit.endswith("同词搜索 新会话") for hit in hits(later))


async def test_an_offline_representative_opens_through_its_online_peer(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(served, boss, tmp_path / "one", "one", [ALPHA]) as one:
            await say(
                one,
                "alpha",
                "room",
                PERSON,
                "在线端的历史",
                kind="group",
                title="离线代表",
                provider_message_id="online-message",
            )
            await heard(one, "alpha")
            await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: names(shown) == ["离线代表"],
            )
            async with computer(served, boss, tmp_path / "two", "two", [BETA]) as two:
                await say(
                    two,
                    "beta",
                    "room",
                    PERSON,
                    "离线端最后活动",
                    kind="group",
                    title="离线代表",
                    provider_message_id="offline-message",
                )
                await heard(two, "beta")
                listing = await until(
                    lambda: page(served, boss, "/ui/chats/"),
                    lambda shown: (
                        names(shown) == ["离线代表"]
                        and "离线端最后活动" in shown
                        and "小雨" in shown
                    ),
                )
                address = opened(listing, "离线代表")
                before = await page(served, boss, f"/ui/chats/{address}")
                assert "在线端的历史" in Bubbles(before).messages
                # Observe the actual departure through a test contrib. The
                # relay's heartbeat window remains part of this real scenario.
                from bazaar_contrib_server_computer import COMPUTER_LEFT, ComputerLeft
                from bazaar_core import Composition, Context, Contrib, ContribEntry

                departed = asyncio.Event()

                def left(ctx: Context, event: ComputerLeft) -> None:
                    del ctx
                    if event.computer_id == two.id:
                        departed.set()

                observation = Composition(
                    entries=(
                        ContribEntry(
                            name="watch-departure",
                            definition=Contrib(listen={COMPUTER_LEFT: left}),
                        ),
                    )
                )
                async with served.hub.enable(observation):
                    assert two.node.stdin is not None
                    two.node.stdin.close()
                    await two.node.wait()
                    await departed.wait()
                    shown = await page(served, boss, f"/ui/chats/{address}")
                    assert Bubbles(shown).messages == ["在线端的历史"]
                    await say(
                        one,
                        "alpha",
                        "room",
                        PERSON,
                        "在线端继续更新",
                        kind="group",
                        title="离线代表",
                        provider_message_id="online-newer",
                    )
                    await heard(one, "alpha")
                    await until(
                        lambda: served.computers.call(
                            one.id,
                            "conversation",
                            "search",
                            "alpha",
                            "继续更新",
                            "time",
                        ),
                        lambda answer: cast(dict[str, Any], answer)["total"] == 1,
                    )
                    marker = re.search(r'data-newer="([^"]+)"', shown)
                    cursors = re.findall(
                        r'class="message"[^>]*data-cursor="([^"]+)"', shown
                    )
                    assert marker and cursors
                    from urllib.parse import urlencode

                    newer = await page(
                        served,
                        boss,
                        html.unescape(marker.group(1))
                        + "?"
                        + urlencode({"after": html.unescape(cursors[-1])}),
                    )
                    assert Bubbles(newer).messages == ["在线端继续更新"]
