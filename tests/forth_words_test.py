"""Behavioural tests for the Forth interpreter's word set.

These build c/forth.c with the host compiler and drive the resulting binary
by feeding it Forth source on stdin, so they check what the interpreter
actually DOES rather than what the source appears to say.  The v7 build is
the same source, so a word that works here works there; deployment is still
verified separately against the real PDP-11.

Each case is (source, expected substring in output).  `.S` prints the stack
as `<depth> a b c `, which makes stack effects easy to assert on.

Runnable two ways:
    python tests/forth_words_test.py
    pytest tests/forth_words_test.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import rich

REPO = Path(__file__).parent.parent
SOURCE = REPO / "c" / "forth.c"

# The host build needs gnu89: the source is K&R C, which modern compilers
# reject by default (implicit int, old-style definitions).
CFLAGS = ["-std=gnu89", "-w", "-Wno-return-mismatch", "-O"]


def find_cc() -> str | None:
    """Locate a C compiler, including one only in a conda env."""
    for name in ("gcc", "cc", "clang"):
        path = shutil.which(name)
        if path:
            return path
    # This project's usual toolchain lives in an LSST conda environment
    # that is not on the default PATH.
    guess = Path(
        "/opt/lsst/software/stack/conda/envs/lsst-scipipe-13.1.0-exact/bin/gcc"
    )
    return str(guess) if guess.is_file() else None


_BINARY: str | None = None
_BUILD_ERROR: str | None = None


def forth_binary() -> str:
    """Build c/forth.c once per session and return the binary's path."""
    global _BINARY, _BUILD_ERROR
    if _BINARY is not None:
        return _BINARY
    if _BUILD_ERROR is not None:
        pytest.skip(_BUILD_ERROR)
    cc = find_cc()
    if cc is None:
        _BUILD_ERROR = "no C compiler available to build c/forth.c"
        pytest.skip(_BUILD_ERROR)
    out = Path(tempfile.mkdtemp(prefix="forthtest")) / "forth"
    proc = subprocess.run(
        [cc, *CFLAGS, "-o", str(out), str(SOURCE)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        _BUILD_ERROR = f"could not build c/forth.c: {proc.stderr[-400:]}"
        pytest.skip(_BUILD_ERROR)
    _BINARY = str(out)
    return _BINARY


def run_forth(src: str, *, timeout: float = 20.0) -> str:
    """Feed `src` to the interpreter and return everything it printed."""
    binary = forth_binary()
    if not src.endswith("\n"):
        src += "\n"
    proc = subprocess.run(
        [binary],
        input=src,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        # Run somewhere harmless: the interpreter opens ./blocks on demand.
        cwd=tempfile.gettempdir(),
        env={**os.environ, "TERM": "dumb"},
    )
    return proc.stdout + proc.stderr


# --------------------------------------------------------------------
# the new double-cell words
# --------------------------------------------------------------------

# A double number is two cells with the LOW cell pushed first, so the high
# cell is on top (see dpush/dpop in c/forth.c).  "Duplicate the top double"
# and "copy the top two cells in order" are therefore the same operation,
# which is why later standards define these on cell pairs.
DOUBLE_CASES = [
    # 2DUP ( d -- d d )
    ("1 2 2DUP .S", "<4> 1 2 1 2"),
    # 2DROP ( d -- )
    ("1 2 3 4 2DROP .S", "<2> 1 2"),
    # 2SWAP ( d1 d2 -- d2 d1 )
    ("1 2 3 4 2SWAP .S", "<4> 3 4 1 2"),
    # 2OVER ( d1 d2 -- d1 d2 d1 )
    ("1 2 3 4 2OVER .S", "<6> 1 2 3 4 1 2"),
]


@pytest.mark.parametrize(("src", "want"), DOUBLE_CASES)
def test_double_cell_word(src: str, want: str) -> None:
    """Each new word must produce exactly the standard's stack effect."""
    out = run_forth(src)
    assert "?" not in out.replace("? ", ""), (
        f"interpreter did not recognise a word in {src!r}: {out.strip()!r}"
    )
    assert want in out, (
        f"{src!r}\n  wanted stack {want!r}\n  got          {out.strip()!r}"
    )


def test_double_words_are_usable_in_definitions() -> None:
    """They must compile into a colon definition, not just run interactively.

    The sequence is chosen to keep at least two doubles on the stack
    throughout, so it tests compilation rather than the underflow guard:
      11 22 33 44  ->  2OVER  ->  11 22 33 44 11 22
                   ->  2DROP  ->  11 22 33 44
                   ->  2SWAP  ->  33 44 11 22
                   ->  2DUP   ->  33 44 11 22 11 22
    """
    out = run_forth(": t 2OVER 2DROP 2SWAP 2DUP ;\n11 22 33 44 t .S")
    assert "<6> 33 44 11 22 11 22" in out, (
        f"the new words did not compile correctly: {out.strip()!r}"
    )


def test_underflow_is_survivable() -> None:
    """Too few cells must not crash: pop() clamps at an empty stack.

    Forth-79 leaves stack underflow undefined, and this interpreter's
    pop() returns 0 rather than faulting -- worth pinning, because a
    2OVER that indexed dstack[] directly would read off the end.
    """
    out = run_forth("1 2 2OVER .S")
    assert "?" not in out.replace("? ", ""), (
        f"2OVER on an under-full stack was not even recognised: {out!r}"
    )
    assert "Memory fault" not in out, (
        f"2OVER faulted on an under-full stack: {out.strip()!r}"
    )
    assert "core dumped" not in out, (
        f"2OVER dumped core on an under-full stack: {out.strip()!r}"
    )


def test_2dup_then_dplus_behaves_as_double_arithmetic() -> None:
    """2DUP must duplicate a DOUBLE, which D+ can then consume.

    This is the check that the cell order is right: if 2DUP swapped the
    halves, D+ would produce nonsense rather than 2*d.  1 0 is the double
    1, so D+ must give 2 0.
    """
    out = run_forth("1 0 2DUP D+ .S")
    assert "<2> 2 0" in out, (
        "2DUP produced a double that D+ could not double correctly "
        f"(cell order wrong?): {out.strip()!r}"
    )


def test_2over_reaches_the_second_double() -> None:
    """2OVER must copy the SECOND double, verified through D+."""
    # doubles: d1 = 5 (5 0), d2 = 7 (7 0).  2OVER then D+ adds d1 to d2.
    out = run_forth("5 0 7 0 2OVER D+ .S")
    assert "<4> 5 0 12 0" in out, (
        f"2OVER did not copy the second double: {out.strip()!r}"
    )


# --------------------------------------------------------------------
# guard: the words we already had must keep working
# --------------------------------------------------------------------

REGRESSION_CASES = [
    ("1 2 DUP .S", "<3> 1 2 2"),
    ("1 2 DROP .S", "<1> 1"),
    ("1 2 SWAP .S", "<2> 2 1"),
    ("1 2 OVER .S", "<3> 1 2 1"),
    ("1 2 3 ROT .S", "<3> 2 3 1"),
    ("27 DUP * .", "729"),
    ("2 3 + .", "5"),
    # the words most likely to be disturbed by a new opcode number
    ("1 2 3 DEPTH .", "3"),
    # DO...LOOP has to be inside a definition: Forth-79 only requires
    # looping to work when compiled, and this interpreter follows that.
    (": t 5 0 DO I . LOOP ; t", "0 1 2 3 4"),
]


@pytest.mark.parametrize(("src", "want"), REGRESSION_CASES)
def test_existing_words_still_work(src: str, want: str) -> None:
    """Adding opcodes must not disturb the words already there."""
    out = run_forth(src)
    assert want in out, (
        f"regression in {src!r}\n  wanted {want!r}\n  got    {out.strip()!r}"
    )


def test_dialect_modes_all_accept_the_new_words() -> None:
    """The new words are dialect-neutral, so no mode may warn about them.

    The interpreter is deliberately lenient: every word stays callable in
    every mode.  These are Double Number EXTENSION words (Forth-79 section
    11.1), absent from Forth-77 entirely, so tagging them as 79-only would
    make `-s 77` warn about words no 77 program could have used anyway.
    """
    binary = forth_binary()
    for mode in ([], ["-s", "77"], ["-s", "79"]):
        proc = subprocess.run(
            [binary, *mode],
            input="1 2 2DUP .S\n",
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            cwd=tempfile.gettempdir(),
        )
        both = proc.stdout + proc.stderr
        label = " ".join(mode) or "(default)"
        assert "<4> 1 2 1 2" in both, (
            f"mode {label}: wrong result {both.strip()!r}"
        )
        # The dialect warning text is "... is Forth-77 (running -s 79)".
        assert "Forth-77" not in both, (
            f"mode {label} warned about a dialect-neutral word: "
            f"{both.strip()!r}"
        )
        assert "Forth-79" not in both, (
            f"mode {label} warned about a dialect-neutral word: "
            f"{both.strip()!r}"
        )


def _main() -> int:
    """Run every test without needing pytest installed."""
    failed = 0
    ran = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        # unroll parametrised cases by hand for the standalone runner
        cases: list[tuple[object, ...]] = [()]
        if name == "test_double_cell_word":
            cases = list(DOUBLE_CASES)
        elif name == "test_existing_words_still_work":
            cases = list(REGRESSION_CASES)
        for args in cases:
            ran += 1
            label = f"{name}{args or ''}"
            try:
                fn(*args)
            except AssertionError as e:
                failed += 1
                rich.print(f"[red]FAIL[/red]: {label}\n      {e}")
            except Exception as e:
                failed += 1
                kind = type(e).__name__
                rich.print(f"[red]ERROR[/red]: {label}\n       {kind}: {e}")
            else:
                rich.print(f"ok: {label}")
    rich.print(f"\n{ran} run, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
