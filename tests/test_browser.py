"""The conversations in a real browser, wide and on a phone, with a real
node: a conversation opened from the list with its picture and what it is
about, a first contact let in after its auto-reply is changed, another
agent's conversation revoked, a search opened at its hit, the agent's own
conversations on its page, and what needs a decision on the overview; and
a long conversation read upward, its older messages coming in above the one
read without moving it."""

from __future__ import annotations

lazy import asyncio
lazy import re
lazy from pathlib import Path

import pytest
from computer import agent, computer, heard, say
from playwright.async_api import async_playwright, expect
from served import admin, client, names, page, serving, until
from test_search import picture

KANA = agent("kana", "有马佳奈")
TIFA = agent("tifa", "Tifa")
YU = {"id": "u-yu", "name": "yuchanns", "display_name": "yuchanns"}
WANG = {"id": "u-wang", "name": "wanglei", "display_name": "王磊"}
LI = {"id": "u-li", "name": "lilei", "display_name": "李雷"}
CHEN = {"id": "u-chen", "name": "chenchen", "display_name": "陈晨"}


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["wide", "phone"])
async def test_the_conversations_read_and_decide_as_the_design_shows(
    tmp_path: Path, size: tuple[int, int]
):
    wide = size[0] >= 960
    async with (
        serving() as served,
        client() as boss,
        async_playwright() as driver,
    ):
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA, TIFA]
        ) as pc:
            await asyncio.to_thread(picture, tmp_path / "checks.png", 300, 112)
            await say(
                pc,
                "tifa",
                "front",
                WANG,
                "CI 的 Plan 一直在排队",
                kind="group",
                title="#前端组",
            )
            await say(pc, "kana", "yuchanns", YU, "明早 9 点提醒我看 review")
            await say(
                pc,
                "kana",
                "dev",
                YU,
                "这个 CI 为什么只跑了 Plan？",
                kind="group",
                title="#bazaar-compute 开发",
                attachments=[
                    {"path": str(tmp_path / "checks.png"), "media_type": "image/png"}
                ],
            )
            await heard(pc, "kana")
            await heard(pc, "tifa")
            await say(
                pc,
                "tifa",
                "lilei",
                LI,
                "你好，我是新来的同事，想请你帮我搭一下开发环境",
                review="pending",
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
            await until(
                lambda: page(served, boss, "/ui/chats/?tab=contacts"),
                lambda shown: sorted(names(shown)) == ["李雷", "设计组"],
            )
            browser = await driver.chromium.launch()
            try:
                context = await browser.new_context(
                    locale="zh-CN", viewport={"width": size[0], "height": size[1]}
                )
                await context.add_cookies(
                    [
                        {"name": c.name, "value": c.value or "", "url": served.url}
                        for c in boss.cookies.jar
                    ]
                )
                tab = await context.new_page()
                await tab.goto(f"{served.url}/chats")
                shown = tab.frame_locator("iframe.frame")
                rows = shown.locator(".pane.list .conv")
                await expect(rows).to_have_count(3)
                # the sidebar's badge and the phone's bar count the contacts waiting
                await expect(tab.locator(".nav[href='/chats'] .tally")).to_have_text(
                    "2"
                )
                messages = shown.locator(".msgs .msg")
                if wide:
                    # the newest opens beside the list
                    newest = await rows.first.locator(".tn").inner_text()
                    await expect(shown.locator(".t-hd h3")).to_have_text(newest)
                else:
                    await expect(messages).to_have_count(0)
                dev = '.pane.list .conv:has(.tn:text-is("#bazaar-compute 开发"))'
                # opened in place, the page that listed it still the one shown
                listing = next(one for one in tab.frames if "/ui/chats/" in one.url)
                await listing.evaluate("window.kept = true")
                await shown.locator(dev).click()
                await expect(messages).to_have_count(1)
                assert await listing.evaluate("window.kept === true")
                if wide:
                    # what it is about, beside it
                    await expect(shown.locator(".info .member")).to_have_count(1)
                picture_shown = shown.locator(".msg .shot img")
                await expect(picture_shown).to_have_count(1)
                await expect(picture_shown).to_have_js_property("naturalWidth", 300)
                if not wide:
                    await shown.locator(".thread-bar a.icon-btn").click()
                    await expect(shown.locator(".info .member")).to_have_count(1)
                    await shown.locator(".top .back").click()
                    await expect(tab).to_have_url(re.compile(r"/chats/\d+/\d+$"))
                    # its own search keeps to it, as 0.2's did
                    await shown.locator(".thread-bar button[data-within]").click()
                    within = shown.locator(".top .bar input[name=q]")
                    await within.fill("Plan")
                    await within.press("Enter")
                    await expect(shown.locator(".hits .conv")).to_have_count(1)

                # a first contact, let in once what tifa says meanwhile is changed
                await tab.goto(f"{served.url}/chats/?tab=contacts")
                await shown.locator(".pane.list .conv", has_text="李雷").click()
                review = shown.locator(".review")
                await expect(review).to_contain_text("李雷")
                reply = shown.locator(".autoreply")
                await reply.locator(".more").click()
                await reply.locator("textarea").fill("你好，我先确认一下，稍后回复你。")
                await reply.locator("button[type=submit]").click()
                await expect(shown.locator(".autoreply .bubble")).to_contain_text(
                    "稍后回复你"
                )
                await review.locator("button[value=approved]").click()
                await expect(tab).to_have_url(re.compile(r"/chats/\d+/\d+$"))
                await expect(shown.locator(".msgs .msg")).to_have_count(1)
                assert "李雷" in names(await page(served, boss, "/ui/chats/"))

                # yuchanns's conversation with kana, revoked from what it is about
                await tab.goto(f"{served.url}/chats")
                await shown.locator(
                    '.pane.list .conv:has(.tn:text-is("yuchanns"))'
                ).click()
                if wide:
                    await expect(shown.locator(".t-hd h3")).to_have_text("yuchanns")
                else:
                    await expect(shown.locator(".thread-bar b")).to_have_text(
                        "yuchanns"
                    )
                    await shown.locator(".thread-bar a.icon-btn").click()
                await shown.locator(".approval .undo").click()
                question = shown.locator(".pop")
                await expect(question).to_be_visible()
                await question.locator("button.danger").click()
                await expect(tab).to_have_url(re.compile(r"/chats/?$"))
                await expect(
                    shown.locator('.pane.list .conv:has(.tn:text-is("yuchanns"))')
                ).to_have_count(0)

                # a search through both agents, opened at what it found
                if wide:
                    finder = shown.locator(".top .finder input[name=q]")
                else:
                    await shown.locator(".top .bar .icon-btn").click()
                    finder = shown.locator(".top .bar input[name=q]")
                await finder.fill("Plan")
                await finder.press("Enter")
                found = shown.locator(".hits .conv")
                await expect(found).to_have_count(2)
                await expect(found.locator("mark")).to_have_count(2)
                await found.filter(has_text="#bazaar-compute 开发").click()
                await expect(shown.locator(".msg.hit")).to_contain_text("只跑了 Plan")
                await expect(shown.locator(".msg.hit mark")).to_have_count(1)

                # the agent's own conversations on its page
                kana = re.search(
                    r'<option value="(\d+)"[^>]*>有马佳奈<',
                    await page(served, boss, "/ui/chats/"),
                )
                assert kana
                await tab.goto(f"{served.url}/agents/{kana.group(1)}?tab=chats")
                pane = tab.frame_locator("iframe.frame").frame_locator("iframe.pane")
                await expect(pane.locator(".crow")).to_have_count(1)
                await expect(pane.locator(".attn-item")).to_have_count(1)

                # what needs a decision, on the overview
                await tab.goto(f"{served.url}/")
                attention = tab.frame_locator("iframe.frame").frame_locator(
                    "iframe[src='/ui/chats/attention']"
                )
                await expect(attention.locator(".attn-item")).to_have_count(1)
                await expect(attention.locator(".attn-item")).to_contain_text("设计组")
            finally:
                await browser.close()


async def test_older_messages_come_in_above_the_one_read(tmp_path: Path):
    async with (
        serving() as served,
        client() as boss,
        async_playwright() as driver,
    ):
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA]
        ) as pc:
            for number in range(90):
                await say(pc, "kana", "yuchanns", YU, f"第 {number} 条")
            await heard(pc, "kana")
            await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: names(shown) == ["yuchanns"],
            )
            browser = await driver.chromium.launch()
            try:
                context = await browser.new_context(
                    locale="zh-CN", viewport={"width": 1440, "height": 900}
                )
                await context.add_cookies(
                    [
                        {"name": c.name, "value": c.value or "", "url": served.url}
                        for c in boss.cookies.jar
                    ]
                )
                tab = await context.new_page()
                await tab.goto(f"{served.url}/chats")
                shown = tab.frame_locator("iframe.frame")
                messages = shown.locator(".msgs .msg[data-id]")
                await expect(messages).not_to_have_count(0)
                first = await messages.count()
                # read up to the first message shown: the older ones come in
                # above it, and it stays in view
                read = shown.locator(
                    f'.msg[data-id="{await messages.first.get_attribute("data-id")}"]'
                )
                await shown.locator(".msgs").evaluate(
                    "messages => { messages.scrollTop = 0; }"
                )
                await expect(messages).not_to_have_count(first)
                await expect(read).to_be_in_viewport()
            finally:
                await browser.close()
