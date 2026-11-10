"""A computer whose node lacks the conversations has them installed by its
manager the first time they are read: a node of its own installation from
PyPI, its catalog naming where bazaar-contrib-starlink-conversation comes
from - first a source without it, then PyPI."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy import json
lazy import os
lazy import re
lazy import shutil
lazy import sys
lazy from collections.abc import AsyncIterator
lazy from pathlib import Path

from computer import TEAHOUSE, Computer, agent, configure, linked, say
from served import admin, client, enrol, names, page, serving

KANA = agent("kana", "有马佳奈")
YU = {"id": "u-yu", "name": "yuchanns", "display_name": "yuchanns"}
# the node's distribution whose sidecar serves what its manager adds at once
HOST = "bazaar-compute[node]~=0.3.0rc25"
# long enough for a failed install to be tried again and a good one to finish,
# both within the login the test holds
WAIT_SECONDS = 240


async def _run(*arguments: str) -> None:
    process = await asyncio.create_subprocess_exec(
        *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode()


async def installation(root: Path) -> tuple[str, str]:
    """A node installed by itself, with the teahouse built into a package
    source of its own; its interpreter and that source."""
    fixture = (await asyncio.to_thread(Path(__file__).resolve)).parent / "fixtures"
    wheels = root / "wheels"
    await _run(
        "uv", "build", str(fixture / "teahouse"), "--wheel", "--out-dir", str(wheels)
    )
    (wheel,) = await asyncio.to_thread(lambda: list(wheels.glob("*.whl")))
    listing = root / "simple" / "teahouse"
    await asyncio.to_thread(listing.mkdir, parents=True)
    await asyncio.to_thread(
        (listing / "index.html").write_text,
        f'<a href="{wheel.as_uri()}">{wheel.name}</a>',
    )
    prefix = root / "node"
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # the interpreter the tests run on, as uv knows none of its own here
    await _run("uv", "venv", "--python", sys.executable, str(prefix))
    await _run(
        "uv",
        "pip",
        "install",
        "--python",
        str(python),
        HOST,
        "bazaar-contrib-starlink~=0.3.0rc2",
        "blockbuster>=1.5.29,<1.6",
        str(wheel),
    )
    return str(python), (root / "simple").as_uri() + "/"


@asynccontextmanager
async def running(
    config: Path, python: str, environment: dict[str, str]
) -> AsyncIterator[asyncio.subprocess.Process]:
    """The node run by its own command with nothing on its standard input,
    ended at the close: on Windows the uv its manager starts inherits a
    pending read of that input, as run_node.py keeps one, and stalls."""
    output = config.with_suffix(".log")
    log = await asyncio.to_thread(output.open, "wb")
    process = await asyncio.create_subprocess_exec(
        python,
        "-m",
        "bazaar_compute_node",
        "--config",
        str(config),
        "run",
        env=environment,
        stdin=asyncio.subprocess.DEVNULL,
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
            process.terminate()
            await process.wait()


async def test_a_computer_without_the_conversations_has_them_installed(
    tmp_path: Path,
):
    python, source = await installation(tmp_path / "installed")
    empty = tmp_path / "empty"
    await asyncio.to_thread(empty.mkdir)
    catalog = tmp_path / "catalog.json"

    async def offer(where: str) -> None:
        await asyncio.to_thread(
            catalog.write_text,
            json.dumps(
                {
                    "contribs": [
                        {
                            "package": "bazaar-contrib-starlink-conversation",
                            "source": where,
                        }
                    ]
                }
            ),
        )

    # the source the catalog names first has no such package
    await offer(empty.as_uri() + "/")
    # the manager stages environments deep under the home: on Windows one more
    # level than the base directory runs past the path limit
    home = tmp_path
    uv = Path(str(await asyncio.to_thread(shutil.which, "uv")))
    environment = dict(os.environ) | {
        "BCN_HOME": str(home),
        "HOME": str(home),
        # the node's manager runs uv, beside the node's own interpreter
        "PATH": os.pathsep.join(
            (str(Path(python).parent), str(uv.parent), os.environ["PATH"])
        ),
    }
    async with serving() as served, client() as boss:
        await admin(served, boss)
        ref, token = await enrol(served, boss, "kana-vm")
        config = await configure(
            home,
            python,
            environment,
            [KANA],
            TEAHOUSE | {"source": source},
            linked(served, token),
            indexes=[str(catalog)],
        )
        async with running(config, python, environment) as node:
            pc = Computer(ref=ref, id=token.partition(":")[0], home=home, node=node)
            await say(pc, "kana", "yuchanns", YU, "早上好")
            seen: set[str] = set()

            async def watch(done: str) -> str:
                shown = ""
                try:
                    async with asyncio.timeout(WAIT_SECONDS):
                        while True:
                            shown = await page(served, boss, "/ui/chats/")
                            seen.update(
                                re.findall(
                                    r'<div class="notice">\s*<p class="(\w+)"', shown
                                )
                            )
                            if re.search(done, shown):
                                return shown
                            await asyncio.sleep(0.5)
                except TimeoutError:
                    # what the page and the node said instead
                    notice = re.search(
                        r'<div class="notice">.*?</div>', shown, re.DOTALL
                    )
                    log = await asyncio.to_thread(
                        config.with_suffix(".log").read_text, errors="replace"
                    )
                    operations = await asyncio.to_thread(
                        lambda: [
                            path.read_text(errors="replace")
                            for path in home.glob(".bcn/contrib/**/operations/*.json")
                        ]
                    )
                    raise AssertionError(
                        f"{done} never came; notices {sorted(seen)}; last "
                        f"{notice.group(0) if notice else None}; operations "
                        f"{operations}; node log:\n{log[-6000:]}"
                    ) from None

            failed = await watch(r'<p class="failed">')
            assert "enabling" in seen
            assert "bazaar-contrib-starlink-conversation" in failed
            # the catalog now finds it on PyPI; a later read installs it again
            await offer("https://pypi.org/simple/")
            seen.clear()
            listed = await watch(r'class="tn">yuchanns<')
            assert "enabling" in seen
            assert names(listed) == ["yuchanns"]
            assert '<div class="notice">' not in listed
            assert node.returncode is None
