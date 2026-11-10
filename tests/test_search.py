"""Searching every agent's messages and the pictures in them, over real HTTP
with a real node: hits merged across agents and counted, a conversation
opened at its hit, pictures that came in read through signed addresses, and
pictures written in Markdown fetched only from the web."""

from __future__ import annotations

lazy import asyncio
lazy import html
lazy import re
lazy import struct
lazy import zlib
lazy from pathlib import Path

from computer import agent, computer, heard, say
from served import admin, client, names, opened, page, role, serving, through, until

KANA = agent("kana", "有马佳奈")
TIFA = agent("tifa", "Tifa")
YU = {"id": "u-yu", "name": "yuchanns", "display_name": "yuchanns"}
WANG = {"id": "u-wang", "name": "wanglei", "display_name": "王磊"}


def picture(path: Path, width: int, height: int) -> bytes:
    """A PNG of one colour, written to the path; its bytes."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    rows = b"".join(b"\x00" + b"\x2f\x81\xf7" * width for _ in range(height))
    content = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(content)
    return content


def hits(shown: str) -> list[str]:
    """What the search found, as the snippets read without their marks."""
    return [
        html.unescape(re.sub(r"<[^>]+>", "", snippet))
        for snippet in re.findall(r'<div class="sn">(.*?)</div>', shown, re.DOTALL)
    ]


async def test_the_search_merges_the_agents_and_opens_at_the_hit(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA, TIFA]
        ) as pc:
            await say(
                pc,
                "kana",
                "dev",
                YU,
                "这个 CI 为什么只跑了 Plan？",
                kind="group",
                title="#开发",
            )
            await heard(pc, "kana")
            for n in range(30):
                await say(
                    pc, "kana", "dev", YU, f"第 {n} 条闲聊", kind="group", title="#开发"
                )
            await say(
                pc,
                "tifa",
                "front",
                WANG,
                "CI 的 Plan 一直在排队",
                kind="group",
                title="#前端组",
            )
            await say(
                pc,
                "tifa",
                "front",
                WANG,
                "按钮的禁用态还要再调一下",
                kind="group",
                title="#前端组",
            )
            await heard(pc, "kana")
            await heard(pc, "tifa")
            found = await until(
                lambda: page(served, boss, "/ui/chats/?q=Plan"),
                lambda shown: len(hits(shown)) == 2,
            )
            assert sorted(names(found)) == ["#前端组", "#开发"]
            assert re.search(r"<b>2</b>", found)
            assert all("Plan" in hit for hit in hits(found))
            assert found.count("<mark>") == 2
            # the newest first when asked
            newest = await page(served, boss, "/ui/chats/?q=Plan&sort=time")
            assert names(newest) == ["#前端组", "#开发"]
            # kept to one conversation
            dev = opened(found, "#开发")
            within = await page(
                served, boss, f"/ui/chats/?q=Plan&in={dev.replace('/', '.')}"
            )
            assert names(within) == ["#开发"]

            # the conversation opens at the hit, with the way back to the latest
            hit = re.search(rf'href="(/ui/chats/{dev}\?[^"]*around=[^"]*)"', found)
            assert hit, found[:3000]
            at = await page(served, boss, html.unescape(hit.group(1)))
            centred = re.search(
                r'<div class="msg hit"[^>]*>(.*?)<div class="msg', at, re.DOTALL
            )
            assert centred and "只跑了 Plan" in centred.group(1)
            assert f'href="/ui/chats/{dev}?q=Plan"' in at
            latest = await page(served, boss, f"/ui/chats/{dev}")
            assert 'class="msg hit"' not in latest
            assert "第 29 条闲聊" in latest


async def test_pictures_come_through_signed_addresses(tmp_path: Path):
    async with serving() as served, client() as boss, client() as bob:
        await admin(served, boss)
        viewers = await role(served, boss, "agents.view")
        await boss.post(f"{served.url}{viewers}/default")
        await through(served, bob, "bob")
        async with computer(
            served, boss, tmp_path / "kana-vm", "kana-vm", [KANA]
        ) as pc:
            content = await asyncio.to_thread(
                picture, tmp_path / "checks.png", 300, 112
            )
            await say(
                pc,
                "kana",
                "dev",
                YU,
                "截图在这里，还有 ![外面的图](http://127.0.0.1:9/inside.png)",
                kind="group",
                title="#开发",
                attachments=[
                    {"path": str(tmp_path / "checks.png"), "media_type": "image/png"}
                ],
            )
            listing = await until(
                lambda: page(served, boss, "/ui/chats/"),
                lambda shown: names(shown) == ["#开发"],
            )
            address = opened(listing, "#开发")
            shown = await until(
                lambda: page(served, boss, f"/ui/chats/{address}"),
                lambda shown: "/ui/chats/files/" in shown,
            )
            image = re.search(r'<img src="(/ui/chats/files/[^"]+)"', shown)
            assert image, shown[:3000]
            source = html.unescape(image.group(1))
            fetched = await boss.get(f"{served.url}{source}")
            assert fetched.status_code == 200 and fetched.content == content
            assert fetched.headers["content-type"] == "image/png"
            assert fetched.headers["cache-control"] == "private, max-age=86400"
            assert fetched.headers["content-security-policy"] == "sandbox"
            assert fetched.headers["x-content-type-options"] == "nosniff"
            forged = re.sub(r"s=[0-9a-f]+", "s=" + "0" * 64, source)
            assert (await boss.get(f"{served.url}{forged}")).status_code == 403
            # bob sees no agent of kana-vm
            assert (await bob.get(f"{served.url}{source}")).status_code == 404

            # a picture written in Markdown comes only from the web
            outside = re.search(
                r'<img class="md-img" src="(/ui/chats/images\?[^"]+)"', shown
            )
            assert outside, shown[:3000]
            proxied = html.unescape(outside.group(1))
            assert (await boss.get(f"{served.url}{proxied}")).status_code == 502
            tampered = re.sub(r"s=[0-9a-f]+", "s=" + "0" * 64, proxied)
            assert (await boss.get(f"{served.url}{tampered}")).status_code == 403
