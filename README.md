# Vibe-coded Unix v7 Forth interpreter and file transfer tool

Claude (Opus 4.8) did most of the work.

I have wanted a Forth interpreter on Unix v7 for a while now.

There are plenty of Forths for the PDP-11, but I don't know of any for Unix on the PDP-11, and I don't know of any that are written in C rather than MACRO-11.
While I very much like the PDP-11 instruction set, it would take me a long time to get adequately fluent in the various addressing modes and assembly conventions, and longer to be able to write without constant reference to a manual.
I want those neurons for other things.
On the other hand, I am already fair-to-middling at C.

Here's how I, leaning heavily on Claude, got a Forth interpreter and block screen editor for v7 Unix running.

Instructions for setting up Forth on v7 follow.
After that there is a brief discussion of the three pieces included in this repository: the Forth interpreter itself, the screen editor, and a tool (which I think is generally useful for people playing with v7 Unix under simh) for uploading and downloading files to the Unix system without the hassle of setting up `uucp`.

## Unix v7 system

First, get yourself a [simulated PDP-11/45](https://github.com/open-simh/simh).

### Building simh
The build system is somewhat complex; follow [the documentation](https://github.com/open-simh/simh/blob/master/README-CMake.md).
When you actually build it, you will want to do so with `TESTS=0 make` so you don't run every test for every simulator, which takes forever.
Put the `pdp11` binary somewhere on your path.

### Acquiring a binary of simh

Alternatively, your package manager may well have `simh` available.

It is available for MacOS via `brew`, and for Debian and Ubuntu through `apt`; it appears to be installable on RPM-based systems as well.

Although your distributed version is unlikely to be fully up-to-date, that doesn't really matter: the `pdp11` simulator is quite stable and the architecture well-understood.
We aren't doing anything fancy with simulated networking, or graphical displays, and therefore almost any `simh` version will do.

### Setting up Unix v7

A prebuilt tape image for the install can be found [here](https://www.tuhs.org/Archive/Distributions/Research/Keith_Bostic_v7/v7.tap.gz).
```
curl -L 'https://www.tuhs.org/Archive/Distributions/Research/Keith_Bostic_v7/v7.tap.gz' -o v7.tap.gz
gunzip v7.tap.gz
```

Then follow the instructions found in ["Installing and Using Research Unix Version 7 In the SimH PDP-11/45 and 11/70 Emulators"](https://drive.google.com/file/d/1Qqg41b2On-VpP9qjpXAEi8hV4RurgC92/view).
Use [install.ini](./simh/install.ini) for the initial installation; this is a minimal PDP-11/45 with one tape and one rp06 disk.

After that you have Unix restored to disk.
Transform your system to a 2M PDP-11/70 with [disk.ini](./simh/disk.ini).
Boot that.
Log in as `root`; the default password is also `root`.
Change the root password from its default of `root` with `passwd`.
Next, make an unprivileged user `claude` (or whatever), and set a password for it.
Remember that it's 1979.
There's no `adduser` or `useradd`
You just hand-edit `/etc/passwd` (don't like `ed`? me neither; just use `cat`!) and run the `passwd` command with the username as the argument to `passwd`.
`^D` means Control-D, which is `EOF`, which is "end of file".
Later in this document, `^X` will mean Control-X.

```
cat >> /etc/passwd
claude:*:11:3::/usr/claude:
^D
passwd claude
mkdir /usr/claude
chown claude /usr/claude
```

Next, you want to add DZ11 support, because the console and DC11 support that's built into the Unix you restored from the boot tape is only 300 bps, and that's really awful to work with.
Follow the instructions to [enable DZ11 lines](https://forums.raspberrypi.com/viewtopic.php?t=163320), and then you can get 9600 bps, which feels a lot better.
Once you've gotten the DZ11 lines into mkconf.c:
```
cc -o mkconf -O mkconf.c
cp hptmconf myconf
echo dz >> myconf
./mkconf < myconf
make
cp /unix /unixold
mv unix /
cd /etc
for i in 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15; do /etc/mknod tty$i c 19 $i; done
```

Then, sorry, you're gonna have to use `ed` (I mean, you don't have to, but it really is the easiest thing).
```
ed ttys
18,$d
1,$s/00/12/
1,$p
w
q
```

Now restart the system, this time with [boot.ini](simh/boot.ini).
You should be able to connect to the system with `telnet localhost 1145` (at least, if you have a machine with `telnet` in its `$PATH`.
Even if you don't, read on.

Python no longer ships `telnet` libraries as part of its standard configuration, so you are going to want a virtualenv with `telnetlib3` (installable via `pip` or `uv pip`) installed to get to the Unix machine.
SSH did not exist in 1979, and even if it had, a PDP-11 would have found it impossible to run it at any acceptable speed.

You can kill two birds with one stone here, because the file transfer utility you're about to use requires `telnetlib3`, so, from the directory that contains this readme:

 * Install a Python virtualenv and activate it.
 * Inside that virtualenv, `pip install -e .` or, if you have `uv` (which is much faster), `uv pip install -e .`.

Once your virtualenv is set up, `telnetlib3-client localhost 1145` will connect you to one of the PDP-11's serial lines.
Log in as the unprivileged user you created.

Once you've logged in, you probably want to add the following to your `.profile` (note that in this case, you type caret then h, not a literal Control-H, and likewise with U):
```
stty erase "^h" kill "^u" nl0 cr0
```
That sets Control-H to delete the previous character and Control-U to wipe out the whole line you're typing.

Note that interrupt is not Control-C; it is Backspace.
Changing this requires changing and recompiling the terminal driver, which is beyond the scope of this README.

### Uploading and Building the Forth Interpreter

Activate the virtualenv you created in the last step.
For *username* here, substitute the unprivileged user you created above.
For *password*, substitute whatever you set its password to.

```
v7 -h localhost -p 1145 -u username
password
cmd mkdir v7forth
put c/forth.c v7forth/forth.c
put c/bedit.c v7forth/bedit.c
put c/Makefile v7forth/Makefile
```

Log in to the v7 system with `telnet` or `telnetlib3-client`.

```
mkdir bin
cat >> .profile
PATH=$PATH:$HOME/bin
export PATH
^D
cd v7forth
make forth
make bedit
cd ../bin
ln ../v7forth/forth forth
ln ../v7forth/bedit bedit
```

Now you have both `forth` and `bedit` on your path.

Try them out:

```
$ forth
forth v7 -- ok
: sqr dup * ;
 ok
27 sqr .
729  ok
^D
$
```

Run `bedit`.
Type `i` to get into insert mode.
Type `: sqr dup * ;` followed by `Enter`
Type `27 sqr.`
Type `^W` to write out the block.
Type `^E` to execute the block.
You should see:

```
--- exec block 0 ---
forth v7 -- ok
 ok
729  ok
 ok
 ok
 ok
 ok
 ok
 ok
 ok
 ok
 ok
 ok
 ok
 ok
 ok
 ok

-- press any key --
```

(because a block is always 1024 characters, 16 lines of 64 characters, it acts as if you typed `Enter` on any blank lines).

Type `^X` to exit.

Run `bedit` again and it will load your `blocks` file.  You can `bedit ` *filename* and name the blocks file anything you want.
The file can be a maximum of 32 1024-character blocks, and will always be a multiple of 1024 characters, since that is the size of the Forth block.

## Forth Interpreter

I offered surprisingly (to me) minimal guidance to Claude: basically not much more setting up the v7 system and telling it, "here's how you log in; it's a standard Unix v7 system with C as described in First Edition K&R, and I want a threaded Forth interpreter written in that C that compiles, runs, and passes its tests on the emulated system."

That got a working interpreter; after that I asked Claude to make it compliant with the [Forth-77](https://www.complang.tuwien.ac.at/forth/forth-77.txt) and [Forth-79](https://6502.org/documents/books/forth_interest_group/forth_79_standard.pdf) standards (with a switch between the two, and lenient about what it accepted).
What we have now is [forth.c](./c/forth.c), an interpreter (maybe; I haven't actually verified more than it behaves like I think a Forth interpreter should) compliant with both standards, including block storage.

It's neither brilliant nor elegant, but it appears functional; forth.c is now 48362 bytes, compiling (with -O, stripped) to 15394 bytes.
That's large in v7 terms:

| Program | Size (bytes) |
|---------|--------------|
| as      | 5604         |
| cc      | 6464         |
| ar      | 9844         |
| ld      | 10750        |
| ed      | 11074        |
| f77     | 11340        |
| sed     | 12876        |
| *forth* | *15394*      |
| sh      | 17310        |
| adb     | 34652        |
| troff   | 41666        |
| awk     | 46126        |

It interprets Forth, has an interactive interface, and you can save your work via the `BLOCK`, `UPDATE`, and `FLUSH` words.
You can load your work back with the `LOAD` word.

It is, however, more pleasant to work with the block screen editor `bedit`.

## Block screen editor

This was even less supervised.
Once Claude had written a Forth interpreter with block support, I pointed it at [my fork of "s" modified for v7](https://github.com/athornton/s/tree/v7).
I asked it for a screen editor that worked on a single block at a time.
The block is 64x16, which is what is stated in [Leo Brodie's _Starting Forth, 1st. Edition_](https://www.forth.com/wp-content/uploads/2018/01/Starting-FORTH.pdf), and what is suggested "by convention", though not mandated, in the [Forth-79 Standard](https://6502.org/documents/books/forth_interest_group/forth_79_standard.pdf).
I asked Claude to use `s` as a model for implementation, and to assume VT100 control codes and an 80x24 screen (`stty` under v7 has no idea how big the screen is, and `curses`, although extant by 1978, is not present in stock v7).

[Bedit](c/bedit.c) is not a great editor, and screen redraws are slow and inefficient, but nevertheless it beats the pants off `ed`.
Well, *I* think so, anyway.
Brian Kernighan probably disagrees.

## v7 file transfer tool

Along the way, Claude realized it needed a way to transfer files into and out of the v7 system.
It came up with an early version of [v7](./v7.py).
Initially this was set for 300 bps (the speed you get from the emulated DC11 and the console in simh), but after I got the DZ11 up and running, it was changed to work at 9600bps.

I realized that `v7` would be much more helpful to me if I put an interactive mode into it, so I did.

I also added ubiquitous type hinting and made it happy with the `ruff` rules we use at work.

This program was much more of a collaboration between myself and Claude.
The core of the character-by-character delayed upload is Claude's; all of the interactive features and working retry logic are mine.

Yes, I'm aware it still needs a test suite.
Maybe I'll get there.
I have an idea for mocking a v7 machine using the server side of `telnetlib3` but that's going to be a bunch of work, and given how much Claude costs to use (see below) I don't necessarily want it to construct that for me.

It is my hope that fellow retrocomputing enthusiasts will find [v7](src/v7/v7.py) to be useful for getting files into and out of emulators when all you have is a terminal or console connection.

Of course, that's not the only way to get files in and out of v7 Unix.
On my emulated system at home I've implemented [uucp](https://en.wikipedia.org/wiki/UUCP) for period-correct pre-TCP/IP file transfer over an 8-bit-clean DZ-11 (this is probably the best solution, but it is far more complex than what is presented here).
The `uucp` implementation doesn't offer a lot of feedback, so debugging dropped connections is actually much harder than with the `v7` tool.

If you want to edit in-situ on the v7 system, there's `ed` (ugh, but it [is the standard editor](https://www.gnu.org/fun/jokes/ed-msg.html) or you can add `s` [re-modified to work on v7](https://github.com/athornton/s/tree/v7) and get something kind of like vi.
Finally, some terminal emulators, including the one I use, [iTerm2](https://iterm2.com), have a "slow paste" mode so you can just start catting to a file, slow-paste the contents, and then hit Control-D.

## Notes

Pair-programming with Claude was an interesting experience.
Its threaded interpreter's inner `execute()` loop is a very-boring-to-read, but also quite-straightforward, switch statement to implement the equivalent of a dispatch table (the usual approach in period interpreters).
My guess is that Claude did not use indirection through a dispatch table because it had, by digesting millions of lines of post-Dijkstra source code, mostly internalized the maxim ["Go To Statement Considered Harmful"](https://homepages.cwi.nl/~storm/teaching/reader/Dijkstra68.pdf) (although not quite! You will find all of `goto`, `setjmp()` and `longjmp()` in the interpreter).

### Forth design choices

Contemporary Forth interpreters for PDP-11 and similar machines are generally written in assembly; I have never found an extant C Forth interpreter that would work on v7 (certainly not without extensive modification).
There are a few single-file Forths that might be coerced to work; I never got any of them working on v7.
They generally had one function per primitive, and that either meant that, if I I put them all in one file, I overflowed a symbol table and `pcc` barfed and died, or if I tried to split the file into one C source file per function/primitive, the linker couldn't handle that many files.

Claude avoided both these traps by putting a very large, extremely tedious to read, switch statement into the code to handle its primitives.  It's all inside `execute()`, so with a single (grotesque) function in a single file, there was nothing for the compiler to complain about.

Other Forth sources I've looked at typically have fewer machine-native primitive words and build up more of their core vocabularly in Forth statements built atop the smaller set of primitives.

### Notes on the generated code

I didn't edit the Claude-generated C.
It's stylistically kind of interesting.

The `bedit` editor is quite strange; it's done as a single file, often has several statements on a line, and imbeds its test harnesses, each with their own `main()` functions, hidden behind `#ifdef`s.
That's despite the fact that `s`, its supposed model, is more traditionally structured, with several `.c` and `.h` files.

The Forth interpreter is more like what I expected: straightforward, overcommented, and repetitive.
This is exactly the sort of work Claude is good at and at which humans tend to make copy-pasting errors when defining a zillion very-similar-but-not-identical things.

Another thing I found interesting: Claude was pretty bad at finding the relevant sections of standards documents; I was often much faster at the job.
In several instances it was easier for me to tell Claude "the detail about this word is on page XX; do what that says," rather than letting it hunt through the document itself.

### Cost/benefit

Given how fast Claude burned through tokens, I'm pretty sure it was costing a higher hourly rate than a junior developer, and the code it produced was at best junior-developer-level quality.
However, I'm not going to find any junior developers who know how to write 1979-era K&R C at all fluently, and it was much better at taking existing patterns (e.g. "Here's how to write a simple screen editor with raw VT100 control codes") and applying them than a human junior developer would have been.

Along the way, Claude went down some weird rabbit holes, like deciding that overrunning the telnet buffer on v7 had something to do with tab expansion (it didn't), or its frankly insane attempt at reconnection after the telnet session dropped.
I spent quite some time staring at that and trying to understand it before deciding it was fundamentally broken and reimplementing it in a straightforward and much easier way.
(The trick was to create the sentinel out of two separate short echo statements, so that the string you were looking for was not in the command you just sent.)

## Conclusions

Do you want to let Claude write software you care about unsupervised?

*No.*

Do you want to let it write code you put in production without very careful human review, indeed so careful that it would have been less effort and more fun to write it yourself?

**Hell, no.**

Do you want it to do one-off jigs and scaffolds to build something you want done?
*Probably.*

Did it take less effort to have Claude write the interpreter and editor, and guide it through the process, than it would have for me to do the same?

*Probably.*

Would I have lost interest partway through one or both of those tasks and put the project aside never to be completed?

*Almost certainly* -- that's exactly what I've done with my previous attempts to bring a Forth written in C to v7 Unix.
(See, humans can use em-dashes too...or did Claude write this README?
The world may never know.)

Do I now have a cool tool that I wanted, that more-or-less works, that I did not have before?

*Yes.*
