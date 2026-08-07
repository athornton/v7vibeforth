( Defining and compiling words: : ; CREATE VARIABLE CONSTANT DOES> )
( ALLOT , IMMEDIATE COMPILE [COMPILE] LITERAL ' EXECUTE FIND FORGET )
( HERE STATE and the ?  display word. )
42 CONSTANT C42
C42 42 ?=
VARIABLE VX
9 VX ! VX @ 9 ?=
( CREATE makes a word that returns its own data address )
CREATE CA 5 , 6 ,
CA @ 5 ?=
CA 2 + @ 6 ?=
( a colon definition of a colon definition )
: sq DUP * ;
: quad sq sq ;
3 quad 81 ?=
( CREATE ... DOES> builds a defining word )
: doubler CREATE , DOES> @ 2 * ;
21 doubler d42
d42 42 ?=
7 doubler d14
d14 14 ?=
( ' gives the compilation address, EXECUTE runs it )
1 2 ' + EXECUTE 3 ?=
( ' of a colon word works too )
3 ' sq EXECUTE 9 ?=
( FIND parses the NEXT word and pushes its cfa [0 if absent] )
FIND DUP 0= 0 ?=
FIND NOSUCHWORDATALL 0 ?=
( ' and FIND agree for the same word )
' DUP FIND DUP ?=
( [COMPILE] forces an immediate word to be compiled instead of run )
: pc [COMPILE] DUP ;
5 pc 5 ?= 5 ?=
( STATE is 0 while interpreting )
STATE @ 0 ?=
( HERE advances as the dictionary grows )
HERE VARIABLE VY HERE SWAP - 0> 1 ?=
( FORGET removes a definition and rewinds HERE )
HERE
: gone1 1 ;
: gone2 2 ;
FORGET gone1
HERE ?=
( the forgotten word is really gone )
FIND gone1 0 ?=
( but earlier words survive )
3 quad 81 ?=
REPORT
