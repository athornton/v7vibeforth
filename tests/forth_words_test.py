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
import re
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
    with tempfile.TemporaryDirectory(prefix="forthrun") as workdir:
        proc = subprocess.run(
            [binary],
            input=src,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            # A fresh directory each time: the interpreter creates ./blocks
            # on demand, and the block tests write to it.
            cwd=workdir,
            env={**os.environ, "TERM": "dumb"},
        )
    return proc.stdout + proc.stderr


# --------------------------------------------------------------------
# the .fth test files: whole-word-set coverage
# --------------------------------------------------------------------

FORTH_DIR = Path(__file__).parent / "forth"
PRELUDE = FORTH_DIR / "prelude.fth"


def chunk_files() -> list[Path]:
    """Return the numbered test files, in order."""
    return sorted(
        p for p in FORTH_DIR.glob("*.fth") if p.name != "prelude.fth"
    )


def run_chunk(chunk: Path, *, timeout: float = 60.0) -> str:
    """Run one .fth file with the assertion prelude in front of it."""
    src = PRELUDE.read_text() + chunk.read_text()
    return run_forth(src, timeout=timeout)


@pytest.mark.parametrize("chunk", chunk_files(), ids=lambda p: p.stem)
def test_forth_chunk_passes(chunk: Path) -> None:
    """Every assertion in every .fth file must pass.

    Each file ends with REPORT, which prints a tests=/failed= line and then
    either ALL-OK or HAVE-FAILURES, so one grep tells us the verdict and the
    *** FAIL lines name the individual checks that broke.
    """
    out = run_chunk(chunk)
    assert "ALL-OK" in out, f"{chunk.name} reported failures:\n" + "\n".join(
        ln
        for ln in out.splitlines()
        if "FAIL" in ln or "tests=" in ln or "?" in ln
    )


@pytest.mark.parametrize("chunk", chunk_files(), ids=lambda p: p.stem)
def test_forth_chunk_has_no_unknown_words(chunk: Path) -> None:
    """A mistyped or missing word prints `<name> ?` -- catch that too.

    Without this a file could "pass" while silently skipping half its
    checks, because an unrecognised word is reported and then ignored
    rather than aborting the run.
    """
    out = run_chunk(chunk)
    unknown = [
        ln.strip()
        for ln in out.splitlines()
        if ln.strip().endswith(" ?") or ln.strip() == "?"
    ]
    assert not unknown, (
        f"{chunk.name} used words the interpreter does not know: {unknown}"
    )


@pytest.mark.parametrize(
    "chunk", [PRELUDE, *chunk_files()], ids=lambda p: p.stem
)
def test_comments_are_period_correct(chunk: Path) -> None:
    r"""Comments must use ( ... ), the only form Forth-78/79 has.

    The `\` comment is a Forth-83/ANS addition (and a gforth habit).  This
    interpreter does implement it, but these files are meant to be readable
    as period-correct Forth, so the parenthesised form is the house style.

    Two things to remember when editing them: `(` is a WORD, so it needs a
    space after it -- `(comment)` is silently parsed as code -- and the
    comment ends at the first `)` on the SAME line, because this
    interpreter's `(` does not span lines.
    """
    # A backslash is only a COMMENT when it starts a line or follows
    # whitespace with more text after it.  `FIND \` names the word itself,
    # which 11-misc.fth does deliberately to exercise it, so allow that.
    offenders = []
    for n, ln in enumerate(chunk.read_text().splitlines(), start=1):
        for m in re.finditer(r"(?:^|\s)\\(?:\s|$)", ln):
            before = ln[: m.start()].split()
            if before and before[-1] in ("FIND", "'", "[COMPILE]"):
                continue  # naming the word, not commenting with it
            offenders.append(f"{n}: {ln.strip()}")
            break
    assert not offenders, (
        f"{chunk.name} uses the Forth-83/ANS '\\' comment; Forth-78/79 has "
        f"only ( ... ):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "chunk", [PRELUDE, *chunk_files()], ids=lambda p: p.stem
)
def test_open_paren_is_followed_by_a_space(chunk: Path) -> None:
    """`( ` needs the space: `(text)` parses as code and is silently wrong."""
    bad = [
        f"{n}: {ln.strip()}"
        for n, ln in enumerate(chunk.read_text().splitlines(), start=1)
        if re.search(r"\((?![ )])", ln)
    ]
    assert not bad, (
        f"{chunk.name} has a '(' not followed by a space, which the "
        f"interpreter reads as a word rather than a comment:\n  "
        + "\n  ".join(bad)
    )


def defined_words() -> set[str]:
    """Every word name c/forth.c registers, from the source itself."""
    src = SOURCE.read_text()
    pattern = r'(?:prim|dprim|immprim|immdprim|vari)\("((?:[^"\\]|\\.)*)"'
    return {
        name.replace('\\"', '"').replace("\\\\", "\\")
        for name in re.findall(pattern, src)
    }


# Runtime words with no user-facing syntax: they exist only as the compiled
# body of a control structure or literal, planted by the compiler.  They are
# exercised indirectly every time a loop, string or literal runs, and cannot
# be typed at the interpreter, so they are exempt from the coverage check.
INTERNAL_ONLY = {
    "(+LOOP)",
    '(.")',
    "(DO)",
    "(DOES>)",
    "(LOOP)",
    "0BRANCH",
    "BRANCH",
    "EXIT",
    "LIT",
}


def test_every_defined_word_is_exercised() -> None:
    """The point of the suite: no word ships without a test.

    Compares the words c/forth.c registers against the text of the .fth
    files, so adding a word without testing it fails here.
    """
    text = "".join(p.read_text() for p in chunk_files())
    tokens = set(text.split())
    missing = sorted(
        w
        for w in defined_words()
        if w not in INTERNAL_ONLY and w not in tokens and w not in text
    )
    assert not missing, (
        f"{len(missing)} defined word(s) appear in no test file: {missing}.\n"
        "Add them to the appropriate tests/forth/*.fth group, or to "
        "INTERNAL_ONLY if they are compiler-planted runtime words."
    )


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
        elif name in (
            "test_forth_chunk_passes",
            "test_forth_chunk_has_no_unknown_words",
        ):
            cases = [(p,) for p in chunk_files()]
        elif name in (
            "test_comments_are_period_correct",
            "test_open_paren_is_followed_by_a_space",
        ):
            # these also check the prelude itself
            cases = [(p,) for p in [PRELUDE, *chunk_files()]]
        for args in cases:
            ran += 1
            shown = (
                args[0].stem if args and isinstance(args[0], Path) else args
            )
            label = f"{name}{f'[{shown}]' if args else ''}"
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
