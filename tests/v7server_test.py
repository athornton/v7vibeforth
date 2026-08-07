"""Self-tests for the fake v7 server in tests/support/v7server.py.

A fixture nobody checks is a fixture that quietly lies.  Earlier in this
project a hand-rolled fake modelled a MODERN `cat` (nonzero exit on a
missing file); tests passed against it while the real PDP-11 still failed,
because v7's cat exits 0.  So these tests pin the fixture's behaviour to
what was actually observed on the real machine.

Every expected string below was captured from the real v7 under simh.

Runnable two ways:
    python tests/v7server_test.py     (no pytest needed)
    pytest tests/v7server_test.py     (once pytest-asyncio is a dep)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import rich

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from support.v7server import V7Server

from v7.v7 import V7, v7sum

# Contains '$ ' (the shell prompt) AND 'not found' -- the two strings that
# each broke a different piece of v7.py.
TRICKY = """\
/* mv_eol: last non-space column */
/* $ : go to end of line */
strc(msg, "not found");
LAST
"""


async def connect_to(srv: V7Server, **kwargs: object) -> V7:
    """Return a logged-in V7 client pointed at `srv`."""
    v = V7(
        user=srv.user,
        password=srv.password,
        host="127.0.0.1",
        port=srv.port,
        **kwargs,  # type: ignore[arg-type]
    )
    await v.connect()
    return v


# --------------------------------------------------------------------
# tests
# --------------------------------------------------------------------


async def test_login_succeeds() -> None:
    """The client must get through the fixture's login sequence."""
    async with V7Server(char_delay=0) as srv:
        v = await connect_to(srv)
        assert v.writer is not None, "client failed to log in to the fixture"
        assert srv.logins == 1, f"server saw {srv.logins} logins, wanted 1"
        await v.close()


async def test_login_rejects_bad_password() -> None:
    """A wrong password must NOT produce a logged-in session."""
    async with V7Server(char_delay=0) as srv:
        v = V7(
            user=srv.user,
            password="wrong",
            host="127.0.0.1",
            port=srv.port,
        )
        await v.connect()
        assert v.writer is None, (
            "client believes it logged in despite a bad password"
        )


async def test_cat_exits_zero_on_missing_file() -> None:
    """v7's cat prints an error but STILL EXITS 0.  This is the quirk."""
    async with V7Server(char_delay=0) as srv:
        v = await connect_to(srv)
        out = await v.cmd("cat /nosuchfile; echo rc$?", timeout=15)
        assert "can't open /nosuchfile" in out, f"no error text: {out!r}"
        assert "rc0" in out, (
            "fixture made cat exit nonzero; the real v7 exits 0, and "
            f"modelling it wrongly is what hid the bug before: {out!r}"
        )
        await v.close()


async def test_test_f_reports_correctly() -> None:
    """`test -f` is the reliable existence check, unlike cat."""
    async with V7Server(files={"real": "x\n"}, char_delay=0) as srv:
        v = await connect_to(srv)
        out = await v.cmd("test -f real; echo rc$?", timeout=15)
        assert "rc0" in out, f"test -f on an existing file: {out!r}"
        out = await v.cmd("test -f nope; echo rc$?", timeout=15)
        assert "rc1" in out, f"test -f on a missing file: {out!r}"
        await v.close()


async def test_echo_resets_status_but_assignment_does_not() -> None:
    """$? is the PREVIOUS command's status; echo clobbers it, s=$? saves it."""
    async with V7Server(char_delay=0) as srv:
        v = await connect_to(srv)
        # Without stashing, the intervening echo resets $? to 0.
        out = await v.cmd("false; echo -n MK; echo rc$?", timeout=15)
        assert "MKrc0" in out, f"echo should have reset $? to 0: {out!r}"
        # Stashing first preserves it.
        out = await v.cmd("false; s=$?; echo -n MK; echo rc$s", timeout=15)
        assert "MKrc1" in out, f"s=$? should have preserved 1: {out!r}"
        await v.close()


async def test_sum_matches_v7sum_and_format() -> None:
    """`sum` output must match v7's real column layout and algorithm."""
    body = "hello world\n"
    async with V7Server(files={"f": body}, char_delay=0) as srv:
        v = await connect_to(srv)
        out = await v.cmd("sum f", timeout=15)
        want, blocks = v7sum(body.encode())
        assert str(want) in out, f"sum {want} not in {out!r}"
        assert str(blocks) in out, f"block count {blocks} not in {out!r}"
        await v.close()


