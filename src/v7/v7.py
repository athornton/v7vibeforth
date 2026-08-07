#!/usr/bin/env python3
"""Expect-style driver for the Unix v7 PDP-11 over telnet.

This would presumably work with a real PDP-11 if you had a
telnet-to-serial gateway in front of the console or a serial line.  It
will work with either the 300 baud console or DC-11, or a 9600 baud
DZ-11; you will have to change the pacing and delays accordingly.  It
should work with any speed in between those, too, if you tweak the
various delay values.

Claude notes:
  Key facts learned:
  * Console echoes char-by-char; input is cooked mode (erase=^H kill=^U).
  * The tty canonical queue drops chars past a limit if no newline drains it,
    so long single lines lose data. Send SHORT lines, each ended by newline.
  * v7 `sum` = rotate-right-then-add, 16-bit  (verified).
"""

import argparse
import asyncio
import contextlib
import json
import logging
import math
import shlex
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from textwrap import dedent
from typing import ClassVar

import rich
import structlog
import telnetlib3

# There's a lot of playing fast and loose with strings and bytes
# because of telnetlib3.  In 1979, you had ASCII; there was no such
# thing as Unicode.  If that bothers you you're really going to hate
# K&R C.

# This program assumes you're going to be both uploading and downloading
# ASCII, split into lines, with a maximum line length of 100.

# mypy: disable-error-code="arg-type"


def v7sum(data: bytes | str) -> tuple[int, int]:
    """Reproduce v7 /bin/sum: returns (sum16, blocks512)."""
    if isinstance(data, str):
        data = data.encode()
    s = 0
    for b in data:
        s = (s >> 1) + 32768 if s & 1 else s >> 1
        s = (s + b) & 0xFFFF
    return s, (len(data) + 511) // 512


@dataclass
class V7verb:
    """Verbs for to the V7 class."""

    verb: str
    arity: float = 0.0  # You'll see why.
    aliases: tuple[str] | None = None
    needs_connection: bool = False


