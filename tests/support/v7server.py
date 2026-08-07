"""A fake Unix v7 system, spoken over a real telnet socket.

This stands in for the simh PDP-11 so the test suite needs no emulator.  It
is a telnetlib3 server whose "shell" is a small state machine over a virtual
filesystem, which is enough to exercise login, cmd(), _fetch() and the
sum-verified upload path end to end.

Everything here was checked against the real v7 under simh; the surprising
bits are called out where they are implemented, because several of them are
what broke v7.py in the first place:

  * `cat` on a missing file prints to stderr but STILL EXITS 0, so cat's own
    status cannot be used to detect failure.  `test -f` reports correctly.
  * `$?` is the status of the immediately preceding command, and a following
    `echo` resets it to 0 -- so a status must be stashed (`s=$?`) before any
    marker echo runs.
  * error wording is not uniform: `sum: Can't open X` (capital C) but
    `wc: can't open X` (lowercase), `X: not found` for an unknown command,
    and `rm: X nonexistent`.
  * output is PACED one byte at a time.  On the real 9600-baud DZ11 a read
    of 4096 bytes returns exactly one byte ~99.99% of the time (measured:
    28910 of 28914 reads), because the host is ~1000x faster than the line.
    Tests that do not reproduce that pacing cannot catch end-of-output bugs,
    which is the entire reason this fixture exists.

The `v7sum` implementation is imported from the code under test on purpose:
it is verified against the real /bin/sum, and duplicating it here would let
the two drift apart silently.
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Self

import telnetlib3

from v7.v7 import v7sum

if TYPE_CHECKING:
    from collections.abc import Callable

# One character at 9600 baud, 8N1 (8 data + start + stop = 10 bits).
BAUD_9600_PER_CHAR = 10 / 9600

PROMPT = "$ "


@dataclass
class V7Server:
    """A fake v7 host: telnet listener + login state + virtual filesystem.

    Usage:
        srv = V7Server(files={"bedit.c": "..."})
        await srv.start()
        ... connect a V7 client to 127.0.0.1:srv.port ...
        await srv.stop()
    """

    user: str = "claude"
    password: str = "daisies"
    files: dict[str, str] = field(default_factory=dict)
    cwd: str = "/usr/claude"
    # Seconds per output character.  0 sends output in one burst, which is
    # much faster for tests that do not care about framing.  The default
    # models the real DZ11 so framing bugs are actually reproducible.
    char_delay: float = BAUD_9600_PER_CHAR
    # Pace at most this many characters; beyond it, send the rest at once.
    # Keeps a 28KB `cat` from taking 30 realtime seconds in a unit test
    # while still delivering the start of the stream one byte at a time.
    paced_prefix: int = 2000
    prompt: str = PROMPT

    port: int = 0
    ran: list[str] = field(default_factory=list)
    logins: int = 0
    _server: asyncio.base_events.Server | None = None
    # Set to drop the connection the next time the shell writes anything,
    # so the reconnect path in _resync() can be tested.
    drop_next_write: bool = False

    async def start(self) -> None:
        """Listen on an ephemeral port; sets self.port."""
        self._server = await telnetlib3.create_server(
            host="127.0.0.1",
            port=0,
            shell=self._shell,
            connect_maxwait=0.3,
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop listening and wait for the socket to close."""
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ---- wire helpers ------------------------------------------------

    async def _emit(self, writer: telnetlib3.TelnetWriter, text: str) -> None:
        """Write text to the client, paced like a serial line."""
        if self.drop_next_write:
            self.drop_next_write = False
            writer.close()
            raise ConnectionResetError("simulated line drop")
        if not self.char_delay:
            writer.write(text)
            await writer.drain()
            return
        for i, ch in enumerate(text):
            writer.write(ch)
            await writer.drain()
            if i < self.paced_prefix:
                await asyncio.sleep(self.char_delay)

    async def _readline(
        self,
        reader: telnetlib3.TelnetReader,
        writer: telnetlib3.TelnetWriter,
        *,
        echo: bool = True,
    ) -> str | None:
        """Read one line, echoing as v7's cooked tty does.

        Returns None at EOF.  Honours ^H erase and ^U kill, like the real
        line (stty erase '^H' kill '^U'), and treats ^D on an empty line as
        end of input.
        """
        line = ""
        while True:
            ch = await reader.read(1)
            if not ch:
                return None
            if ch in "\r\n":
                if echo:
                    await self._emit(writer, "\r\n")
                return line
            if ch == "\x04":  # ^D
                return line or None
            if ch in "\x08\x7f":  # erase
                if line:
                    line = line[:-1]
                    if echo:
                        await self._emit(writer, "\x08 \x08")
                continue
            if ch == "\x15":  # ^U kill
                if echo:
                    await self._emit(writer, "\r\n")
                line = ""
                continue
            if ch == "\x03":  # ^C -- v7 uses DEL for intr, but be tolerant
                line = ""
                continue
            line += ch
            if echo:
                await self._emit(writer, ch)

    # ---- the session -------------------------------------------------

    async def _shell(
        self,
        reader: telnetlib3.TelnetReader,
        writer: telnetlib3.TelnetWriter,
    ) -> None:
        try:
            await self._session(reader, writer)
        except (ConnectionError, asyncio.CancelledError, EOFError):
            return
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _session(
        self,
        reader: telnetlib3.TelnetReader,
        writer: telnetlib3.TelnetWriter,
    ) -> None:
        # simh prints a banner before getty ever runs.
        await self._emit(
            writer,
            "\n\r\nConnected to the PDP-11 simulator DZ device, line 0\r\n\n",
        )
        while not await self._login(reader, writer):
            await self._emit(writer, "Login incorrect\r\n")
        self.logins += 1
        await self._emit(writer, self.prompt)
        while True:
            line = await self._readline(reader, writer)
            if line is None:
                return
            if not line.strip():
                await self._emit(writer, self.prompt)
                continue
            self.ran.append(line)
            # `cat > file` with no operand reads STDIN until ^D.  This is how
            # every upload works (see _cat_chunk), so the fixture has to
            # model it rather than treating it as an ordinary command.
            sink = self._stdin_sink(line)
            if sink is not None:
                target, append = sink
                await self._slurp(reader, writer, target, append=append)
                await self._emit(writer, self.prompt)
                continue
            out = self._run(line)
            await self._emit(writer, out + self.prompt)

    @staticmethod
    def _stdin_sink(line: str) -> tuple[str, bool] | None:
        """If `line` is a bare `cat > f` / `cat >> f`, return (file, append).

        `cat foo > bar` has an operand and copies a file, so it is NOT a
        stdin sink; only a redirect with nothing to read from is.
        """
        for op, append in ((">>", True), (">", False)):
            idx = line.find(op)
            if idx == -1:
                continue
            left, right = line[:idx].strip(), line[idx + len(op) :].strip()
            if left == "cat" and right:
                return right, append
        return None

    async def _slurp(
        self,
        reader: telnetlib3.TelnetReader,
        writer: telnetlib3.TelnetWriter,
        target: str,
        *,
        append: bool,
    ) -> None:
        """Consume echoed input into `target` until ^D, as v7's cat does."""
        body = ""
        seen_cr = False
        while True:
            ch = await reader.read(1)
            if not ch or ch == "\x04":  # EOF
                break
            # The line discipline maps CR to newline and echoes CRLF.  A
            # sender may use CR (send_slow does) or a bare LF (_cat_chunk
            # does), and CRLF must count once, not twice.
            if ch in "\r\n":
                if ch == "\n" and seen_cr:
                    seen_cr = False
                    continue
                seen_cr = ch == "\r"
                body += "\n"
                await self._emit(writer, "\r\n")
                continue
            seen_cr = False
            body += ch
            await self._emit(writer, ch)
        if append and target in self.files:
            self.files[target] += body
        else:
            self.files[target] = body

    async def _login(
        self,
        reader: telnetlib3.TelnetReader,
        writer: telnetlib3.TelnetWriter,
    ) -> bool:
        # getty re-prompts on an empty line rather than treating it as a
        # username.  This matters: the client deliberately sends several
        # bare CRs to shake the line out before logging in (speed
        # negotiation can mangle the first line), and a fixture that took
        # the first CR as the username would sit permanently one step out
        # of phase with the client.
        while True:
            await self._emit(writer, "\r\nlogin: ")
            who = await self._readline(reader, writer)
            if who is None:
                raise EOFError
            if who.strip():
                break
        # v7 does not echo the password.
        await self._emit(writer, "Password:")
        pw = await self._readline(reader, writer, echo=False)
        if pw is None:
            raise EOFError
        await self._emit(writer, "\r\n")
        return who.strip() == self.user and pw == self.password

    # ---- the "shell" -------------------------------------------------

    def _run(self, line: str) -> str:
        """Execute one command line, returning what the tty would print.

        Supports just enough sh: `;` sequencing, `>`/`>>` redirection,
        `$?`/`$<var>` expansion and `var=$?` assignment.
        """
        out = ""
        status = 0
        variables: dict[str, str] = {}
        for raw_part in line.split(";"):
            part = raw_part.strip()
            if not part:
                continue
            part = self._expand(part, status, variables)
            # var=$?  (assignment does not disturb $? itself)
            if "=" in part.split(" ")[0] and not part.startswith("="):
                name, _, val = part.partition("=")
                if name.isidentifier():
                    variables[name] = val
                    continue
            text, status = self._one(part)
            out += text
        return out

    @staticmethod
    def _expand(part: str, status: int, variables: dict[str, str]) -> str:
        part = part.replace("$?", str(status))
        for name, val in variables.items():
            part = part.replace(f"${name}", val)
        return part

    def _one(self, part: str) -> tuple[str, int]:
        """Run a single command (no `;`), returning (output, exit status)."""
        # Redirection: peel off '> file' / '>> file' first.
        target = None
        append = False
        for op in (">>", ">"):
            idx = part.find(op)
            if idx != -1:
                target = part[idx + len(op) :].strip()
                append = op == ">>"
                part = part[:idx].strip()
                break
        try:
            argv = shlex.split(part)
        except ValueError:
            argv = part.split()
        if not argv:
            return "", 0
        handler = self._COMMANDS.get(argv[0])
        if handler is None:
            # v7 sh reports an unknown command like this, and exits nonzero.
            return f"{argv[0]}: not found\r\n", 1
        text, status = handler(self, argv)
        if target is not None:
            # Redirected output goes to the file, not the terminal.  Note
            # v7 writes stderr to the tty regardless; we keep it simple and
            # send everything to the file, which is all the tests need.
            body = text.replace("\r\n", "\n")
            if append and target in self.files:
                self.files[target] += body
            else:
                self.files[target] = body
            return "", status
        return text, status

    # -- individual commands.  Each returns (tty output, exit status). --

    def _cmd_cat(self, argv: list[str]) -> tuple[str, int]:
        out = ""
        for name in argv[1:]:
            body = self.files.get(name)
            if body is None:
                out += f"cat: can't open {name}\r\n"
            else:
                out += body.replace("\n", "\r\n")
        # THE QUIRK: v7 cat exits 0 even when it could not open the file.
        return out, 0

    def _cmd_echo(self, argv: list[str]) -> tuple[str, int]:
        args = argv[1:]
        newline = True
        if args and args[0] == "-n":
            newline = False
            args = args[1:]
        return " ".join(args) + ("\r\n" if newline else ""), 0

    def _cmd_test(self, argv: list[str]) -> tuple[str, int]:
        # test -f FILE / -r FILE: 0 if it exists (as a regular file), else 1
        if len(argv) >= 3 and argv[1] in ("-f", "-r", "-s"):
            return "", 0 if argv[2] in self.files else 1
        return "", 1

    def _cmd_sum(self, argv: list[str]) -> tuple[str, int]:
        out = ""
        status = 0
        for name in argv[1:]:
            body = self.files.get(name)
            if body is None:
                # Capital C here; wc uses lowercase.  Verified on v7.
                out += f"sum: Can't open {name}\r\n"
                status = 1
                continue
            checksum, blocks = v7sum(body.encode())
            # v7's sum is printf("%u%6u\n"): the checksum unpadded, then the
            # block count right-aligned in six columns.  Verified against
            # the real machine for 1-, 2- and 3-digit block counts:
            #   '65456     1'   '33237    55'   '43287   135'
            out += f"{checksum:d}{blocks:6d}\r\n"
        return out, status

    def _cmd_wc(self, argv: list[str]) -> tuple[str, int]:
        args = [a for a in argv[1:] if not a.startswith("-")]
        out = ""
        status = 0
        for name in args:
            body = self.files.get(name)
            if body is None:
                out += f"wc: can't open {name}\r\n"
                status = 1
                continue
            out += f"{len(body.encode()):7d} {name}\r\n"
        return out, status

    def _cmd_rm(self, argv: list[str]) -> tuple[str, int]:
        out = ""
        status = 0
        for name in argv[1:]:
            if name in self.files:
                del self.files[name]
            else:
                out += f"rm: {name} nonexistent\r\n"
                status = 1
        return out, status

    def _cmd_pwd(self, argv: list[str]) -> tuple[str, int]:
        return f"{self.cwd}\r\n", 0

    def _cmd_ls(self, argv: list[str]) -> tuple[str, int]:
        names = sorted(self.files)
        return "".join(f"{n}\r\n" for n in names), 0

    def _cmd_mkdir(self, argv: list[str]) -> tuple[str, int]:
        return "", 0

    def _cmd_true(self, argv: list[str]) -> tuple[str, int]:
        return "", 0

    def _cmd_false(self, argv: list[str]) -> tuple[str, int]:
        return "", 1

    # Not a dataclass field: ClassVar keeps it off __init__ and out of the
    # mutable-default check.
    _COMMANDS: ClassVar[
        dict[str, Callable[[V7Server, list[str]], tuple[str, int]]]
    ] = {
        "cat": _cmd_cat,
        "echo": _cmd_echo,
        "test": _cmd_test,
        "sum": _cmd_sum,
        "wc": _cmd_wc,
        "rm": _cmd_rm,
        "pwd": _cmd_pwd,
        "ls": _cmd_ls,
        "mkdir": _cmd_mkdir,
        "true": _cmd_true,
        "false": _cmd_false,
    }


async def serve(
    files: dict[str, str] | None = None, **kwargs: object
) -> V7Server:
    """Start a V7Server and return it (caller must stop() it)."""
    srv = V7Server(files=dict(files or {}), **kwargs)  # type: ignore[arg-type]
    await srv.start()
    return srv