async def test_error_wording_matches_v7() -> None:
    """v7 is inconsistent: sum says "Can't", wc says "can't"."""
    async with V7Server(char_delay=0) as srv:
        v = await connect_to(srv)
        out = await v.cmd("sum /nope", timeout=15)
        assert "sum: Can't open /nope" in out, (
            f"sum uses a capital C on real v7: {out!r}"
        )
        out = await v.cmd("wc -c /nope", timeout=15)
        assert "wc: can't open /nope" in out, (
            f"wc uses a lowercase c on real v7: {out!r}"
        )
        out = await v.cmd("frobnicate", timeout=15)
        assert "frobnicate: not found" in out, f"unknown command: {out!r}"
        await v.close()


async def test_output_is_paced_one_byte_at_a_time() -> None:
    """The whole point of the fixture: reads must return single bytes.

    If this fails, the suite can no longer catch end-of-output framing bugs,
    because on the real 9600-baud line 28910 of 28914 reads are 1 byte.
    """
    async with V7Server(files={"f": "x" * 200 + "\n"}) as srv:
        v = await connect_to(srv)
        sizes: list[int] = []
        original = v._pump

        async def spy(timeout: float) -> str:
            got = await original(timeout)
            if got:
                sizes.append(len(got))
            return got

        v._pump = spy  # type: ignore[method-assign]
        await v.cmd("cat f", timeout=30)
        ones = sum(1 for s in sizes if s == 1)
        assert sizes, "no reads recorded at all"
        assert ones / len(sizes) > 0.9, (
            "fixture is not pacing output: only "
            f"{ones}/{len(sizes)} reads were a single byte, so this suite "
            "cannot reproduce the real line's framing behaviour"
        )
        await v.close()


async def test_redirection_and_append() -> None:
    """put_verified reassembles chunks with `cat a > b` and `cat c >> b`."""
    async with V7Server(char_delay=0) as srv:
        v = await connect_to(srv)
        await v.cmd("echo one > /tmp/a", timeout=15)
        await v.cmd("echo two > /tmp/b", timeout=15)
        await v.cmd("cat /tmp/a > /tmp/c", timeout=15)
        await v.cmd("cat /tmp/b >> /tmp/c", timeout=15)
        out = await v.cmd("cat /tmp/c", timeout=15)
        body = out.replace("\r", "")
        assert "one" in body, (
            f"append redirection lost the first chunk: {body!r}"
        )
        assert "two" in body, (
            f"append redirection lost the second chunk: {body!r}"
        )
        assert body.index("one") < body.index("two"), (
            f"append put the chunks in the wrong order: {body!r}"
        )
        await v.close()


async def test_cmd_survives_prompt_inside_data() -> None:
    """End to end over a real socket: the original bug must stay fixed."""
    async with V7Server(files={"tricky.c": TRICKY}) as srv:
        v = await connect_to(srv)
        out = await v.cmd("cat tricky.c", timeout=30)
        assert "LAST" in out, (
            f"output truncated at the '$ ' inside the data: {out[-40:]!r}"
        )
        follow = await v.cmd("echo MARKER", timeout=15)
        assert "MARKER" in follow, f"follow-up lost its output: {follow!r}"
        assert "mv_eol" not in follow, (
            f"previous output leaked into the next command: {follow[:80]!r}"
        )
        await v.close()


async def test_fetch_roundtrip_over_socket() -> None:
    """_fetch() must return a file containing both '$ ' and 'not found'."""
    async with V7Server(files={"tricky.c": TRICKY}) as srv:
        v = await connect_to(srv)
        body = await v._fetch("tricky.c")
        assert body == TRICKY, (
            "download mismatch\n"
            f"  got  {len(body or 0 * '')} bytes {body!r}\n"
            f"  want {len(TRICKY)} bytes"
        )
        assert await v._fetch("nope.c") is None, (
            "a missing file was reported as a successful download"
        )
        await v.close()


async def test_put_verified_roundtrip() -> None:
    """The sum-verified upload path, end to end against the fixture."""
    async with V7Server(char_delay=0) as srv:
        v = await connect_to(srv)
        content = "".join(f"line {i}\n" for i in range(150))
        ok = await v.put_verified("out.txt", content, chunk_lines=60, pace=0)
        assert ok, "put_verified reported failure"
        assert srv.files.get("out.txt") == content, (
            "uploaded file does not match what was sent\n"
            f"  got  {len(srv.files.get('out.txt', ''))} bytes\n"
            f"  want {len(content)} bytes"
        )
        await v.close()


async def _main() -> int:
    """Run every test_* coroutine without needing pytest installed."""
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            await asyncio.wait_for(t(), timeout=120)
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
