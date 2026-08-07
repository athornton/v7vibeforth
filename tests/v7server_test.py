"""Self-tests for the fake v7 server in tests/support/v7server.py.

A fixture nobody checks is a fixture that quietly lies.  Earlier in this
project a hand-rolled fake modelled a MODERN `cat` (nonzero exit on a
missing file); tests passed against it while the real PDP-11 still failed,
because v7's cat exits 0.  So these tests pin the fixture's behaviour to
what was actually observed on the real machine.

Every expected string below was captured from the real v7 under simh.

These are the FIXTURE's own tests: v7 shell semantics, login, pacing.  The
end-to-end tests of v7.py's behaviour against this fixture live in
cmd_test.py; anything asserted there is deliberately not repeated here.

Runnable two ways:
    python tests/v7server_test.py     (no pytest needed)
    pytest tests/v7server_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import rich

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from support.v7server import V7Server, logged_in

from v7.v7 import V7, v7sum


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


@pytest.mark.asyncio
async def test_login_succeeds() -> None:
    """The client must get through the fixture's login sequence."""
    async with V7Server(char_delay=0) as srv:
        v = await connect_to(srv)
        assert v.writer is not None, "client failed to log in to the fixture"
        assert srv.logins == 1, f"server saw {srv.logins} logins, wanted 1"
        await v.close()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_cat_exits_zero_on_missing_file() -> None:
    """v7's cat prints an error but STILL EXITS 0.  This is the quirk."""
    async with logged_in(char_delay=0) as (v, _):
        out = await v.cmd("cat /nosuchfile; echo rc$?", timeout=15)
        assert "can't open /nosuchfile" in out, f"no error text: {out!r}"
        assert "rc0" in out, (
            "fixture made cat exit nonzero; the real v7 exits 0, and "
            f"modelling it wrongly is what hid the bug before: {out!r}"
        )


@pytest.mark.asyncio
async def test_test_f_reports_correctly() -> None:
    """`test -f` is the reliable existence check, unlike cat."""
    async with logged_in({"real": "x\n"}, char_delay=0) as (v, _):
        out = await v.cmd("test -f real; echo rc$?", timeout=15)
        assert "rc0" in out, f"test -f on an existing file: {out!r}"
        out = await v.cmd("test -f nope; echo rc$?", timeout=15)
        assert "rc1" in out, f"test -f on a missing file: {out!r}"


@pytest.mark.asyncio
async def test_echo_resets_status_but_assignment_does_not() -> None:
    """$? is the PREVIOUS command's status; echo clobbers it, s=$? saves it."""
    async with logged_in(char_delay=0) as (v, _):
        # Without stashing, the intervening echo resets $? to 0.
        out = await v.cmd("false; echo -n MK; echo rc$?", timeout=15)
        assert "MKrc0" in out, f"echo should have reset $? to 0: {out!r}"
        # Stashing first preserves it.
        out = await v.cmd("false; s=$?; echo -n MK; echo rc$s", timeout=15)
        assert "MKrc1" in out, f"s=$? should have preserved 1: {out!r}"


@pytest.mark.asyncio
async def test_sum_matches_v7sum_and_format() -> None:
    """`sum` output must match v7's real column layout and algorithm."""
    body = "hello world\n"
    async with logged_in({"f": body}, char_delay=0) as (v, _):
        out = await v.cmd("sum f", timeout=15)
        want, blocks = v7sum(body.encode())
        assert f"{want:d}{blocks:6d}" in out, (
            f"sum column layout wrong; real v7 prints '%u%6u': {out!r}"
        )


@pytest.mark.asyncio
async def test_error_wording_matches_v7() -> None:
    """v7 is inconsistent: sum says "Can't", wc says "can't"."""
    async with logged_in(char_delay=0) as (v, _):
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


@pytest.mark.asyncio
async def test_output_is_paced_one_byte_at_a_time() -> None:
    """The whole point of the fixture: reads must return single bytes.

    If this fails, the suite can no longer catch end-of-output framing bugs,
    because on the real 9600-baud line 28910 of 28914 reads are 1 byte.
    """
    async with logged_in({"f": "x" * 200 + "\n"}) as (v, _):
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


@pytest.mark.asyncio
async def test_redirection_and_append() -> None:
    """put_verified reassembles chunks with `cat a > b` and `cat c >> b`."""
    async with logged_in(char_delay=0) as (v, _):
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


@pytest.mark.asyncio
async def test_stdin_capture_reassembles_lines() -> None:
    """`cat > f` must store what was typed, newlines and all.

    Every upload goes through this path.  _cat_chunk sends a bare LF while
    send_slow sends CR, so the fixture has to treat CR, LF and CRLF alike or
    uploads silently lose every newline (which showed up as a sum mismatch,
    not as missing data).
    """
    async with logged_in(char_delay=0) as (v, srv):
        await v._cat_chunk("/tmp/chunk", ["alpha", "beta", "gamma"], 0)
        assert srv.files.get("/tmp/chunk") == "alpha\nbeta\ngamma\n", (
            f"stdin capture mangled the chunk: {srv.files.get('/tmp/chunk')!r}"
        )


@pytest.mark.asyncio
async def test_put_verified_roundtrip() -> None:
    """The sum-verified upload path, end to end against the fixture.

    This is the one v7.py path that cannot be tested without a server
    fixture: it interleaves upload, `sum` verification and reassembly.
    """
    async with logged_in(char_delay=0) as (v, srv):
        content = "".join(f"line {i}\n" for i in range(150))
        ok = await v.put_verified("out.txt", content, chunk_lines=60, pace=0)
        assert ok, "put_verified reported failure"
        assert srv.files.get("out.txt") == content, (
            "uploaded file does not match what was sent\n"
            f"  got  {len(srv.files.get('out.txt', ''))} bytes\n"
            f"  want {len(content)} bytes"
        )


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
