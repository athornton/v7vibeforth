"""Tests for V7.cmd() end-of-output detection.

These run against the fake v7 server in tests/support/v7server.py, over a
real telnet socket on localhost, so they need no emulator and no PDP-11.
Two properties of the real DZ11 line are what broke command framing, and
the fixture reproduces both:

  * output arrives a BYTE AT A TIME (on the real 9600-baud line, 28910 of
    28914 reads returned exactly one byte), so any needle we find is
    usually the last thing in the buffer, and
  * the tty echoes the command line back before the output.

None of these tests knows how cmd() builds its end-of-output marker; they
only assert on what the caller gets back.

Runnable two ways:
    python tests/cmd_test.py          (no pytest needed)
    pytest tests/cmd_test.py
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

# A file whose CONTENTS contain the shell prompt string '$ '.  This is not
# contrived: it is line 71 of the project's own c/bedit.c, and any C file
# documenting Forth's '$' word or using '$ ' in a comment will do it.  It
# also contains "not found", which broke _fetch's old error heuristic.
BEDIT_C = """\
/* bedit.c -- a VT100 full-screen editor for Forth blocks */
mv_eol()
{
        int c;
        ccol = 0;
        for (c = NC-1; c >= 0; c--)
                if (buf[crow][c] != ' ') { ccol = c; break; }
}

/* $ : last non-space column, or 0 if the line is blank */
mv_bol()   { ccol = 0; }

/* THIS IS THE LAST LINE -- if you cannot see it, output was truncated */
"""

NO_NEWLINE = "abc"

DEFAULT_FILES = {"bedit.c": BEDIT_C}


# --------------------------------------------------------------------
# tests
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_command_still_works() -> None:
    """A command with no '$ ' in its output must be unaffected."""
    async with logged_in(char_delay=0) as (v, _):
        out = await v.cmd("echo hello", timeout=15)
        assert "hello" in out, f"lost the output: {out!r}"


@pytest.mark.asyncio
async def test_output_containing_prompt_is_not_truncated() -> None:
    """The reported bug: '$ ' inside the DATA ended the command early."""
    async with logged_in(DEFAULT_FILES) as (v, _):
        out = await v.cmd("cat bedit.c", timeout=30)
        assert "THIS IS THE LAST LINE" in out, (
            "output truncated at the '$ ' inside the file; got "
            f"{len(out)} bytes, ending {out[-40:]!r}"
        )


@pytest.mark.asyncio
async def test_no_residue_leaks_into_the_next_command() -> None:
    """The second half of the bug: leftovers surfacing as later output."""
    async with logged_in(DEFAULT_FILES) as (v, _):
        await v.cmd("cat bedit.c", timeout=30)
        out = await v.cmd("echo MARKER1", timeout=15)
        assert "MARKER1" in out, f"follow-up lost its own output: {out!r}"
        for leaked in ("mv_eol", "THIS IS THE LAST LINE", "last non-space"):
            assert leaked not in out, (
                f"previous command's output leaked into this one ({leaked!r} "
                f"seen in {out[:120]!r})"
            )


@pytest.mark.asyncio
async def test_file_without_trailing_newline() -> None:
    """The marker must not be mistaken for file content when they join."""
    async with logged_in({"nonl": NO_NEWLINE}, char_delay=0) as (v, _):
        out = await v.cmd("cat nonl", timeout=15)
        assert out.rstrip().endswith("abc"), (
            "marker not cleanly stripped from a file with no final newline: "
            f"{out[-40:]!r}"
        )


@pytest.mark.asyncio
async def test_fetch_roundtrips_a_file_containing_a_prompt() -> None:
    """_fetch() (the 'get' verb) must return the file byte-for-byte."""
    async with logged_in(DEFAULT_FILES) as (v, _):
        body = await v._fetch("bedit.c")
        assert body == BEDIT_C, (
            "downloaded file does not match the original\n"
            f"  got  {len(body or '')} bytes\n"
            f"  want {len(BEDIT_C)} bytes"
        )


@pytest.mark.asyncio
async def test_fetch_of_missing_file_returns_none() -> None:
    """A genuinely absent file must still be reported as a failure."""
    async with logged_in(char_delay=0) as (v, _):
        assert await v._fetch("nosuchfile") is None, (
            "a missing file was reported as a successful download"
        )


@pytest.mark.asyncio
async def test_fetch_file_containing_error_text() -> None:
    """A file whose CONTENTS mention 'not found' must download fine.

    c/bedit.c does exactly this (strc(msg,"not found")), and the old
    body-scanning heuristic made it impossible to fetch.
    """
    body = 'strc(msg, "not found");\nstrc(msg, "can\'t open it");\n'
    async with logged_in({"editor.c": body}, char_delay=0) as (v, _):
        got = await v._fetch("editor.c")
        assert got == body, (
            "a file that merely MENTIONS an error string was misreported as "
            f"a failed download: got {got!r}"
        )


async def _main() -> int:
    """Run every test_* coroutine without needing pytest installed."""
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            await asyncio.wait_for(t(), timeout=90)
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
