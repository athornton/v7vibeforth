( Words that do not fit the other groups: 79-STANDARD >IN CONVERT )
( WORDS ." --> ;S and the ABORT/QUIT/BYE family. )
( --- 79-STANDARD is a no-op that simply must exist [Forth-79 requires it )
( so a program can assert which standard it needs] --- )
VARIABLE DM
DEPTH DM ! 79-STANDARD DEPTH DM @ ?=
( --- comments --- )
( Forth-78 and -79 have only the parenthesised comment. )
( ( is a WORD, so it must be followed by a space. )
( The comment ends at the first ] on the same line: this )
( interpreter does not let a comment span lines. )
2 ( inline comment ) 3 + 5 ?=
( --- ." prints, and must work compiled --- )
: dq ." [dq-ok]" ;
DEPTH DM ! dq CR DEPTH DM @ ?=
( This interpreter ALSO accepts the Forth-83/ANS backslash comment, and )
( that word has to be exercised somewhere.  Check it is defined rather )
( than using it, so these files stay period-correct Forth throughout. )
FIND \ 0= 0 ?=
( --- >IN is the parse offset into the input line; it advances as we read )
( tokens, so it is nonzero part-way through a line --- )
>IN @ 0> 1 ?=
( --- CONVERT accumulates digits onto a running value.  Its address )
( argument points at text-1: the first digit is at addr+1 [Forth-79]. --- )
PAD 49 SWAP C!
PAD 1+ 50 SWAP C!
PAD 2 + 32 SWAP C!
( CONVERT leaves [ n' addr' ]: the value is BELOW the updated address. )
0 PAD 1- CONVERT DROP 12 ?=
( CONVERT respects BASE.  Write the digit bytes while still in DECIMAL: )
( inside HEX the literal 102 would mean 0x102 = 258, and C! would store )
( its low byte [2] instead of the character 'f'. )
PAD 102 SWAP C!
PAD 1+ 102 SWAP C!
PAD 2 + 32 SWAP C!
HEX
0 PAD 1- CONVERT DROP
DECIMAL
255 ?=
( --- WORDS lists the vocabulary; just prove it runs and is stack-neutral )
DEPTH DM ! WORDS CR DEPTH DM @ ?=
( --- ;S ends interpretation of a block; --> chains to the next block. )
( Both are exercised by the block tests via LOAD, so here we only check )
( that they exist as words rather than re-testing LOAD. )
FIND ;S 0= 0 ?=
FIND --> 0= 0 ?=
( --- ABORT and QUIT reset the interpreter, and BYE exits it, so running )
( them here would end the test run.  Confirm they are defined instead. )
FIND ABORT 0= 0 ?=
FIND QUIT 0= 0 ?=
FIND BYE 0= 0 ?=
FIND EXPECT 0= 0 ?=
FIND QUERY 0= 0 ?=
FIND WORD 0= 0 ?=
( --- KEY reads one character with getchar, i.e. from the SAME buffered )
( stream the interpreter is parsing.  When input is a file that makes the )
( exact character it returns depend on how much has been buffered, and at )
( end of file it returns EOF, which is -1.  So assert only what is stable: KEY )
( returns *something* [a character or EOF] and does not hang or corrupt )
( the stack.  It is exercised interactively for real. )
REPORT
( KEY must be the FINAL token in the file: it reads with getchar from )
( the same buffered stream the interpreter is parsing, so it swallows the )
( next character of the source -- putting anything after it [even the )
( word REPORT] loses that character and produces a spurious "EPORT ?". )
( Nothing is asserted here beyond "it runs and returns a cell"; KEY is )
( genuinely exercised interactively. )
KEY DROP
