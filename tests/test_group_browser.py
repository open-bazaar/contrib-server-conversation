"""Grouped chats, their sources and late duplicate messages in a browser."""

from __future__ import annotations

lazy from pathlib import Path
lazy from time import time_ns

import pytest
from computer import agent, computer, heard, say
from playwright.async_api import async_playwright, expect
from served import admin, client, names, page, serving, until


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["wide", "phone"])
async def test_a_group_shows_its_bots_and_manages_each_source(
    tmp_path: Path, size: tuple[int, int]
):
    async with serving() as served, client() as boss, async_playwright() as driver:
        await admin(served, boss)
        async with computer(
            served,
            boss,
            tmp_path / "node",
            "node",
            [agent("alpha", "青禾"), agent("beta", "小雨")],
        ) as pc:
            person = {"id": "person", "name": "lin", "display_name": "林"}
            at_ms = time_ns() // 1_000_000 - 10_000
            for owner in ("alpha", "beta"):
                await say(
                    pc,
                    owner,
                    "room",
                    person,
                    "请一起整理本周安排",
                    kind="group",
                    title="协作群",
                    provider_message_id="first",
                    at_ms=at_ms,
                )
            await say(
                pc,
                "beta",
                "room",
                {"id": "alpha-alpha-bot", "display_name": "平台名 A", "kind": "agent"},
                "我整理待办",
                kind="group",
                title="协作群",
                provider_message_id="reply-a",
                at_ms=at_ms + 100,
            )
            await say(
                pc,
                "alpha",
                "room",
                {"id": "beta-beta-bot", "display_name": "平台名 B", "kind": "agent"},
                "我补充时间安排",
                kind="group",
                title="协作群",
                provider_message_id="reply-b",
                at_ms=at_ms + 200,
            )
            await heard(pc, "alpha")
            await heard(pc, "beta")
            await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: names(shown) == ["协作群"],
            )
            browser = await driver.chromium.launch()
            try:
                context = await browser.new_context(
                    locale="zh-CN", viewport={"width": size[0], "height": size[1]}
                )
                await context.add_cookies(
                    [
                        {
                            "name": cookie.name,
                            "value": cookie.value or "",
                            "url": served.url,
                        }
                        for cookie in boss.cookies.jar
                    ]
                )
                tab = await context.new_page()
                errors: list[str] = []
                tab.on("pageerror", lambda error: errors.append(str(error)))
                await tab.goto(f"{served.url}/chats")
                shown = tab.frame_locator("iframe.frame")
                await expect(shown.locator(".convs .conv")).to_have_count(1)
                await shown.locator(".convs .conv").click()
                await expect(shown.locator(".msgs .bubble")).to_have_count(3)
                await expect(shown.locator(".msgs .who b")).to_have_text(
                    ["林", "青禾", "小雨"]
                )

                # A second observation arrives after the first is already
                # displayed, with a different local provider time.
                await say(
                    pc,
                    "alpha",
                    "room",
                    person,
                    "新增的安排",
                    kind="group",
                    title="协作群",
                    provider_message_id="late",
                    at_ms=at_ms + 300,
                )
                await expect(
                    shown.locator(".msgs .bubble", has_text="新增的安排")
                ).to_have_count(1)
                await say(
                    pc,
                    "beta",
                    "room",
                    person,
                    "新增的安排",
                    kind="group",
                    title="协作群",
                    provider_message_id="late",
                    at_ms=at_ms + 500,
                )
                await say(
                    pc,
                    "beta",
                    "room",
                    person,
                    "已经补充完毕",
                    kind="group",
                    title="协作群",
                    provider_message_id="after-late",
                    at_ms=at_ms + 600,
                )
                await expect(
                    shown.locator(".msgs .bubble", has_text="已经补充完毕")
                ).to_have_count(1)
                await expect(
                    shown.locator(".msgs .bubble", has_text="新增的安排")
                ).to_have_count(1)
                if size[0] >= 960:
                    await expect(shown.locator(".convs .conv.on")).to_have_count(1)
                if size[0] < 960:
                    await shown.locator(".thread-bar a.icon-btn").click()
                sources = shown.locator(".info a.member")
                await expect(sources).to_have_count(2)
                await sources.filter(has_text="青禾").click()
                if size[0] < 960:
                    await shown.locator(".thread-bar a.icon-btn").click()
                await expect(shown.locator(".approval")).to_be_visible()
                assert "agent=" in tab.url
                assert not errors, errors
            finally:
                await browser.close()
