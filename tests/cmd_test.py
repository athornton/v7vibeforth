"""Tests for V7.cmd() end-of-output detection.

These run against a tiny fake v7 shell rather than a real PDP-11, so they
need no emulator and no network.  The fake reproduces the two things about
the real DZ11 line that broke command framing:

  * output arrives a BYTE AT A TIME, so any needle we find is usually the
    last thing in the buffer, and
  * the tty echoes the command line back before the output.

The fake derives its end-of-command marker from the command it is sent, so
these tests do not need to know how cmd() builds that marker.

Runnable two ways:
    python tests/cmd_test.py          (no pytest needed)
    pytest tests/cmd_test.py          (once pytest-asyncio is a dep)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import rich

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from v7.v7 import V7

# A file whose CONTENTS contain the shell prompt string '$ '.  This is not
# contrived: it is line 71 of the project's own c/bedit.c, and any C file
# documenting Forth's '$' word or using '$ ' in a comment will do it.
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


class FakeV7:
    """A minimal v7 tty + sh, standing in for reader and writer both."""

    def __init__(self, files: dict[str, str], chunk: int = 1) -> None:
        self.files = files
        self.chunk = chunk
        self.pending = ""  # bytes waiting to go back to the client
        self.line = ""  # partial command being typed
        self.ran: list[str] = []

    # ---- writer side ------------------------------------------------
    def write(self, data: str) -> None:
        for ch in data:
            if ch in "\r\n":
                self._run(self.line)
                self.line = ""
            else:
                self.line += ch

    async def drain(self) -> None:
        return

    def close(self) -> None:
        return

    # ---- the "shell" ------------------------------------------------
    def _run(self, line: str) -> None:
        """Interpret one command line the way v7's sh does.

        The v7 quirks that matter here, all verified on the real machine:
          * `cat` on a missing file prints to stderr but still EXITS 0,
            so cat's status cannot be used to detect failure;
          * `test -f` does report correctly;
          * `$?` is the status of the immediately preceding command, and
            an `echo` resets it to 0;
          * `s=$?` assignment stores it for later.
        """
        self.ran.append(line)
        self.pending += line + "\r\n"  # tty echo of the command
        status = 0  # $?  -- the PREVIOUS command's status
        saved = 0  # whatever was stashed with s=$?
        for raw_part in line.split(";"):
            part = raw_part.strip()
            if part.startswith("cat "):
                name = part[4:].strip()
                body = self.files.get(name)
                if body is None:
                    self.pending += f"cat: can't open {name}\r\n"
                else:
                    self.pending += body.replace("\n", "\r\n")
                status = 0  # v7 cat: always 0, even on failure
            elif part.startswith("test -f "):
                status = (
                    0 if part[len("test -f ") :].strip() in self.files else 1
                )
            elif part == "s=$?":
                saved = status  # assignment does not change $?
            elif part.startswith("echo -n "):
                self.pending += self._expand(part[8:], status, saved)
                status = 0
            elif part.startswith("echo "):
                self.pending += self._expand(part[5:], status, saved) + "\r\n"
                status = 0
        self.pending += "$ "  # and the prompt

    @staticmethod
    def _expand(word: str, status: int, saved: int) -> str:
        """Substitute $? and $s the way sh would."""
        return word.replace("$?", str(status)).replace("$s", str(saved))

    # ---- reader side ------------------------------------------------
    async def read(self, n: int) -> str:
        if not self.pending:
            # Line has gone quiet.  _pump() suppresses EOFError and treats
            # it as "nothing more right now", which is what we want.
            raise EOFError
        take = min(self.chunk, n, len(self.pending))
        out, self.pending = self.pending[:take], self.pending[take:]
        return out


def make_v7(files: dict[str, str] | None = None) -> tuple[V7, FakeV7]:
    fake = FakeV7(files if files is not None else {"bedit.c": BEDIT_C})
    v = V7(user="claude", password="x", host="fake", port=0)
    v.reader = fake  # type: ignore[assignment]
    v.writer = fake  # type: ignore[assignment]
    return v, fake


# --------------------------------------------------------------------
# tests
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_command_still_works() -> None:
    """A command with no '$ ' in its output must be unaffected."""
    v, _ = make_v7({})
    out = await v.cmd("echo hello", timeout=10)
    assert "hello" in out, f"lost the output: {out!r}"


@pytest.mark.asyncio
async def test_output_containing_prompt_is_not_truncated() -> None:
    """The reported bug: '$ ' inside the DATA ended the command early."""
    v, _ = make_v7()
    out = await v.cmd("cat bedit.c", timeout=10)
    assert "THIS IS THE LAST LINE" in out, (
        f"output truncated at the '$ ' inside the file; got {len(out)} bytes, "
        f"ending {out[-40:]!r}"
    )


@pytest.mark.asyncio
async def test_no_residue_leaks_into_the_next_command() -> None:
    """The second half of the bug: leftovers surfacing as later output."""
    v, _ = make_v7()
    await v.cmd("cat bedit.c", timeout=10)
    out = await v.cmd("echo MARKER1", timeout=10)
    assert "MARKER1" in out, f"follow-up lost its own output: {out!r}"
    for leaked in ("mv_eol", "THIS IS THE LAST LINE", "last non-space"):
        assert leaked not in out, (
            f"previous command's output leaked into this one ({leaked!r} "
            f"seen in {out[:120]!r})"
        )


@pytest.mark.asyncio
async def test_file_without_trailing_newline() -> None:
    """The marker must not be mistaken for file content when they join."""
    v, _ = make_v7({"nonl": NO_NEWLINE})
    out = await v.cmd("cat nonl", timeout=10)
    assert out.rstrip().endswith("abc"), (
        f"marker not cleanly stripped from a file with no final newline: "
        f"{out[-40:]!r}"
    )


@pytest.mark.asyncio
async def test_fetch_roundtrips_a_file_containing_a_prompt() -> None:
    """_fetch() (the 'get' verb) must return the file byte-for-byte."""
    v, _ = make_v7()
    body = await v._fetch("bedit.c")
    assert body == BEDIT_C, (
        "downloaded file does not match the original\n"
        f"  got  {len(body or '')} bytes\n"
        f"  want {len(BEDIT_C)} bytes"
    )


@pytest.mark.asyncio
async def test_fetch_of_missing_file_returns_none() -> None:
    """A genuinely absent file must still be reported as a failure."""
    v, _ = make_v7({})
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
    v, _ = make_v7({"editor.c": body})
    got = await v._fetch("editor.c")
    assert got == body, (
        "a file that merely MENTIONS an error string was misreported as a "
        f"failed download: got {got!r}"
    )


async def _main() -> int:
    """Run every test_* coroutine without needing pytest installed."""
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            await asyncio.wait_for(t(), timeout=60)
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
