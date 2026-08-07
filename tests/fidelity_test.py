"""Differential test: does the fake v7 answer exactly like the real one.

This is the test that keeps tests/support/v7server.py honest.  It runs the
same probes against the fixture and against a real v7 under simh, then
demands the answers be byte-identical.

It is SKIPPED unless a real machine is reachable, so the normal suite needs
no emulator:

    V7_REAL_HOST=127.0.0.1 V7_REAL_PORT=1145 \
    V7_REAL_USER=claude V7_REAL_PASSWORD=daisies \
        python tests/fidelity_test.py

Why bother: an earlier hand-rolled fake in this project modelled a MODERN
`cat` (nonzero exit on a missing file).  Tests passed against it while the
real PDP-11 still failed, because v7's cat exits 0.  A fixture that lies is
worse than no fixture, and only a differential test catches the lie.  When
this test caught a real divergence it was `sum`'s column layout -- the fake
printed '65456    1' where v7 prints '65456     1'.

Note this is SLOW against the real machine: a 9600-baud line delivers about
960 bytes/sec, so keep the probe list short and the probe file tiny.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path

import pytest
import rich

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from support.v7server import V7Server

from v7.v7 import V7

# Keep this file SMALL: every byte costs ~1 ms on the real line.
PROBE_NAME = "probe.txt"
PROBE_BODY = "hello world\nsecond line\n"

# Each probe must be deterministic and independent of the machine's own
# state (no dates, no pids, no directory listings).
PROBES = (
    "echo hi",
    # v7's cat exits 0 even when it cannot open the file.
    "cat /nosuchfile; echo rc$?",
    # test -f is the reliable existence check.
    f"test -f {PROBE_NAME}; echo rc$?",
    "test -f /nosuchfile; echo rc$?",
    # An intervening echo resets $?; stashing it first preserves it.
    "false; echo -n MK; echo rc$?",
    "false; s=$?; echo -n MK; echo rc$s",
    # Column layouts and the Can't/can't capitalisation difference.
    f"sum {PROBE_NAME}",
    f"wc -c {PROBE_NAME}",
    "sum /nosuchfile",
    "wc -c /nosuchfile",
    "frobnicate",
    "pwd",
    f"cat {PROBE_NAME}",
)


def real_target() -> tuple[str, int, str, str] | None:
    """Return (host, port, user, password) if a real v7 was configured."""
    host = os.environ.get("V7_REAL_HOST")
    port = os.environ.get("V7_REAL_PORT")
    user = os.environ.get("V7_REAL_USER")
    password = os.environ.get("V7_REAL_PASSWORD")
    if not (host and port and user and password):
        return None
    return host, int(port), user, password


async def probe_all(v: V7) -> dict[str, str]:
    """Run every probe, returning {probe: output with CRs stripped}."""
    out = {}
    for p in PROBES:
        raw = await asyncio.wait_for(v.cmd(p, timeout=30), timeout=60)
        out[p] = raw.replace("\r", "").strip()
    return out


async def against_fake() -> dict[str, str]:
    async with V7Server(files={PROBE_NAME: PROBE_BODY}, char_delay=0) as srv:
        v = V7(
            user=srv.user,
            password=srv.password,
            host="127.0.0.1",
            port=srv.port,
        )
        await v.connect()
        try:
            return await probe_all(v)
        finally:
            await v.close()


async def against_real(
    host: str, port: int, user: str, password: str
) -> dict[str, str] | None:
    for _ in range(10):
        v = V7(user=user, password=password, host=host, port=port)
        # A busy DZ11 line just fails to give us a login prompt; retry.
        with contextlib.suppress(Exception):
            await v.connect()
        if v.writer is not None:
            try:
                # Make sure the probe file exists and matches byte for byte.
                await v.put_verified(PROBE_NAME, PROBE_BODY, pace=0.02)
                return await probe_all(v)
            finally:
                await v.close()
        await asyncio.sleep(3)
    return None


@pytest.mark.asyncio
async def test_fake_matches_real() -> None:
    """Every probe must produce identical output on both systems."""
    target = real_target()
    if target is None:
        rich.print(
            "[yellow]SKIP[/yellow]: set V7_REAL_HOST/PORT/USER/PASSWORD "
            "to check the fixture against a real v7"
        )
        return
    fake = await against_fake()
    live = await against_real(*target)
    assert live is not None, (
        f"could not reach the real v7 at {target[0]}:{target[1]}"
    )
    diffs = [p for p in PROBES if fake[p] != live[p]]
    for p in diffs:
        rich.print(f"[red]DIFF[/red] {p!r}")
        rich.print(f"     fake: {fake[p]!r}")
        rich.print(f"     real: {live[p]!r}")
    assert not diffs, (
        f"the fixture disagrees with the real v7 on {len(diffs)} of "
        f"{len(PROBES)} probes: {diffs}.  Fix the FIXTURE to match the "
        "machine -- a fake that lies makes the whole suite worthless."
    )
    rich.print(f"all {len(PROBES)} probes identical on fake and real")


async def _main() -> int:
    """Run every test_* coroutine without needing pytest installed."""
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            await asyncio.wait_for(t(), timeout=900)
        except AssertionError as e:
            failed += 1
            rich.print(f"[red]FAIL[/red]: {name}\n      {e}")
        except Exception as e:
            failed += 1
            rich.print(
                f"[red]ERROR[/red]: {name}\n       {type(e).__name__}: {e}"
            )
        else:
            rich.print(f"ok: {name}")
    rich.print(f"\n{len(tests)} run, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
