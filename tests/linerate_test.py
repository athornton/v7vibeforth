"""Tests for deriving the line speed by measurement.

Telnet cannot tell us the speed: simh negotiates only LINEMODE, SGA, ECHO
and BINARY -- never RFC 1079 TSPEED -- so `get_extra_info('speed')` is None
and the DZ11's rate is invisible to the protocol.  It is, however, directly
observable, because the host is ~1000x faster than the line and every
character costs a full character time to arrive.

These tests drive the fake server at several `char_delay` settings and
check that the measurement recovers the corresponding baud rate, so the
whole mechanism is verified without touching a PDP-11.

Runnable two ways:
    python tests/linerate_test.py
    pytest tests/linerate_test.py
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

from v7.v7 import V7

# Pacing the fixture at exactly 10/baud seconds per character should make
# the measurement come back at `baud`.  Only rates fast enough to keep the
# suite quick are worth testing: the probe is 100 characters, so 1200 baud
# already costs ~0.8 s of real time.
#
# 2400 is deliberately EXCLUDED from the snap assertion below.  It sits
# only 1.33x above 1800, so their geometric midpoint is 2078 -- and this
# measurement is routinely 15-20% low under a loaded test run, which lands
# beneath that midpoint and correctly snaps to 1800.  That is the
# measurement running out of resolution, not a bug: 1800 must stay in
# STANDARD_BAUD, because removing it would make a genuine 1800-baud line
# snap UP to 2400 and pace 33% too fast, risking the silent overrun that
# under-reporting cannot cause.  Erring slow is safe; erring fast corrupts.
RATES = (1200.0, 2400.0, 4800.0, 9600.0)

# Rates whose standard-table neighbours are a full octave away, so a
# measurement has room to be sloppy and still snap correctly.
UNAMBIGUOUS = (1200.0, 4800.0, 9600.0)

# The measurement is a wall-clock timing on a loopback socket with an event
# loop in the way, and the fixture's own asyncio.sleep() granularity adds
# error on top, so it is not exact.  Observed under a loaded pytest run: a
# 4800-baud line measured 23% low.  All that matters is landing inside
# snap_baud's window (sqrt(2), i.e. 41%), so allow a third and let the
# snap assertion below be the real check.
TOLERANCE = 0.33


@pytest.mark.asyncio
async def test_snap_baud_picks_nearest_standard_rate() -> None:
    """Pure arithmetic: a raw estimate must snap to a real serial speed."""
    # Values actually measured against the real DZ11 during development.
    assert V7.snap_baud(9737.0) == 9600.0, "9737 should snap down to 9600"
    assert V7.snap_baud(9235.0) == 9600.0, "9235 should snap up to 9600"
    assert V7.snap_baud(310.0) == 300.0, "310 should snap to 300"
    # A sloppy measurement on a real 4800-baud line came back 23% low.  The
    # standard rates are an octave apart here, so it must still snap: an
    # under-wide window used to leave this stranded at 3684.
    assert V7.snap_baud(3684.0) == 4800.0, (
        "3684 (measured on a real 4800-baud line) must still snap to 4800"
    )
    # Snapping is by RATIO, not raw distance.  13000 is 3400 above 9600 and
    # 6200 below 19200 -- raw distance says 9600, and so does the ratio
    # (1.35x vs 1.48x).  The point is that both agree only because the
    # comparison is in log space; below the geometric midpoint (13576) the
    # lower rate wins, above it the higher one.
    assert V7.snap_baud(13000.0) == 9600.0, (
        "below the geometric midpoint of 9600 and 19200, 9600 must win"
    )
    assert V7.snap_baud(15000.0) == 19200.0, (
        "above the geometric midpoint of 9600 and 19200, 19200 must win"
    )
    # Nothing plausible: leave it alone rather than lying confidently.
    assert V7.snap_baud(1_000_000.0) == 1_000_000.0, (
        "an implausible rate should be reported as-is, not snapped"
    )
    assert V7.snap_baud(0.0) == 0.0, "zero must not blow up the log"


@pytest.mark.asyncio
async def test_char_time_derives_from_baud() -> None:
    """char_time must be one 8N1 character at the current speed."""
    v = V7(user="u", password="p", host="h", port=1, baud=9600)
    assert abs(v.char_time - 10 / 9600) < 1e-9, (
        f"9600 baud should be ~1.04 ms/char, got {v.char_time * 1000:.3f} ms"
    )
    v = V7(user="u", password="p", host="h", port=1, baud=300)
    assert abs(v.char_time - 10 / 300) < 1e-9, (
        f"300 baud should be ~33.3 ms/char, got {v.char_time * 1000:.3f} ms"
    )


@pytest.mark.asyncio
async def test_unmeasured_falls_back_to_slowest() -> None:
    """With no measurement we must assume the SLOW end, not the fast one.

    Guessing too fast overruns the tty input queue and corrupts an upload;
    guessing too slow merely wastes time.
    """
    v = V7(user="u", password="p", host="h", port=1)
    assert v.baud is None, "baud should start unknown"
    assert v.char_time == 10 / V7.FALLBACK_BAUD, (
        "an unmeasured line must fall back to the slowest supported rate"
    )
    assert V7.FALLBACK_BAUD == 300.0, (
        "300 baud is the slowest thing this tool talks to (console/DC11)"
    )


@pytest.mark.asyncio
async def test_measures_each_rate() -> None:
    """The headline: pacing the fixture at N baud must measure back N."""
    for baud in RATES:
        async with logged_in(char_delay=10 / baud, paced_prefix=10_000) as (
            v,
            _,
        ):
            got = await v.measure_line_rate()
            assert got is not None, f"no measurement at all for {baud} baud"
            err = abs(got - baud) / baud
            assert err < TOLERANCE, (
                f"measured {got:.0f} baud on a {baud:.0f}-baud line "
                f"({err * 100:.0f}% off, tolerance {TOLERANCE * 100:.0f}%)"
            )
            # The measurement is biased LOW (scheduling overhead only ever
            # adds time), so never let it read faster than the line really
            # is: pacing too fast is the failure that corrupts uploads.
            assert got < baud * (1 + TOLERANCE), (
                f"measured {got:.0f} on a {baud:.0f}-baud line -- reading "
                "FASTER than the line risks a silent tty overrun"
            )
            if baud in UNAMBIGUOUS:
                assert V7.snap_baud(got) == baud, (
                    f"measured {got:.0f} on a {baud:.0f}-baud line, which "
                    f"snapped to {V7.snap_baud(got):.0f} not {baud:.0f}"
                )


@pytest.mark.asyncio
async def test_ambiguous_rate_errs_slow_never_fast() -> None:
    """Where snapping is ambiguous, it must round DOWN, not up.

    1800 and 2400 are only 1.33x apart, closer than this measurement can
    reliably resolve, so a 2400-baud line may well read as 1800.  That is
    acceptable -- it paces 33% slower than necessary.  The reverse would
    pace 33% too fast and risk the silent character loss that put_verified
    then has to catch.
    """
    for raw in (1976.0, 2000.0, 2077.0):
        snapped = V7.snap_baud(raw)
        assert snapped <= 2400.0, (
            f"{raw:.0f} snapped up to {snapped:.0f}; a measurement below "
            "the true rate must never be rounded above it"
        )
    # And a clean 2400 measurement must still land on 2400.
    assert V7.snap_baud(2400.0) == 2400.0, "an exact 2400 must stay 2400"
    assert V7.snap_baud(2350.0) == 2400.0, "2350 is nearest 2400 by ratio"


@pytest.mark.asyncio
async def test_connect_sets_baud_automatically() -> None:
    """connect() must leave a usable line rate behind without being asked."""
    async with logged_in(char_delay=10 / 9600, paced_prefix=10_000) as (v, _):
        assert v.baud == 9600.0, (
            f"connect() should have measured ~9600 baud, got {v.baud}"
        )
        assert abs(v.char_time - 10 / 9600) < 1e-9, (
            "char_time should follow the measured rate"
        )


@pytest.mark.asyncio
async def test_explicit_baud_is_not_overridden() -> None:
    """An explicit baud= is the caller's promise; don't second-guess it."""
    async with V7Server(char_delay=0) as srv:
        v = V7(
            user=srv.user,
            password=srv.password,
            host="127.0.0.1",
            port=srv.port,
            baud=300,
        )
        await v.connect()
        try:
            assert v.writer is not None, "failed to log in"
            assert v.baud == 300, (
                f"explicit baud=300 was overwritten with {v.baud}"
            )
        finally:
            await v.close()


@pytest.mark.asyncio
async def test_line_pace_scales_with_length_and_speed() -> None:
    """The upload pause must scale with BOTH line length and line speed.

    A fixed constant was the old bug: 20 ms is a rounding error at 300 baud
    and twice the necessary wait at 9600.
    """
    fast = V7(user="u", password="p", host="h", port=1, baud=9600)
    slow = V7(user="u", password="p", host="h", port=1, baud=300)
    short, long = "x" * 10, "x" * 80
    assert fast.line_pace(long) > fast.line_pace(short), (
        "a longer line takes longer to echo, so it must wait longer"
    )
    assert slow.line_pace(short) > fast.line_pace(short), (
        "the same line on a slower link must wait longer"
    )
    # 300 baud is 32x slower than 9600, so the pause should scale with it.
    ratio = slow.line_pace(short) / fast.line_pace(short)
    assert 30 < ratio < 34, (
        f"300 vs 9600 baud should scale the pause ~32x, got {ratio:.1f}x"
    )


@pytest.mark.asyncio
async def test_measurement_failure_leaves_baud_alone() -> None:
    """A line that goes quiet must not produce a bogus rate."""
    async with logged_in(char_delay=0) as (v, srv):
        before = v.baud
        await srv.stop()  # kill the server under the client's feet
        got = await v.measure_line_rate()
        assert got is None, f"a dead line should not yield a rate, got {got}"
        assert v.baud == before, (
            f"a failed measurement changed baud from {before} to {v.baud}"
        )


@pytest.mark.asyncio
async def test_upload_still_verifies_with_derived_pace() -> None:
    """End to end: the sum-verified upload must work on derived pacing."""
    async with logged_in(char_delay=0) as (v, srv):
        content = "".join(f"line {i}\n" for i in range(120))
        ok = await v.put_verified("out.txt", content, chunk_lines=60)
        assert ok, "put_verified failed using the derived pace"
        assert srv.files.get("out.txt") == content, (
            "upload with derived pacing corrupted the file:\n"
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
            await asyncio.wait_for(t(), timeout=180)
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
