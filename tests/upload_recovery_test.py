"""Tests that a sick line during upload is RETRIED, not fatal.

put_verified exists to survive a line that wedges mid-transfer: it checks
every chunk against v7's own `sum`, and on any failure it calls _resync()
(which clears a stuck cat, or reconnects and logs in again if the line is
dead) and tries the chunk again.

The bug these tests pin: a wedged line does not always announce itself the
same way.  _cat_chunk raises RuntimeError when it spots 'login:' in the
echo, but when the far end simply stops answering, the failure surfaces as
a TimeoutError out of expect() instead.  TimeoutError is an OSError, NOT a
RuntimeError, so a retry loop that catches only RuntimeError lets it escape
and aborts the whole upload with a traceback -- reliably at chunk 20 of
c/forth.c on the real machine.

Runnable two ways:
    python tests/upload_recovery_test.py
    pytest tests/upload_recovery_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import rich

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from support.v7server import logged_in

CONTENT = "".join(f"line {i} of a multi-chunk upload\n" for i in range(150))


@pytest.mark.asyncio
async def test_wedged_line_is_retried_not_raised() -> None:
    """A stalled upload must be retried, and must ultimately succeed.

    The fixture swallows the first `cat >` without ever echoing the closing
    prompt, so the client sees a TimeoutError exactly where the real
    machine produced one.  put_verified must absorb that, _resync(), and
    get the file there on a later attempt.
    """
    async with logged_in(char_delay=0) as (v, srv):
        srv.wedge_uploads = 1
        try:
            ok = await asyncio.wait_for(
                v.put_verified("out.txt", CONTENT, chunk_lines=60, pace=0),
                timeout=120,
            )
        except TimeoutError as e:
            msg = (
                "put_verified let a TimeoutError escape instead of retrying "
                f"the chunk: {e}.  TimeoutError is an OSError, not a "
                "RuntimeError -- the retry loop has to catch both."
            )
            raise AssertionError(msg) from e
        assert ok, "put_verified gave up on a line that recovered"
        assert srv.files.get("out.txt") == CONTENT, (
            "file is wrong after recovery:\n"
            f"  got  {len(srv.files.get('out.txt', ''))} bytes\n"
            f"  want {len(CONTENT)} bytes"
        )


@pytest.mark.asyncio
async def test_permanently_wedged_line_fails_cleanly() -> None:
    """If every attempt stalls, report False -- don't raise, don't hang.

    A caller (the REPL's `transmit` verb) needs a usable answer even when
    the line never recovers.
    """
    async with logged_in(char_delay=0) as (v, srv):
        srv.wedge_uploads = 99  # never recovers
        try:
            ok = await asyncio.wait_for(
                v.put_verified(
                    "out.txt", CONTENT, chunk_lines=60, pace=0, tries=2
                ),
                timeout=180,
            )
        except TimeoutError as e:
            msg = f"a permanently wedged line raised instead of returning: {e}"
            raise AssertionError(msg) from e
        assert ok is False, (
            f"a permanently wedged upload reported success ({ok!r})"
        )


@pytest.mark.asyncio
async def test_pace_covers_the_whole_echo() -> None:
    """The per-line pause must cover the full echo, else backlog builds.

    This is the arithmetic that made chunk 20 of forth.c wedge: waiting a
    fraction of the echo time leaves a deficit on every line, and the
    deficit accumulates across a 60-line chunk until the tty's silo
    overflows.
    """
    async with logged_in(char_delay=0) as (v, _):
        v.baud = 9600.0
        char_time = 10 / 9600
        for length in (10, 45, 72, 100):
            line = "x" * length
            # the tty echoes the line plus CR and LF
            echo_time = (length + 2) * char_time
            assert v.line_pace(line) >= echo_time, (
                f"a {length}-char line needs {echo_time * 1000:.1f} ms of "
                f"echo but line_pace waits only "
                f"{v.line_pace(line) * 1000:.1f} ms -- the shortfall "
                "accumulates and wedges the line mid-upload"
            )


@pytest.mark.asyncio
async def test_pace_still_scales_with_speed() -> None:
    """Covering the echo must not mean ignoring the measured rate."""
    async with logged_in(char_delay=0) as (v, _):
        line = "x" * 60
        v.baud = 9600.0
        fast = v.line_pace(line)
        v.baud = 300.0
        slow = v.line_pace(line)
        ratio = slow / fast
        assert 30 < ratio < 34, (
            f"300 vs 9600 baud should scale the pause ~32x, got {ratio:.1f}x"
        )


def _main() -> int:
    """Run every test_* coroutine without needing pytest installed."""
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            asyncio.run(asyncio.wait_for(t(), timeout=240))
        except AssertionError as e:
            failed += 1
            rich.print(f"[red]FAIL[/red]: {name}\n      {e}")
        except Exception as e:
            failed += 1
            kind = type(e).__name__
            rich.print(f"[red]ERROR[/red]: {name}\n       {kind}: {e}")
        else:
            rich.print(f"ok: {name}")
    rich.print(f"\n{len(tests)} run, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
