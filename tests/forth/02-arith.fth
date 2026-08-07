( Arithmetic: + - * / MOD /MOD */ */MOD 1+ 1- 2+ 2- NEGATE MINUS ABS )
( MIN MAX and the unsigned family U* U/ U/MOD U. )
2 3 + 5 ?=
9 4 - 5 ?=
6 7 * 42 ?=
17 5 / 3 ?=
17 5 MOD 2 ?=
17 5 /MOD 3 ?= 2 ?=
( */ and */MOD keep the intermediate product in a double, so this does )
( not overflow a 16-bit cell the way 300*400 alone would. )
300 400 600 */ 200 ?=
7 3 2 */ 10 ?=
17 5 3 */MOD 28 ?= 1 ?=
5 1+ 6 ?=
5 1- 4 ?=
5 2+ 7 ?=
5 2- 3 ?=
5 NEGATE -5 ?=
-5 NEGATE 5 ?=
5 MINUS -5 ?=
-7 ABS 7 ?=
7 ABS 7 ?=
3 9 MIN 3 ?=
3 9 MAX 9 ?=
-3 9 MIN -3 ?=
( 16-bit wraparound is expected on a PDP-11 cell )
32767 1+ -32768 ?=
( U* leaves a double: lo hi )
7 3 U* 0 ?= 21 ?=
( U/ and U/MOD take a DOUBLE dividend [ ud u -- rem quot ] )
17 0 5 U/ 3 ?= 2 ?=
17 0 5 U/MOD 3 ?= 2 ?=
( division by zero must not trap: the interpreter pushes 0 0 )
17 0 0 U/ 0 ?= 0 ?=
REPORT
