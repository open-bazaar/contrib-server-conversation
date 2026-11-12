"""A long search on a real node: pages still load, only nearby hits are
mounted, and returning to the first hit restores it without another query."""

from __future__ import annotations

lazy from pathlib import Path

import pytest
from computer import agent, computer, heard, say
from playwright.async_api import async_playwright, expect
from served import admin, client, page, serving, until


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["wide", "phone"])
async def test_search_windows_loaded_hits_and_restores_them(
    tmp_path: Path, size: tuple[int, int]
):
    async with serving() as served, client() as boss, async_playwright() as driver:
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "node", "search", [agent("kana", "有马佳奈")]
        ) as pc:
            for number in range(180):
                await say(
                    pc,
                    "kana",
                    "dev",
                    {"id": "yu", "name": "yuchanns", "display_name": "yuchanns"},
                    f"Plan 第 {number} 项的检查结果已经到齐，请帮忙看看是否可以合并。",
                    kind="group",
                    title="#开发",
                )
            await heard(pc, "kana")
            await until(
                lambda: page(served, boss, "/ui/chats/?q=Plan"),
                lambda shown: "<b>180</b>" in shown,
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
                errors = []
                requests = []
                tab.on("pageerror", lambda error: errors.append(str(error)))
                tab.on(
                    "request",
                    lambda request: (
                        requests.append(request.url)
                        if "/ui/chats/hits?" in request.url
                        else None
                    ),
                )
                await tab.goto(f"{served.url}/chats")
                shown = tab.frame_locator("iframe.frame")
                if size[0] < 960:
                    await shown.locator(".top a[aria-label='搜索消息']").click()
                search = shown.locator("input[name=q]:visible")
                await search.fill("Plan")
                await search.press("Enter")
                await expect(shown.locator(".sr-hd .count")).to_contain_text("180")
                hits = shown.locator(".hits .conv")
                first = await hits.first.get_attribute("href")
                text = await hits.first.inner_text()
                root = shown.locator(".hits" if size[0] >= 960 else ".panes")
                more = shown.locator(".hits .more-rows")
                maximum = await hits.count()
                while await more.count():
                    address = await more.get_attribute("hx-get")
                    await root.evaluate(
                        "root => { root.scrollTop = root.scrollHeight; }"
                    )
                    await expect(
                        shown.locator(f'.hits .more-rows[hx-get="{address}"]')
                    ).to_have_count(0)
                    maximum = max(maximum, await hits.count())
                # all three further pages came through the existing paging link;
                # their rows have not accumulated in the page
                assert requests
                assert maximum < 50
                restored = shown.locator(f'.hits .conv[href="{first}"]')
                await expect(restored).to_have_count(0)
                loaded = len(requests)
                await root.evaluate("root => { root.scrollTop = 0; }")
                await expect(restored).to_be_in_viewport()
                await expect(restored).to_have_text(text)
                assert len(requests) == loaded
                # The same row is kept when the list moves between its own
                # scrollbar and the phone page's scrollbar.
                await root.evaluate(
                    "root => { root.scrollTop = root.scrollHeight / 2; }"
                )
                await expect(restored).to_have_count(0)
                reading = await root.evaluate("""root => {
                    const top = root.getBoundingClientRect().top + root.clientTop;
                    const row = [...document.querySelectorAll('.hits .conv')].find(
                        row => row.getBoundingClientRect().bottom > top
                    );
                    return {href: row.getAttribute('href'), offset: row.getBoundingClientRect().top - top};
                }""")
                await tab.set_viewport_size(
                    {"width": 390 if size[0] >= 960 else 1440, "height": 844}
                )
                await expect(shown.locator("html")).to_have_attribute(
                    "data-layout", "narrow" if size[0] >= 960 else "wide"
                )
                kept = shown.locator(f'.hits .conv[href="{reading["href"]}"]')
                await expect(kept).to_be_in_viewport()
                await tab.set_viewport_size({"width": size[0], "height": size[1]})
                await expect(shown.locator("html")).to_have_attribute(
                    "data-layout", "wide" if size[0] >= 960 else "narrow"
                )
                await root.evaluate("root => { root.scrollTop = 0; }")
                await expect(restored).to_be_in_viewport()
                await restored.click()
                await expect(shown.locator(".msgs .hit")).to_be_in_viewport()
                # a different search starts a different list, including an empty one
                if size[0] < 960:
                    await shown.locator(".top .back").click()
                search = shown.locator("input[name=q]:visible")
                await search.fill("没有出现的词语")
                await search.press("Enter")
                await expect(shown.locator(".hits .none")).to_be_visible()
                await expect(hits).to_have_count(0)
                assert not errors, errors
            finally:
                await browser.close()