class V7:
    """Tool for upload to/download from remote Unix v7 system."""

    verbs: ClassVar[list[V7verb]] = [
        V7verb("bye", aliases=("quit", "exit")),
        V7verb("close", aliases=("disconnect")),
        V7verb("connect"),
        V7verb("debug"),
        V7verb("status"),
        V7verb("help"),
        V7verb(
            "password",
            aliases=("passwd"),
        ),
        V7verb(
            "host",
            arity=1,
        ),
        V7verb(
            "port",
            arity=1,
        ),
        V7verb(
            "local-sum",
            arity=1,
            aliases=("local_sum"),
        ),
        V7verb(
            "check",
            arity=1.5,  # See, I told you.
            aliases=("sum"),
        ),
        V7verb(
            "transmit",
            arity=1.5,
            aliases=("xmit", "send", "put", "upload"),
        ),
        V7verb(
            "get",
            arity=1.5,
            aliases=("receive", "recv", "download"),
        ),
        V7verb(
            "compare",
            arity=1.5,
            aliases=("cmp"),
        ),
        V7verb(
            "command",
            arity=999,
            aliases=("cmd", "run"),
        ),
    ]
    """Verbs known to the V7 class."""

    def __init__(
        self,
        user: str | None = None,
        password: str | None = None,
        host: str | None = None,
        port: int | None = None,
        *,
        debug: bool = False,
        baud: float | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.debug = debug
        self.reader: telnetlib3.TelnetReader | None = None
        self.writer: telnetlib3.TelnetWriter | None = None
        self.buf = ""
        self._seq = 0  # serial number for cmd()'s framing marker
        # Line speed in bits/sec.  If given, it is taken as gospel and no
        # measurement happens; otherwise connect() measures it (see
        # measure_line_rate) and every pacing delay is derived from it.
        self.baud: float | None = baud
        self._baud_pinned = baud is not None
        self.logger = structlog.get_logger()
        self._set_debug_log()
        self.logger.debug("v7 instance initialized")

    # A character on an 8N1 serial line costs ten bit-times: eight data
    # bits plus a start and a stop bit.
    BITS_PER_CHAR: ClassVar[int] = 10

    # Used until a measurement lands, and if measurement fails.  300 baud
    # is the slowest thing this tool talks to (simh's console and DC11), so
    # it is the safe assumption: too-slow merely wastes time, while
    # too-fast overruns the tty input queue and corrupts an upload.
    FALLBACK_BAUD: ClassVar[float] = 300.0

    @property
    def char_time(self) -> float:
        """Seconds one character occupies on the wire."""
        baud = self.baud or self.FALLBACK_BAUD
        return self.BITS_PER_CHAR / baud

    def _set_debug_log(self) -> None:
        if self.debug:
            structlog.configure(
                wrapper_class=structlog.make_filtering_bound_logger(
                    logging.DEBUG
                )
            )
        else:
            structlog.configure(
                wrapper_class=structlog.make_filtering_bound_logger(
                    logging.WARNING
                )
            )

    def _write_error(self) -> None:
        self.logger.error("Cannot write to closed connection")

    async def connect(self) -> None:
        """Connect to remote system."""
        for attr in ("host", "port", "user", "password"):
            if getattr(self, attr) is None:
                self.logger.error(f"{attr.title()} must be set")
                return
        self.reader, self.writer = await telnetlib3.open_connection(
            self.host, self.port, connect_minwait=0.3
        )
        logged_in = await self._login()
        if logged_in is None:
            self.logger.error("Connection failed")
            self.reader = None
            self.writer = None
            return
        structlog.contextvars.bind_contextvars(host=self.host)
        structlog.contextvars.bind_contextvars(port=self.port)
        structlog.contextvars.bind_contextvars(user=self.user)
        # Now that there is a shell, find out how fast the line really is,
        # so every pacing delay can be derived from it instead of assuming
        # the 300-baud DC11 this tool was first written for.  An explicit
        # baud= wins; a failed measurement leaves the previous value.
        if not self._baud_pinned:
            measured = await self.measure_line_rate()
            if measured is not None:
                self.baud = self.snap_baud(measured)
                structlog.contextvars.bind_contextvars(baud=self.baud)
                self.logger.debug(
                    "line rate set",
                    measured=round(measured),
                    baud=self.baud,
                    char_ms=round(self.char_time * 1000, 3),
                )

    async def _pump(self, tmout: float) -> str:
        if self.reader is None:
            self.logger.error("Cannot read from closed connection")
            return ""
        self.logger.debug("_pump", timeout=round(tmout, 3))
        with contextlib.suppress(
            TimeoutError, ConnectionError, EOFError, OSError
        ):
            blk = 4 * 1024
            dxb = await asyncio.wait_for(self.reader.read(blk), timeout=tmout)
            # The telnetlib3 typing is inconsistent, hence the type:ignore.
            dx = (
                dxb.decode()
                if isinstance(dxb, bytes)  # type:ignore [redundant-expr]
                else dxb
            )
            self.logger.debug("_pump", buf=self.buf, dx=dx)
            self.buf += dx
            return dx
        return ""

    async def expect(
        self, needles: str | list[str], timeout: float = 60
    ) -> tuple[str, str]:
        """Read until one of `needles` appears; return (needle, text).

        Returns as soon as a needle matches, consuming everything up to and
        including it.  Deciding whether a match is *really* the end of a
        command is not something this can know -- see cmd(), which frames
        commands with a unique marker instead of guessing.

        When several needles match, the EARLIEST one in the stream wins:
        for ["$ ", "# ", "incorrect"] a login failure has to be reported as
        a failure even though a prompt turns up later in the buffer.
        """
        if isinstance(needles, str):
            needles = [needles]
        self.logger.debug("expect", needles=needles)
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while True:
            besti = -1
            best = None
            for n in needles:
                i = self.buf.find(n)
                self.logger.debug("expect", needle=n, pos=i, besti=besti)
                if i != -1 and (besti == -1 or i < besti):
                    besti, best = i, n
            self.logger.debug("expect", best=best, pos=besti)
            if best is not None:
                cut = besti + len(best)
                out, self.buf = self.buf[:cut], self.buf[cut:]
                self.logger.debug(
                    "expect", out=out, best=best, cut=cut, buf=self.buf
                )
                return best, out
            if loop.time() > end:
                raise TimeoutError(f"want {needles}, buf='{self.buf}'")
            self.logger.debug("expect: reading more output")
            # Floor the pump timeout: a non-positive wait_for() would fail
            # instantly and spin the loop until the deadline.
            await self._pump(min(2.0, max(0.05, end - loop.time())))

    async def send_slow(self, st: str, delay: float | None = None) -> None:
        """Send a string character-by-character with a delay between each.

        `delay` defaults to one character time at the measured line speed,
        so this paces itself to whatever is on the other end instead of
        assuming a 300-baud DC11 as it used to.

        DELIBERATELY still one character at a time.  Under simh a zero-delay
        burst of a whole command line appears to work, but that proves
        nothing: simh does not throttle TCP *input* (measured ~4500 cps
        accepted), so the burst is absorbed by a socket buffer that no real
        serial line has.  On actual hardware the receiver has to keep up
        character by character -- a DZ11's silo is small and shared between
        its lines, and period UARTs were worse still (the 8250 and even the
        faster 16450 had a ONE-byte receive buffer; no FIFO until the
        16550).  There is no flow control in this path either: no RTS/CTS,
        no XON/XOFF.  An overrun therefore does not raise an error, it
        silently eats characters -- which is the failure this project
        already hit, and the reason put_verified checksums every chunk.
        So: pace to the line, and let the checksum catch what slips.
        """
        if self.writer is None:
            self._write_error()
            return
        if delay is None:
            delay = self.char_time
        for ch in st:
            self.writer.write(ch)
            await self.writer.drain()
            if delay:
                await asyncio.sleep(delay)

    # Probe for measure_line_rate.  Long enough for a stable estimate
    # (measured spread: 16% at 60 chars, 3.5% at 100) and short enough to
    # be cheap -- 100 characters is ~0.11 s at 9600 baud, ~3.3 s at 300.
    PROBE_CHARS: ClassVar[int] = 100

    async def measure_line_rate(self) -> float | None:
        """Time a known-length reply to derive the line speed, in baud.

        Telnet cannot tell us this.  RFC 1079 TSPEED is a *client's* hint
        about its own terminal, and simh does not negotiate it at all: it
        offers only LINEMODE, SGA, ECHO and BINARY (verified against the
        real machine), so `get_extra_info('speed')` is None.  The line
        speed is a property of the emulated DZ11/DC11 inside simh, which
        nobody advertises.

        But it is directly observable.  The host is ~1000x faster than the
        line, so output arrives one character at a time, each one costing a
        full character time on the wire -- measured median inter-byte gap
        on the 9600-baud DZ11 was 1.027 ms against a theoretical 1.042 ms.
        Timing a reply of known length therefore recovers the rate to a few
        percent, and works on anything from a 300-baud console to a real
        PDP-11 behind a serial gateway.

        Returns the estimate in baud, or None if it could not be measured
        (in which case self.baud is left alone).
        """
        if self.writer is None:
            self._write_error()
            return None
        payload = "x" * self.PROBE_CHARS
        self.buf = ""
        # Send with no pacing: we are timing the RECEIVE side, and a probe
        # is a single short line that the tty queue swallows comfortably.
        await self.send_slow(f"echo {payload}\r", 0.0)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 30.0
        # Start the clock at the FIRST byte back, not at the send: what
        # comes before that is the round trip and the shell's own latency,
        # neither of which is the line rate.
        first: float | None = None
        seen = 0
        while seen < self.PROBE_CHARS and loop.time() < deadline:
            got = await self._pump(2.0)
            if not got:
                break
            if first is None:
                first = loop.time()
                # Don't count the first read: its arrival time is what we
                # are measuring from, so it has no gap in front of it.
                got = got[1:]
            seen += len(got)
        span = loop.time() - first if first is not None else 0.0
        # Drain the prompt so the next command starts from a clean line.
        await self._drain(0.4, 5.0)
        self.buf = ""
        if seen < self.PROBE_CHARS // 2 or span <= 0:
            self.logger.warning(
                "could not measure line rate; keeping current value",
                chars=seen,
                span=round(span, 4),
                baud=self.baud,
            )
            return None
        baud = seen / span * self.BITS_PER_CHAR
        self.logger.debug(
            "measured line rate",
            chars=seen,
            span=round(span, 4),
            baud=round(baud),
        )
        return baud

    # Standard serial rates.  A measurement lands a few percent off, so it
    # is snapped to the nearest of these -- both to report something
    # recognisable and so pacing is derived from the rate the hardware is
    # actually running at.
    STANDARD_BAUD: ClassVar[tuple[float, ...]] = (
        75.0,
        110.0,
        150.0,
        300.0,
        600.0,
        1200.0,
        1800.0,
        2400.0,
        4800.0,
        9600.0,
        19200.0,
        38400.0,
    )

    # How far a measurement may sit from a standard rate and still snap to
    # it, as a ratio.  Most of the standard rates are an OCTAVE apart
    # (300/600/1200, 2400/4800/9600/19200), so the midpoint between two
    # neighbours is sqrt(2) = 1.414x away from both; a window smaller than
    # that leaves a dead band where a real rate fails to snap.  sqrt(2) is
    # therefore the natural choice: every point between two octave-spaced
    # rates belongs to one of them, and nothing is left stranded.
    SNAP_WINDOW: ClassVar[float] = math.sqrt(2.0)

    @classmethod
    def snap_baud(cls, baud: float) -> float:
        """Snap a measured rate to the nearest standard serial speed.

        Compared in log space, so "nearest" means nearest by ratio: 9737
        lands on 9600 rather than being pulled toward 19200 by raw distance,
        and 3684 (measured on a real 4800-baud line, 23% low) lands on 4800
        rather than falling into a gap.

        A measurement further than SNAP_WINDOW from every standard rate is
        returned unchanged -- better an honest odd number than a confident
        wrong one.
        """
        if baud <= 0:
            return baud
        best = min(cls.STANDARD_BAUD, key=lambda s: abs(math.log(baud / s)))
        if abs(math.log(baud / best)) > math.log(cls.SNAP_WINDOW):
            return baud
        return best

    async def _login(self) -> str | None:
        """Log in to remote v7 system."""
        if self.writer is None:
            self._write_error()
            return None
        # Sometimes the first line is mangled due to speed negotiation.
        # Hit enter a couple times to get it straightened out.
        lprompt = False
        best = ""
        for _ in range(4):
            await self.send_slow("\r")
            try:
                tgt = "ogin:"
                _, found = await self.expect(tgt, timeout=1)
                self.logger.debug(f"Waiting for '{tgt}'", found=found)
                if tgt.find(found) >= -1:
                    lprompt = True
                    break
                if len(found) > len(best):
                    best = found
            except TimeoutError:
                continue
        if not lprompt:
            self.logger.error("Failed to get login prompt", best=best)
            return None
        await self.send_slow(f"{self.user}\r")
        await self.expect("assword:")
        await self.send_slow(f"{self.password}\r")
        hit, out = await self.expect(["$ ", "# ", "incorrect"], timeout=5)
        if hit == "incorrect":
            self.logger.error(f"login incorrect: {out}")
            return None
        return out

    # Framing marker for cmd().  The command line we send never contains
    # the joined string, because we send it as two echoes ("...MARK; echo
    # <serial>") and the shell joins them only in the OUTPUT.  So the first
    # occurrence of the joined marker is always the true end of output --
    # never the tty's echo of the command itself.  (Same trick as _resync.)
    _MARK = "V7xEOC"

    async def cmd(
        self, command: str, timeout: float = 120, prompt: str = "$ "
    ) -> str:
        """Send `command` to the remote system and return its output.

        The shell prompt cannot be used to find the end of the output: '$ '
        occurs in plenty of files (c/bedit.c has three), and any of them
        would end the read early and leave the rest of the output in the
        socket, where it would surface as the next command's output.  So we
        append a marker command and read until the marker instead.

        `prompt` is accepted for compatibility and used only as a fallback
        if the marker never arrives.
        """
        self.buf = ""
        # Serial number keeps a stale marker from a timed-out earlier
        # command from ending this one prematurely.
        self._seq += 1
        serial = f"{self._seq:04d}"
        marker = self._MARK + serial
        await self.send_slow(
            f"{command}; echo -n {self._MARK}; echo {serial}\r"
        )
        try:
            _, out = await self.expect(marker, timeout=timeout)
        except TimeoutError:
            # Marker never showed (command died, line dropped, or output
            # stopped).  Fall back to the prompt so callers still get
            # something, but leave the buffer alone for the caller to see.
            self.logger.warning(
                "cmd: marker not seen; falling back to prompt",
                command=command,
                marker=marker,
            )
            _, out = await self.expect(prompt, timeout=5)
            return self._strip_frame(out, command, marker)
        # The marker is immediately followed by "\r\n$ " from the shell.
        # Consume it so it cannot pollute the next command's read.
        await self.expect(prompt, timeout=10)
        return self._strip_frame(out, command, marker)

    def _strip_frame(self, out: str, command: str, marker: str) -> str:
        """Remove the echoed command line and the trailing marker."""
        # Drop everything up to and including the echo of our command line,
        # so the caller sees output only.  The echo ends at the first
        # newline after the command text.
        idx = out.find(command)
        if idx != -1:
            nl = out.find("\n", idx + len(command))
            if nl != -1:
                out = out[nl + 1 :]
        cut = out.rfind(marker)
        if cut != -1:
            out = out[:cut]
        return out

    async def put_lines(
        self,
        remote: str,
        content: str,
        *,
        pace: float | None = None,
        verify: bool = True,
    ) -> bool | dict[str, str | int]:
        """Upload text to `remote` via ed-free cat, line by line.

        Each physical line is sent then we wait to see its echo (the trailing
        newline) before sending the next, so the tty queue never overflows.
        `pace` is the per-character delay; None derives it from the measured
        line speed.  put_verified() is the faster path -- this one is kept
        for the pathologically slow case, where one character at a time with
        an echo check after every line is the only thing that survives.
        """
        if self.writer is None:
            self._write_error()
            return False
        if pace is None:
            pace = self.char_time
        if not content.endswith("\n"):
            content += "\n"
        lines = content.split("\n")[:-1]  # drop final empty
        self.buf = ""
        await self.send_slow("cat > " + remote + "\n")
        await asyncio.sleep(0.6)
        self.buf = ""
        for ln in lines:
            self.logger.debug("put_lines", line=ln)
            for ch in ln:
                self.writer.write(ch)
                await self.writer.drain()
                await asyncio.sleep(pace)
            self.writer.write("\n")
            await self.writer.drain()
            # wait for newline echo (CR or LF) to know the line drained
            await asyncio.sleep(pace)
            await self._pump(2.0)
        await asyncio.sleep(0.4)
        self.logger.debug("put_lines: terminating write")
        self.writer.write("\x04")  # EOF
        await self.writer.drain()
        self.logger.debug("put_lines: awaiting shell prompt")
        await self.expect("$ ", timeout=5)
        if verify:
            return await self.check(remote, content)
        return True

    async def _drain(self, quiet: float = 0.4, maxwait: float = 8.0) -> None:
        """Read until the line is quiet for `quiet` seconds."""
        loop = asyncio.get_event_loop()
        end = loop.time() + maxwait
        while loop.time() < end:
            got = await self._pump(quiet)
            self.logger.debug("_drain", got=got)
            if got == "":
                return

    async def _resync(self) -> bool:
        """Recover the session.  First try to clear a stuck cat (^D, ^C) and
        confirm the shell with a marker echo.  If that fails (the telnet line
        dropped under load and we're back at login:), reconnect and log in.
        """
        self.logger.debug("Attempted resync")
        if self.writer is not None:
            for tt in range(2):
                self.logger.debug("_resync", tries=tt + 1)
                self.logger.debug("_resync: sending ^D to terminate cat")
                self.writer.write("\x04")  # end any pending cat
                await self.writer.drain()
                await asyncio.sleep(0.2)
                self.logger.debug("_resync: sending ^C to interrupt process")
                self.writer.write("\x03")  # interrupt
                await self.writer.drain()
                await asyncio.sleep(0.2)
                self.buf = ""
                self.logger.debug("_resync: testing for shell")
                self.writer.write("echo -n foo; echo bar")  # split so it can't
                # match echo
                await self.writer.drain()
                with contextlib.suppress(TimeoutError):
                    await self.expect("foobar", timeout=3)
                    await self._drain(0.3, 3.0)
                    return True
                continue
        # line is dead -- reconnect from scratch
        self.logger.warning("_resync: line is dead; attempting reconnection")
        with contextlib.suppress(Exception):
            if self.writer is not None:
                self.logger.warning("Closing old connection")
                self.writer.close()
        await asyncio.sleep(1.0)
        self.buf = ""
        self.logger.warning("_resync: attempting reconnect")
        await self.connect()
        return True

    def line_pace(self, line: str) -> float:
        """How long to wait after bursting `line` into a `cat > file`.

        The tty echoes every character back, and that echo is what costs
        time: writing a 60-character line takes no time at all locally, but
        the far end needs 60 character times (plus CR and LF) to echo it.
        The pause therefore scales with the line's LENGTH and the line's
        SPEED -- a fixed constant is wrong at every rate but the one it was
        tuned for.

        The wait must cover the WHOLE echo, not a fraction of it.  Anything
        less leaves a per-line deficit that accumulates: at 9600 baud a
        60-line chunk of forth.c needs ~2960 ms of echo but a half-echo
        wait supplies only ~1420 ms, leaving ~1480 characters of backlog
        per chunk.  The DZ11's silo cannot hold that, so it compounds until
        the line wedges -- reliably around chunk 20 of forth.c.
        """
        # +2 for the CR/LF the tty echoes at the end of the line.
        return (len(line) + 2) * self.char_time * self.ECHO_HEADROOM

    # Multiplier on the measured echo time.  Must be >= 1.0: the echo has
    # to finish, and the only question is how much margin to add on top for
    # the far end's own scheduling (simh services the DZ11 from a single
    # core, so echo can lag its theoretical rate).  1.0 exactly was tried
    # and is NOT enough in practice; 1.25 restores the safety margin the
    # old hardcoded 0.02 s happened to provide for typical source lines.
    ECHO_HEADROOM: ClassVar[float] = 1.25

    async def _cat_chunk(
        self, remote: str, lines: list[str], pace: float | None = None
    ) -> None:
        """Cat > remote, ECHO ON, whole lines burst out with a tiny pause.

        Each line goes out in one write, then we pause long enough for the
        far end to echo it and drain that echo, keeping the tty's canonical
        input queue clear.  With `pace=None` the pause is derived from the
        measured line speed and the line's length (see line_pace); pass a
        number to override it.  Short lines matter too -- the caller's
        maxlen guard enforces that.
        """
        if self.writer is None:
            self._write_error()
            return
        self.buf = ""
        self.logger.debug("_cat_chunk: sending chunk", chunk=f"'{remote}'")
        await self.send_slow("cat > " + remote + "\n")
        await asyncio.sleep(0.3)
        self.buf = ""
        for ln in lines:
            self.writer.write(ln + "\n")  # whole line in one burst
            await self.writer.drain()
            delay = self.line_pace(ln) if pace is None else pace
            if delay:
                await asyncio.sleep(delay)
            await self._pump(0.3)  # drain echo, keep queue clear
            if "login:" in self.buf:  # session dropped mid-upload!
                self.logger.error(
                    "_cat_chunk: `login:` seen in buffer",
                    buffer=f"'{self.buf}'",
                )
                raise RuntimeError("connection dropped during upload")
            self.buf = ""  # don't accumulate echoed source
        # let the tty finish echoing the final line before we send EOF, so
        # the prompt we match next is the real shell prompt (not a '$ '
        # that appears inside the echoed source text).
        await self._drain(0.4, 8.0)
        self.buf = ""
        self.writer.write("\x04")  # end the cat
        await self.writer.drain()
        await self.expect("$ ", timeout=5)

    async def _remote_sum(self, remote: str) -> int | None:
        """Run `sum` on the remote end to see whether transfer succeeded."""
        self.logger.debug("_remote_sum", file=remote)
        out = await self.cmd("sum " + remote)
        self.logger.debug("_remote_sum", out=f"'{out}'")
        for nline in out.split("\n"):
            line = nline.strip().replace("\r", "")
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                return int(parts[0])
        return None

    async def put_verified(
        self,
        remote: str,
        content: str,
        *,
        chunk_lines: int = 60,
        pace: float | None = None,
        tries: int = 4,
        maxlen: int = 100,
    ) -> bool:
        """Upload in sum-verified chunks (echo on, whole-line bursts),
        retrying any failed chunk, then concatenate into `remote`.

        With `pace=None` the inter-line pause is derived from the measured
        line speed, so this runs at whatever the far end can take: full
        forth.c (~32KB) in ~90s on the 9600-baud DZ11, and correctly slower
        on a 300-baud console instead of corrupting the upload.  We can't go
        much faster than the line -- the DZ11 has no DMA, so the
        single-core simulator services every char and the telnet line DROPS
        under sustained faster bursts (hence _resync's reconnect-on-drop).
        `maxlen` guards the tty canonical input-queue limit.
        """
        if not content.endswith("\n"):
            content += "\n"
        # Expand tabs to spaces: the v7 tty echoes tabs expanded to the next
        # 8-column stop, so a tab-indented line can echo far wider than its
        # byte length and overrun the DZ11 output path mid-upload (this is
        # what dropped the line at the deeply-nested code around chunk 20).
        # Spaces upload cleanly and mean the same thing to cc.
        lines = content.split("\n")[:-1]
        toolong = [
            (i + 1, len(ln)) for i, ln in enumerate(lines) if len(ln) > maxlen
        ]
        if toolong:
            errstr = f"  REFUSING: {len(toolong)} line"
            if len(toolong) > 1:
                errstr += "s"
            errstr += f" exceed maxlen={maxlen}"
            errstr += str(toolong)
            self.logger.error(errstr)
            return False
        chunks = [
            lines[i : i + chunk_lines]
            for i in range(0, len(lines), chunk_lines)
        ]
        parts = []
        for ci, clines in enumerate(chunks):
            tmp = f"/tmp/c{ci}"
            body = ("\n".join(clines) + "\n").encode()
            want = v7sum(body)[0]
            ok = False
            cpace = pace
            for attempt in range(tries):
                got = None
                try:
                    await self._cat_chunk(tmp, clines, cpace)
                    got = await self._remote_sum(tmp)
                # A wedged line shows up two ways and BOTH are retryable:
                # _cat_chunk raises RuntimeError when it sees 'login:' in
                # the echo, but if the far end simply stops answering we
                # instead get a TimeoutError out of expect().  TimeoutError
                # is an OSError, NOT a RuntimeError, so catching only
                # RuntimeError let it escape past _resync() and abort the
                # whole upload -- which is exactly the crash this retry
                # loop exists to prevent.  ConnectionError/EOFError are
                # included for the same reason: they mean "line is sick",
                # and the answer to that is _resync(), not a traceback.
                except (
                    RuntimeError,
                    TimeoutError,
                    ConnectionError,
                    EOFError,
                ) as e:
                    self.logger.warning(
                        "put_verified: chunk failed, will retry",
                        chunk=ci + 1,
                        attempt=attempt + 1,
                        error=f"{type(e).__name__}: {e}",
                    )
                    reason = f"{type(e).__name__}: {e}"
                else:
                    reason = "sum mismatch" if got != want else "ok"
                status = "ok" if got == want else reason
                rich.print(
                    f"  chunk {ci + 1}/{len(chunks)} try {attempt + 1}"
                    f" : want={want} got={got} ({status})"
                )
                if got == want:
                    ok = True
                    break
                # recover: reconnect if the line died, clear residue, and
                # back off the pace -- the drop is load/timing sensitive, so
                # a slower retry usually goes through.  Multiplicative, so
                # it stays meaningful whether the pace came from the
                # measured line rate or was passed in: adding a flat 20 ms
                # would be a rounding error at 300 baud and a doubling at
                # 9600.  Capped so a pathological line cannot stall for ever.
                await self._resync()
                self.buf = ""
                base = self.line_pace("x" * maxlen) if cpace is None else cpace
                cpace = min(base * 2, 0.12)
            if not ok:
                self.logger.error(
                    f"  giving up on chunk {ci + 1} after {tries} tries"
                )
                return False
            parts.append(tmp)
        # Reassemble by appending one chunk at a time.  A single
        # "cat c0 c1 ... > remote" with all 33 names is >256 chars and
        # would be truncated by the tty input queue, so keep each
        # command short.
        await self.cmd("cat " + parts[0] + " > " + remote)
        for tmp in parts[1:]:
            await self.cmd("cat " + tmp + " >> " + remote)
        final = await self._remote_sum(remote)
        want_final = v7sum(content.encode())[0]
        rich.print(f"  final: want={want_final} got={final}")
        return final == want_final

    async def _fetch(self, remote: str) -> str | None:
        """Cat a remote text file and return just its contents.

        Returns None if the file cannot be read.  Readability is decided by
        `test -f`, NOT by scanning the body for error text: c/bedit.c
        contains the string "not found" twice, and the old content scan made
        that file (and any other file that discusses errors) impossible to
        download.

        Two v7 quirks drive the odd-looking command:
          * v7's `cat` exits 0 even when it cannot open the file, so cat's
            own status is useless -- verified on the machine.  `test -f`
            does report correctly.
          * `$?` refers to the immediately preceding command, so the status
            must be saved to a variable BEFORE the marker echo runs.
        """
        self.logger.debug("Fetching file", file=remote)
        # cmd() already strips its own echoed command line and end marker,
        # so what comes back is the file contents (with CRs from the tty).
        # Don't strip a leading 'cat <remote>' line here as well: after the
        # marker fix that line is gone, and a file whose own first line
        # happened to look like the command would lose it.
        #
        # The status is prefixed with the same split marker cmd() uses, so
        # the file contents cannot forge it: the joined string only ever
        # appears in the shell's OUTPUT, never in the command we send.
        tag = self._MARK + "rc"
        raw = await self.cmd(
            f"test -f {remote}; s=$?; cat {remote}"
            f"; echo -n {self._MARK}; echo rc$s"
        )
        body = raw.replace("\r", "")
        head, found, rest = body.rpartition(tag)
        if not found:
            # No status marker: something ate the framing.  Don't silently
            # hand back a possibly-truncated file.
            self.logger.error(
                "fetch: status marker missing", file=remote, bytes=len(body)
            )
            return None
        body = head
        code = rest.strip()
        if code != "0":
            self.logger.error(
                "remote error: file not readable", file=remote, status=code
            )
            return None
        if body and not body.endswith("\n"):
            body += "\n"
        self.logger.debug("File fetched", bytes=len(body))
        return body

    async def check(
        self, remote: str, content: bytes | str
    ) -> dict[str, str | int]:
        """Verify remote file matches content via byte count + v7 sum."""
        data = content.encode() if isinstance(content, str) else content
        want_sum, _ = v7sum(data)
        want_n = len(data)
        out = await self.cmd("sum " + remote + "; wc -c " + remote)
        self.logger.debug(f"check() got {out}")
        self.logger.info(
            "Checking file sum",
            want_sum=want_sum,
            want_n=want_n,
            remote_report=out,
        )
        return {"want_sum": want_sum, "want_n": want_n, "remote_report": out}

    async def close(self) -> None:
        """Close the connection to remote v7 host."""
        if self.writer:
            self.writer.close()
            self.writer = None
            self.reader = None
            self.buf = ""
            self.logger.debug("Connection closed")

    def help(self) -> None:
        rich.print(
            dedent(
                """COMMANDS:

                quit | bye | exit:
                    Exit the program.

                close | disconnect
                    Disconnect from remote.

                user <username>
                    Set remote user to <username>.

                password
                    Prompt for password for remote, and set it.

                host <hostname>
                    Set remote host.

                port <port-number>
                     Set remote port.

                debug
                     Toggle debug state.

                status
                     Show connection status.

                command | cmd | run <command ...>
                     Run <command ...> on remote system and report output.

                transmit | put | send | upload <local-file> [ <remote-file> ]
                    Send <local-file> to <remote-file>.
                    Must be a text file (no lines longer than 100 characters).
                    If unspecified <remote-file> is the basename of
                        <local-file> in the remote working directory.

                get | receive | recv | download <remote-file> [ <local-file> ]
                     Retrieve <remote-file> to <local-file>.
                     Must be a text file.
                     If unspecified <local-file> is the basename of
                         <remote-file> in the local working directory.

                compare <local-file> [ <remote-file> ]
                     Compare v7 checksums of <local-file> and <remote-file>.
                     If unspecified <remote-file> is the basename of
                        <local-file> in the remote working directory.

                check | sum <remote-file>
                     Show v7 checksum and byte count of <remote-file>.

                local_sum | local-sum <local-file>
                     Show v7 checksum and byte count of a local file.

                help
                    Show this help.
            """
            )
        )

    def _read_path(self, pth: str) -> str | None:
        pp = Path(pth)
        if not pp.is_file():
            self.logger.error(f"'{pth}' is not a file")
            return None
        try:
            return pp.read_text()
        except Exception:
            self.logger.exception(f"Cannot read text from '{pth}'")
            return None

    def _verify_verb(
        self, verb: str, args: list[str], *, connected: bool
    ) -> tuple[str | None, str | None]:
        # Yes, I have had Go on my mind, why do you ask?
        allverbs = [x.verb for x in self.verbs]
        vv: V7verb | None = None
        if verb in allverbs:
            vv = next(x for x in self.verbs if x.verb == verb)
        else:
            found = False
            for vv in self.verbs:
                if vv.aliases and verb in vv.aliases:
                    found = True
                    break
            if not found:
                return None, f"Unknown verb '{verb}'"
        la = len(args)
        lx = f"{la}: '{' '.join(args)}'"
        if vv is None:  # Can't be, but mypy can't tell that
            return None, f"Unknown verb '{verb}'"
        match vv.arity:
            case 0:
                if la != 0:
                    return None, f"'{verb}' requires no arguments, not {lx}"
            case 1:
                if la != 1:
                    return None, f"'{verb}' requires 1 argument, not {lx}"
            case 1.5:
                if la not in (1, 2):
                    return (
                        None,
                        f"'{verb}' requires 1 or 2 arguments, not {lx}",
                    )
            case 999:
                if la == 0:
                    return None, f"'{verb}' requires at least 1 argument"
            case _:
                return None, f"{verb} has weird arg reqs {vv.arity}, not {lx}"
        if self.writer is None and vv.needs_connection:
            return None, f"{verb} requires an active connection"
        return vv.verb, None

    async def interact(self) -> None:
        """REPL loop to interactively upload and check files."""
        connected = False
        verb: str | None = None
        try:
            await self.connect()
            connected = self.writer is not None
        except Exception:
            self.logger.exception("Error connecting")
        while True:
            self.buf = ""  # Clear any stored output.
            try:
                loop = asyncio.get_running_loop()
                inp = await loop.run_in_executor(None, input, "v7> ")
            except EOFError:
                break
            toks = shlex.split(inp)
            if len(toks) == 0:
                continue
            verb = toks[0].lower()  # match the verb case-insensitively,
            toks = toks[1:]  # but leave arguments untouched
            verb, err = self._verify_verb(verb, toks, connected=connected)
            if verb is None or err:
                self.logger.error("Parsing error", verb=verb, error=err)
                continue
            match verb:
                case "bye":
                    break
                case "help":
                    self.help()
                case "close":
                    await self.close()
                    connected = False
                case "connect":
                    try:
                        await self.connect()
                        connected = self.writer is not None
                    except Exception:
                        self.logger.exception("Connection failed")
                case "user":
                    self.user = toks[0]
                    structlog.contextvars.bind_contextvars(user=self.user)
                case "host":
                    self.host = toks[0]
                case "port":
                    try:
                        self.port = int(toks[0])
                    except ValueError:
                        self.logger.exception(f"'{toks[0]}' is not an integer")
                case "password":
                    self.password = getpass()
                case "debug":
                    self.debug = not self.debug
                    self._set_debug_log()
                    rich.print(f"debug is now {self.debug}")
                case "status":
                    cn = "[green]connected"
                    if self.writer is None:
                        cn = "[red]not connected"
                    db = ""
                    if self.debug:
                        db = "[blue]debug"
                    bd = ""
                    if self.baud:
                        how = "pinned" if self._baud_pinned else "measured"
                        bd = f"[cyan]{self.baud:g} baud ({how})"
                    status = (
                        f"{self.user}@{self.host}:{self.port} {cn} {db} {bd}"
                    )
                    rich.print(status)
                case "local_sum":
                    content = self._read_path(toks[0])
                    if content is None:
                        continue
                    s, blocks = v7sum(content)
                    rich.print(f"\nsum={s} blocks={blocks}\n")
                case "transmit":
                    content = self._read_path(toks[0])
                    if content is None:
                        continue
                    remote = Path(toks[0]).name if len(toks) == 1 else toks[1]
                    ok = await self.put_verified(remote, content)
                    if ok:
                        rich.print("[green]ok")  # Lowercase, like Forth.
                    else:
                        self.logger.error("Upload failed")
                case "get":
                    remote = toks[0]
                    local = (
                        Path(toks[1])
                        if len(toks) > 1
                        else Path(Path(remote).name)
                    )
                    try:
                        body = await self._fetch(remote)
                        if body is not None:
                            local.write_text(body)
                            rich.print(f"wrote {len(body)} bytes to {local}")
                    except Exception:
                        self.logger.exception(
                            "File retrieval failed",
                            local=local,
                            remote=remote,
                        )
                case "command":
                    args = " ".join(toks)
                    try:
                        result = await self.cmd(args)
                        if result:
                            print(f"\n{result}\n")
                    except Exception:
                        self.logger.exception("Command failed", command=args)
                case "check":
                    remote = toks[0]
                    try:
                        cmd = f"sum {remote}; wc -c {remote}"
                        result = await self.cmd(cmd)
                        if result:
                            print(f"\n{result}\n")
                    except Exception:
                        self.logger.exception("Checksum failed")
                case "compare":
                    content = self._read_path(toks[0])
                    if content is None:
                        continue
                    remote = toks[1] if len(toks) > 1 else Path(toks[0]).name
                    try:
                        res = await self.check(remote, content)
                        print(
                            f"\n{json.dumps(res, indent=4, sort_keys=True)}\n"
                        )
                    except Exception:
                        self.logger.exception("Comparison failed")
                case _:
                    self.logger.error("Unknown command", verb=verb)


def _cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="v7",
        description="Communicate with a v7 system (usually simulated on SIMH)",
    )
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-u", "--user")
    parser.add_argument("-r", "--host", "--remote")  # -h is for help
    parser.add_argument("-p", "--port")

    return parser.parse_args()


async def _interactive(args: argparse.Namespace, password: str) -> None:
    v7 = V7(
        user=args.user,
        password=password,
        host=args.host,
        port=args.port,
        debug=args.debug,
    )
    await v7.interact()


def main() -> None:
    """Start here."""
    args = _cli()
    password = getpass()
    asyncio.run(_interactive(args, password))


if __name__ == "__main__":
    main()
